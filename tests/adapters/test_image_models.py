"""Image catalog RPCs share validation while retaining independent chat state."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from adapters.stdio_gateway.handlers import RpcHandlers
from adapters.web_gateway.app import WebSocketRuntimeConnection
from juice_agents.core.config.runtime_config import read_workspace_config
from juice_agents.core.registry.agents import registry as registry_module
from juice_agents.core.runner import RunnerBusyError


@pytest.fixture(params=["stdio", "web"])
def gateway(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exercise both transports against real catalog parsing and Runner gates."""

    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("IMAGE_RPC_TEST_KEY", raising=False)
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(yaml.safe_dump({
        "providers": {"ark": {
            "backend": "doubao", "api_base": "https://ark.example.test/api/v3",
            "api_key_env": "IMAGE_RPC_TEST_KEY",
        }},
        "models": {
            "chat": {"provider": "ark", "provider_model_name": "chat-deployment"},
            "judge": {"provider": "ark", "provider_model_name": "judge-deployment"},
        },
        "image_models": {
            "seedream_4": {"provider": "ark", "provider_model_name": "seedream-4"},
            "seedream_alt": {"provider": "ark", "provider_model_name": "seedream-alt"},
        },
        "image": {"enabled": False, "model_name": "seedream_4"},
        "runtime": {"model_name": "chat"},
    }), encoding="utf-8")
    params = {"base_dir": str(workspace), "runtime_config_path": str(catalog)}
    if request.param == "stdio":
        connection = RpcHandlers()
        dispatch = connection.dispatch
        current = lambda: connection._runtime
    else:
        connection = WebSocketRuntimeConnection(websocket=MagicMock())
        dispatch = connection._handle_sync_command
        current = lambda: connection.runtime
    try:
        yield dispatch, current, params, workspace
    finally:
        runtime = current()
        if runtime is not None:
            runtime._close_runner(reason="test_finished")


def test_image_catalog_is_cold_read_only_and_separate(gateway) -> None:
    dispatch, current, params, workspace = gateway
    listed = dispatch("list_image_models", params)
    assert [item["model_name"] for item in listed["image_models"]] == ["seedream_4", "seedream_alt"]
    assert listed["current_image_model_name"] == "seedream_4"
    assert listed["image_enabled"] is False
    assert {item["model_name"] for item in dispatch("list_models", params)} == {"chat", "judge"}
    assert "api_key" not in listed["image_models"][0]
    assert current()._runner is None
    assert list(workspace.iterdir()) == []


def test_first_image_selection_does_not_pollute_chat_or_enable_generation(gateway) -> None:
    dispatch, current, params, workspace = gateway
    changed = dispatch("set_image_config", {**params, "model_name": "seedream_alt"})
    assert changed == {"enabled": False, "image_model_name": "seedream_alt", "runner_id": ""}
    assert current().model_name == "chat"
    assert current()._runner is None
    assert read_workspace_config(workspace) == {"image": {"model_name": "seedream_alt"}}
    assert dispatch("list_image_models", params)["current_image_model_name"] == "seedream_alt"


def test_empty_image_patch_has_no_writes(gateway) -> None:
    dispatch, current, params, workspace = gateway
    result = dispatch("set_image_config", params)
    assert result["enabled"] is False
    assert result["image_model_name"] == "seedream_4"
    assert current()._runner is None and list(workspace.iterdir()) == []


@pytest.mark.parametrize("patch", [
    {"model_name": "missing"}, {"model_name": "chat"}, {"model_name": ""},
    {"model_name": True}, {"enabled": "false"}, {"enabled": 1}, {"enabled": True},
])
def test_invalid_image_configuration_never_reaches_disk(gateway, patch) -> None:
    dispatch, current, params, workspace = gateway
    with pytest.raises(ValueError):
        dispatch("set_image_config", {**params, **patch})
    assert current().model_name == "chat"
    assert read_workspace_config(workspace) == {}
    assert list(workspace.iterdir()) == []


