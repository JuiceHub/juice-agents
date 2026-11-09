"""Session replacement uses one durable namespace across both gateways.

The tests run real Runners with a deterministic model. This checks stored
context and recovery rather than merely asserting which constructor was called.
Every workspace and artifact belongs to pytest's cleaned temporary directory.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from adapters.stdio_gateway.handlers import RpcHandlers
from adapters.stdio_gateway.runtime import DirectRunnerRuntime
from adapters.stdio_gateway.sessions import list_sessions
from adapters.web_gateway import app as web_app
from juice_agents import Runner
from juice_agents.core.managers.agents import AgentManager
from juice_agents.core.memory.store import MemoryStore
from juice_agents.core.registry.agents import registry as registry_module
from juice_agents.core.runner.persistence.store import build_layout


class _Model:
    """Complete each turn locally while retaining its actual Agent context."""

    def generate(self, _messages: Any, **_kwargs: Any) -> dict[str, str]:
        return {
            "content": '<thought>done</thought><actions>'
            '[{"name":"submit_output","args":{"output":"finished"}}]</actions>',
        }


@pytest.fixture(params=["stdio", "web"])
def gateway(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: _Model())
    if request.param == "stdio":
        connection = RpcHandlers()
        dispatch = connection.dispatch
        current = lambda: connection._runtime
    else:
        connection = web_app.WebSocketRuntimeConnection(websocket=MagicMock())
        dispatch = connection._handle_sync_command
        current = lambda: connection.runtime
    try:
        yield dispatch, current
    finally:
        if current() is not None:
            current().stop_session()


def test_replacement_starts_empty_and_old_session_remains_discoverable_and_resumable(
    tmp_path: Path, gateway,
) -> None:
    dispatch, current = gateway
    params = {"base_dir": str(tmp_path)}
    original = dispatch("start_session", params)
    old_id = original["runner_id"]
    current().stream_message("remember this old conversation")
    old_layout = build_layout(tmp_path, old_id)
    old_context = (old_layout.agents_dir / "root" / "session.json").read_text()

    replacement = dispatch("start_session", params)
    released_context = (old_layout.agents_dir / "root" / "session.json").read_text()
    previous_payload, released_payload = json.loads(old_context), json.loads(released_context)
    assert released_payload.pop("agent_status") == "released"
    previous_payload.pop("agent_status")
    assert released_payload == previous_payload
    old_context = released_context
    new_id = replacement["runner_id"]
    assert new_id != old_id
    assert current()._runner.layout.root_dir == tmp_path / ".juice" / "sessions" / new_id
    assert not current()._runner.agent_manager.live_agents
    assert not list(current()._runner.layout.agents_dir.glob("*/session.json"))
    assert not (tmp_path / ".juice" / "runners").exists()
    assert {summary.runner_id for summary in list_sessions(tmp_path)} == {old_id, new_id}

    # A normal message after clear must populate only the new session.
    current().stream_message("a new conversation")
    new_context = (build_layout(tmp_path, new_id).agents_dir / "root" / "session.json").read_text()
    assert "a new conversation" in new_context
    assert "remember this old conversation" not in new_context
    assert (old_layout.agents_dir / "root" / "session.json").read_text() == old_context

    resumed = dispatch("resume_session", {**params, "runner_id": old_id})
    assert resumed["runner_id"] == old_id and resumed["resumed"]
    assert (current()._runner.layout.agents_dir / "root" / "session.json").read_text() == old_context


def test_resume_restores_ordered_history_without_acquiring_agents_or_rewriting_state(
    tmp_path: Path, gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both gateways restore history, and inspection also works without live Agents."""

    dispatch, current = gateway
    params = {"base_dir": str(tmp_path)}
    runner_id = dispatch("start_session", params)["runner_id"]
    prompts = ["first historical request", "second historical request"]
    for prompt in prompts:
        current().stream_message(prompt)

    # Populate a different session before resuming, so the history response
    # must restore the requested Runner rather than retain the current view.
    replacement_id = dispatch("start_session", params)["runner_id"]
    assert replacement_id != runner_id
    current().stream_message("unrelated replacement conversation")

    # Exercise discovery's fallback against a real ContextTree session.json,
    # rather than satisfying the picker entirely from its cached manifest title.
    manifest_path = build_layout(tmp_path, runner_id).manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("first_user_request_preview", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    summary = next(
        item for item in dispatch("list_sessions", params) if item["runner_id"] == runner_id
    )
    assert summary["first_user_request_preview"] == prompts[0]

    resumed = dispatch("resume_session", {**params, "runner_id": runner_id})
    assert resumed["runner_id"] == runner_id and resumed["resumed"]

    def persisted_files() -> dict[Path, tuple[bytes, int]]:
        # Compare content and modification times: rewriting identical bytes is
        # still an unwanted side effect of loading the conversation for display.
        return {
            path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in tmp_path.rglob("*") if path.is_file()
        }

    def inspect_history(
        read_report: Callable[[], dict[str, Any]], manager: AgentManager,
    ) -> dict[str, Any]:
        before = persisted_files()
        existing_agents = manager.live_agents
        with monkeypatch.context() as scoped:
            def unexpected_instantiation(*_args: Any, **_kwargs: Any) -> None:
                pytest.fail("Inspecting resumed history must not instantiate an Agent")

            scoped.setattr(registry_module.AgentRegistry, "instantiate", unexpected_instantiation)
            report = read_report()
            assert read_report() == report

        assert manager.live_agents == existing_agents
        assert persisted_files() == before
        return report

    # Gateways leave resumed scheduling dormant until initial history is loaded.
    # Reading history must not acquire Agents or rewrite persisted files.
    report = inspect_history(
        lambda: dispatch("describe_agent_sessions", {}), current()._runner.agent_manager,
    )
    assert report["runner_id"] == runner_id
    root = next(agent for agent in report["agents"] if agent["is_root"])
    # Preserve user/answer interleaving across rounds; nonempty files alone do
    # not prove that the frontend receives a usable transcript after /resume.
    assert [
        (step["type"], step["task"] if step["type"] == "task" else step["output"])
        for step in root["steps"] if step["type"] in {"task", "action"}
    ] == [
        ("task", prompts[0]), ("action", "finished"),
        ("task", prompts[1]), ("action", "finished"),
    ]
    assert all(
        step["round_outcome"] == "submitted"
        for step in root["steps"] if step["type"] == "action"
    )
    results = [step["result"] for step in root["steps"] if step["type"] == "round_result"]
    assert len(results) == 2
    assert all(result["status"] == "completed" and result["output"] == "finished" for result in results)
    assert len({result["round_id"] for result in results}) == 2
    assert root["agent_role"] == "root" and root["agent_kind"] == "persistent"

    # SDK recovery remains lazy until activate() is called. Inspect the same
    # durable history in that cold state to cover the persisted-snapshot path.
    current().stop_session()
    cold = Runner.resume(base_dir=tmp_path, runner_id=runner_id)
    try:
        assert not cold.agent_manager.live_agents
        cold_report = inspect_history(cold.describe_agent_sessions, cold.agent_manager)
        cold_root = next(agent for agent in cold_report["agents"] if agent["is_root"])
        assert cold_root["steps"] == root["steps"]
        assert not cold.agent_manager.live_agents
    finally:
        cold.close()


def test_resume_switches_root_children_tasks_and_permissions_as_one_runner(tmp_path: Path, gateway) -> None:
    """Equal child/task names in different sessions must never share context."""

    dispatch, current = gateway
    records: list[tuple[str, str, str]] = []
    previous_child_instances: list[Any] = []
    for label, permission in [("saved", "accept"), ("other", "default")]:
        status = dispatch("start_session", {"base_dir": str(tmp_path), "permission_mode": permission})
        runtime = current()
        runner = runtime._runner
        runtime.stream_message(f"{label} root conversation")
        child = runner.agent_manager.acquire("general", lifecycle="persistent", role="worker")
        previous_child_instances.append(child.instance)
        runner.agent_manager.run(child, f"{label} child conversation")
        task = runner.async_task_manager.launch(
            task_type="local_agent", owner_agent_id=child.agent_id, owner_agent_name=child.agent_name,
            description=f"{label} task", runner=lambda _task, _dir, value=label: {"value": value},
            summary_builder=lambda result: result["value"],
        )
        runner.async_task_manager.wait_active_async_tasks(timeout_seconds=3)
        assert runner.async_task_manager.get_task(task["async_task_id"])["status"] == "completed"
        records.append((status["runner_id"], child.agent_id, task["async_task_id"]))

    saved_id, child_id, task_id = records[0]
    previous = current()._runner
    resumed = dispatch("resume_session", {"base_dir": str(tmp_path), "runner_id": saved_id})
    restored = current()._runner
    assert restored is not previous and previous._closed
    assert resumed["runner_id"] == saved_id and resumed["permission_mode"] == "accept"
    report = dispatch("describe_agent_sessions", {})
    assert report["runner_id"] == saved_id
    root = next(agent for agent in report["agents"] if agent["is_root"])
    child = next(agent for agent in report["agents"] if agent["agent_id"] == child_id)
    assert any(step.get("task") == "saved root conversation" for step in root["steps"])
    assert any(step.get("task") == "saved child conversation" for step in child["steps"])
    assert "other root conversation" not in json.dumps(root["steps"])
    assert "other child conversation" not in json.dumps(child["steps"])
    tasks = current().list_async_tasks()
    assert [(task["async_task_id"], task["description"]) for task in tasks] == [(task_id, "saved task")]
    assert restored.async_task_manager.read_output(task_id)["latest_result"] == {"value": "saved"}
    assert not restored.agent_manager.live_agents
    dispatch("activate_session", {"runner_id": saved_id})
    assert restored.agent_manager.get(child_id).instance not in previous_child_instances
    assert restored.layout.root_dir == build_layout(tmp_path, saved_id).root_dir


def test_replacement_preserves_launch_strategy_reward_and_catalog_choices(
    tmp_path: Path, gateway,
) -> None:
    dispatch, current = gateway
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        "providers:\n  local:\n    backend: openai\n    api_base: https://example.test/v1\n"
        "    api_key_env: SESSION_STORAGE_TEST_KEY\nmodels:\n  doubao_lite:\n"
        "    provider: local\n    provider_model_name: local-provider\n",
        encoding="utf-8",
    )
    params = {
        "base_dir": str(tmp_path), "permission_mode": "accept", "agent_mode": "plan",
        "agent_type": "codeact", "model_name": "doubao_lite", "model_effort": "high",
        "runtime_config_path": str(catalog),
    }
    original = dispatch("start_session", params)
    previous = current()
    previous.switch_strategy({"breadth": 3})
    previous.configure_reward_model({"model_name": "doubao_lite"})
    replacement = dispatch("start_session", {"base_dir": str(tmp_path)})
    for name in ("permission_mode", "agent_mode", "agent_type", "model_name", "model_effort"):
        assert replacement[name] == original[name] == params[name]
    assert current().runtime_config_path == catalog
    assert current().describe_reward_model() == {
        "model_name": "doubao_lite", "scorer_configured": True,
        "breadth": 3, "execution": "candidate_selection",
    }
    assert current()._reward_model is not previous._reward_model
    assert not (tmp_path / ".juice" / "config.yaml").exists()

    explicit = dispatch("start_session", {
        "base_dir": str(tmp_path), "permission_mode": "default", "agent_mode": "agent",
        "agent_type": "react", "model_effort": "disabled",
    })
    assert explicit["permission_mode"] == "default"
    assert explicit["agent_mode"] == "agent" and explicit["agent_type"] == "react"
    assert explicit["model_effort"] == "disabled"


