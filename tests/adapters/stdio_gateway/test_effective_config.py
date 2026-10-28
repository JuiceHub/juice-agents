"""Effective configuration RPCs remain read-only and share backend layering."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adapters.stdio_gateway.handlers import RpcHandlers
from adapters.stdio_gateway.runtime import DirectRunnerRuntime
from adapters.web_gateway.app import create_app
from juice_agents.core.config.runtime_config import read_workspace_config


def test_effective_config_reads_global_and_project_without_copying_defaults(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / ".juice").mkdir(parents=True)
    (home / ".juice" / "config.yaml").write_text(
        "runtime:\n  agent_type: codeact\n  permission_mode: accept\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "project"
    workspace.mkdir()
    handlers = RpcHandlers()
    params = {"base_dir": str(workspace)}
    assert handlers.dispatch("load_workspace_config", params) == {}
    effective = handlers.dispatch("load_effective_config", params)
    assert effective["runtime"]["agent_type"] == "codeact"
    assert effective["runtime"]["permission_mode"] == "accept"
    assert not (workspace / ".juice").exists()
    handlers.dispatch("save_workspace_config", {**params, "config": {"runtime": {"agent_type": "react"}}})
    assert read_workspace_config(workspace) == {"runtime": {"agent_type": "react"}}
    assert handlers.dispatch("load_effective_config", params)["runtime"]["agent_type"] == "react"
    (workspace / ".juice" / "config.yaml").unlink()
    assert handlers.dispatch("load_effective_config", params)["runtime"]["agent_type"] == "codeact"
    client = TestClient(create_app(), base_url="http://localhost")
    response = client.get("/api/effective-config", params=params)
    assert response.status_code == 200
    assert response.json() == effective


def test_launch_choices_do_not_become_project_overrides(tmp_path: Path) -> None:
    runtime = DirectRunnerRuntime(base_dir=tmp_path, permission_mode="accept", agent_type="codeact")
    try:
        status = runtime.start_session()
        assert status.permission_mode == "accept"
        assert runtime.effective_config()["runtime"]["agent_type"] == "codeact"
        assert read_workspace_config(tmp_path) == {}
    finally:
        runtime.stop_session()


@pytest.mark.parametrize("started", [False, True], ids=["cold", "hot"])
@pytest.mark.parametrize("config_source", ["user", "project"])
def test_mode_rpc_preserves_inherited_agent_type_unless_explicitly_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: bool, config_source: str,
) -> None:
    """Mode changes preserve the current protocol and never copy defaults to YAML."""
    home = tmp_path / "home"
    (home / ".juice").mkdir(parents=True)
    (home / ".juice" / "config.yaml").write_text(
        f"runtime:\n  agent_type: {'codeact' if config_source == 'user' else 'react'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "project"
    workspace.mkdir()
    if config_source == "project":
        (workspace / ".juice").mkdir()
        (workspace / ".juice" / "config.yaml").write_text(
            "runtime:\n  agent_type: codeact\n", encoding="utf-8",
        )
    project_config = read_workspace_config(workspace)
    handlers = RpcHandlers()
    params = {"base_dir": str(workspace)}
    runner_id = handlers.dispatch("start_session", params)["runner_id"] if started else ""

    try:
        # Both a cold shell and an existing Runner must inherit codeact. An
        # omitted field changes only the mode, not the Agent's action protocol.
        switched = handlers.dispatch("switch_agent_mode", {**params, "agent_mode": "plan"})
        assert switched["agent_mode"] == "plan"
        assert switched["agent_type"] == "codeact"
        assert switched["runner_id"] == runner_id

        # Explicit changes remain process-local and become the value retained
        # by later omissions, even though the inherited default stays codeact.
        for mode, agent_type, expected_type in [
            ("agent", "react", "react"),
            ("plan", None, "react"),
            ("agent", "codeact", "codeact"),
        ]:
            choice = {"agent_type": agent_type} if agent_type is not None else {}
            switched = handlers.dispatch("switch_agent_mode", {**params, "agent_mode": mode, **choice})
            assert switched["agent_type"] == expected_type
            assert switched["runner_id"] == runner_id
            assert handlers.dispatch("load_effective_config", params)["runtime"]["agent_type"] == expected_type
            assert read_workspace_config(workspace) == project_config
            if started:
                assert handlers._runtime._runner.config_context.read_merged_config()["runtime"]["agent_type"] == expected_type

        if not started and config_source == "user":
            assert not (workspace / ".juice").exists()
    finally:
        handlers.dispatch("stop_session", {})


@pytest.mark.parametrize("method", ["enter_worktree", "exit_worktree", "list_worktrees", "worktree_status"])
def test_reserved_worktree_operations_have_actionable_errors(method: str) -> None:
    with pytest.raises(NotImplementedError, match="git worktree add"):
        RpcHandlers().dispatch(method, {})


def test_model_edit_updates_launch_layer_and_persists_only_changed_field(tmp_path: Path) -> None:
    runtime = DirectRunnerRuntime(base_dir=tmp_path, model_name="doubao_lite", model_effort="disabled")
    try:
        first = runtime.start_session()
        switched = runtime.switch_model("doubao_lite", "high")
        assert switched.runner_id == first.runner_id
        assert read_workspace_config(tmp_path) == {"runtime": {"model_effort": "high"}}
        assert runtime._runner.config_context.read_merged_config()["runtime"]["model_effort"] == "high"
        assert runtime._runner.config_context.read_merged_config()["runtime"]["model_name"] == "doubao_lite"
        runtime._runner.state["status"] = "running"
        with pytest.raises(RuntimeError, match="正忙"):
            runtime.switch_model("gpt4o_mini", "low")
        assert runtime.model_name == "doubao_lite"
        assert read_workspace_config(tmp_path) == {"runtime": {"model_effort": "high"}}
    finally:
        runtime._runner.state["status"] = "idle"
        runtime.stop_session()