def test_image_updates_are_sparse_and_validate_credentials_before_enabling(
    gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch, current, params, workspace = gateway
    monkeypatch.setenv("IMAGE_RPC_TEST_KEY", "offline-test-key")
    enabled = dispatch("set_image_config", {**params, "enabled": True})
    assert enabled["enabled"] is True
    assert read_workspace_config(workspace) == {"image": {"enabled": True}}
    selected = dispatch("set_image_config", {**params, "model_name": "seedream_alt"})
    assert selected["enabled"] is True
    assert read_workspace_config(workspace) == {"image": {"enabled": True, "model_name": "seedream_alt"}}
    disabled = dispatch("set_image_config", {**params, "enabled": False})
    assert disabled["image_model_name"] == "seedream_alt"
    assert current()._runner is None


@pytest.mark.parametrize("busy_kind", ["state", "active_request"])
def test_busy_image_updates_leave_project_configuration_untouched(gateway, busy_kind) -> None:
    dispatch, current, params, workspace = gateway
    dispatch("start_session", params)
    runner = current()._runner
    before = read_workspace_config(workspace)
    if busy_kind == "state":
        runner.state["status"] = "running"
    else:
        runner._active_requests = 1
    try:
        with pytest.raises(RunnerBusyError):
            dispatch("set_image_config", {**params, "model_name": "seedream_alt"})
        assert read_workspace_config(workspace) == before
        assert dispatch("list_image_models", params)["current_image_model_name"] == "seedream_4"
    finally:
        runner.state["status"] = "idle"
        runner._active_requests = 0


def test_image_reload_preserves_runner_history_reward_and_strategy(
    gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch, current, params, workspace = gateway

    class Generator:
        def generate(self, messages, **kwargs):
            actions = [{"name": "submit_output", "args": {"output": "finished"}}]
            return {"content": f"<thought>done</thought><actions>{json.dumps(actions)}</actions>"}

    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **kwargs: Generator())
    dispatch("start_session", params)
    runtime = current()
    runner = runtime._runner
    runtime.configure_reward_model({"model_name": "judge"})
    scorer = runtime._reward_model
    # A selected GRM at breadth=1 is deliberately inactive and still needs its
    # old catalog connection invalidated when the image setting triggers reload.
    old_client = scorer._client = SimpleNamespace(close=AsyncMock())
    runtime.stream_message("remember this history")
    history_path = runner.layout.agents_dir / "root" / "session.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    strategy = runtime._strategy
    changed = dispatch("set_image_config", {**params, "model_name": "seedream_alt"})
    assert runtime._runner is runner and changed["runner_id"] == runner.runner_id
    assert runtime._reward_model is scorer and scorer._client is None
    old_client.close.assert_awaited_once()
    assert runtime._strategy is strategy
    refreshed_history = json.loads(history_path.read_text(encoding="utf-8"))
    # A reload releases the current instance before the next request acquires
    # one with the new configuration. Only its lifecycle state may change;
    # committed steps, round results and model history must remain intact.
    assert history.pop("agent_status") == "idle"
    assert refreshed_history.pop("agent_status") == "released"
    assert refreshed_history == history
    assert runtime.describe_reward_model()["model_name"] == "judge"
    assert runtime.describe_reward_model()["breadth"] == 1
    assert runtime.model_name == "chat"
    assert read_workspace_config(workspace) == {"image": {"model_name": "seedream_alt"}}

    # Candidate-count and GRM selections remain independent after a second
    # refresh. No scoring/network is necessary to verify the pending strategy.
    runtime.switch_strategy({"breadth": 3})
    selected_strategy = runtime._strategy
    dispatch("set_image_config", {**params, "enabled": False})
    assert runtime._strategy is selected_strategy
    assert runtime.describe_reward_model()["breadth"] == 3
    assert runtime._reward_model is scorer