def test_replacement_does_not_transfer_configuration_to_another_workspace(
    tmp_path: Path, gateway,
) -> None:
    dispatch, current = gateway
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    dispatch("start_session", {
        "base_dir": str(first), "permission_mode": "accept", "agent_mode": "plan",
        "agent_type": "codeact", "model_effort": "high",
    })
    current().switch_strategy({"breadth": 4})
    current().configure_reward_model({"model_name": "doubao_lite"})
    status = dispatch("start_session", {"base_dir": str(second)})
    assert status["permission_mode"] == "default" and status["agent_mode"] == "agent"
    assert status["agent_type"] == "react" and status["model_effort"] == "disabled"
    assert current().pending_strategy_configuration is None
    assert current().pending_reward_model_configuration is None


def test_failed_create_exposes_no_closed_session_identity(tmp_path: Path, gateway, monkeypatch) -> None:
    dispatch, current = gateway
    params = {"base_dir": str(tmp_path)}
    old_id = dispatch("start_session", params)["runner_id"]
    previous = current()

    def fail_create(_runtime):
        raise OSError("session storage unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(DirectRunnerRuntime, "_create_declarative_runner", fail_create)
        with pytest.raises(OSError, match="session storage unavailable"):
            dispatch("start_session", params)
    status = dispatch("describe_session", {})
    assert status["runner_id"] == "" and not status["started"]
    assert previous._runner is None
    assert old_id in {item.runner_id for item in list_sessions(tmp_path)}
    # Recovery creates an empty destination and cannot reuse the closed Runner.
    assert dispatch("start_session", params)["runner_id"] != old_id


def test_invalid_replacement_configuration_preserves_the_live_session(tmp_path: Path, gateway) -> None:
    dispatch, current = gateway
    old_id = dispatch("start_session", {"base_dir": str(tmp_path)})["runner_id"]
    previous = current()
    with pytest.raises(ValueError, match="Workspace is missing"):
        dispatch("start_session", {"base_dir": str(tmp_path / "missing")})
    assert current() is previous
    assert dispatch("describe_session", {})["runner_id"] == old_id
    assert not previous._runner._closed


def test_legacy_namespace_is_ignored_by_discovery_resume_and_memory(tmp_path: Path) -> None:
    legacy = tmp_path / ".juice" / "runners" / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "manifest.json").write_text(json.dumps({"runner_id": "legacy"}))
    snapshot = legacy / "agents" / "root" / "session.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("{}\n", encoding="utf-8")
    assert list_sessions(tmp_path) == []
    with pytest.raises(FileNotFoundError):
        Runner.resume(base_dir=tmp_path, runner_id="legacy")
    store = MemoryStore.for_workspace(tmp_path)
    assert store.count_runner_sessions_touched_since(0) == 0
    session = build_layout(tmp_path, "current").agents_dir / "root" / "session.json"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")
    assert store.count_runner_sessions_touched_since(0) == 1
    assert store.count_runner_sessions_touched_since(0, exclude_runner_id="current") == 0


def test_web_archiving_and_browser_artifacts_share_session_namespace(tmp_path: Path, monkeypatch) -> None:
    runner = Runner.create(base_dir=tmp_path, runner_config="agent")
    runner_id = runner.runner_id
    runner.close()
    layout = build_layout(tmp_path, runner_id)
    web_app._archive_web_runner(tmp_path, runner_id)
    assert web_app._list_web_sessions(tmp_path) == []
    assert [item.runner_id for item in web_app._list_archived_web_sessions(tmp_path)] == [runner_id]
    assert [item.runner_id for item in list_sessions(tmp_path)] == [runner_id]
    web_app._restore_web_runner(tmp_path, runner_id)
    assert [item.runner_id for item in web_app._list_web_sessions(tmp_path)] == [runner_id]

    image = layout.browser_screenshots_dir / "screen.png"
    image.write_bytes(b"png")
    assert web_app._latest_browser_image(tmp_path, runner_id=runner_id) == image
    assert layout.observation_images_dir == layout.root_dir / "observation_images"
    monkeypatch.setattr(web_app, "get_existing_browser_session", lambda _key: None)
    browser_factory = MagicMock()
    monkeypatch.setattr(web_app, "get_browser_session", browser_factory)
    web_app._browser_live_session(tmp_path, runner_id)
    assert browser_factory.call_args.kwargs["state_dir"] == layout.root_dir / "browser_state"
    assert browser_factory.call_args.kwargs["recording_dir"] == layout.root_dir / "browser_recordings"


@pytest.mark.parametrize("snapshot_kind", ["runner", "agent"])
def test_resume_rejects_old_snapshot_versions_without_migration(tmp_path: Path, gateway, snapshot_kind: str) -> None:
    dispatch, current = gateway
    session = dispatch("start_session", {"base_dir": str(tmp_path)})
    current().stream_message("save a current conversation")
    runner_id = session["runner_id"]
    current().stop_session()
    layout = build_layout(tmp_path, runner_id)
    manifest = layout.manifest_path if snapshot_kind == "runner" else layout.agents_dir / "root" / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["schema_version"] -= 1
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    before = {str(path.relative_to(layout.root_dir)): path.read_bytes() for path in layout.root_dir.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="不支持|unsupported"):
        dispatch("resume_session", {"base_dir": str(tmp_path), "runner_id": runner_id})
    assert {str(path.relative_to(layout.root_dir)): path.read_bytes() for path in layout.root_dir.rglob("*") if path.is_file()} == before
