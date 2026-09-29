"""Gateway regressions for the declarative Registry -> Manager -> Runner path."""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters.stdio_gateway.handlers import RpcHandlers, _serialize_session_status
from adapters.stdio_gateway.runtime import DirectRunnerRuntime


@pytest.mark.parametrize(
    ("mode_id", "expected_names"),
    [
        ("agent", {"general", "explore"}),
        ("plan", {"general", "explore"}),
        ("group", {"general_worker", "research_worker", "implement_worker", "review_worker", "evolution_worker"}),
        ("team", {"researcher", "developer"}),
    ],
)
def test_gateway_cold_agent_query_projects_defaults_without_workspace_writes(
    tmp_path: Path,
    mode_id: str,
    expected_names: set[str],
) -> None:
    """`/agents` exposes future defaults without bootstrapping a Runner."""

    runtime = DirectRunnerRuntime(base_dir=tmp_path, agent_mode=mode_id)

    result = runtime.list_available_agents()
    assert result["mode_id"] == mode_id
    assert {item["name"] for item in result["agents"]} == expected_names
    assert {item["source"] for item in result["agents"]} == {"builtin"}
    first_name = next(iter(expected_names))
    assert runtime.list_available_agents(name=first_name)["agents"] == [
        item for item in result["agents"] if item["name"] == first_name
    ]
    assert runtime.list_available_agents(name="not-registered")["agents"] == []
    assert runtime._runner is None
    assert not (tmp_path / ".juice").exists()


def test_gateway_creates_and_resumes_declarative_runner_without_live_root(tmp_path: Path) -> None:
    runtime = DirectRunnerRuntime(base_dir=tmp_path, agent_mode="agent")

    status = runtime.start_session()
    runner = runtime._runner

    assert runner is not None
    assert status.root_agent_name == "root"
    assert runner.config.mode_id == "agent"
    assert runner.state["runner_config"]["mode_id"] == "agent"
    # Runner creation is a declaration operation.  AgentManager acquires the
    # root only on the first stream request, rather than at gateway startup.
    assert dict(runner.agent_manager.live_agents) == {}

    resumed = DirectRunnerRuntime(base_dir=tmp_path)
    resumed_status = resumed.resume_session(status.runner_id)

    assert resumed_status.runner_id == status.runner_id
    assert resumed_status.root_agent_name == "root"
    assert resumed._runner is not None
    assert resumed._runner.config.mode_id == "agent"

    runtime.stop_session()
    resumed.stop_session()


def test_gateway_reconfigures_runner_and_serializes_stable_root_identity(tmp_path: Path) -> None:
    runtime = DirectRunnerRuntime(base_dir=tmp_path)
    first = runtime.start_session()

    switched = runtime.switch_agent_mode("plan")

    assert switched.runner_id == first.runner_id
    assert switched.root_agent_name == "root"
    assert runtime._runner is not None
    assert runtime._runner.config.mode_id == "plan"
    payload = _serialize_session_status(switched)
    assert payload["root_agent_name"] == "root"
    assert "root_actor_name" not in payload

    runtime.stop_session()


def test_gateway_agent_listing_follows_the_active_mode(tmp_path: Path) -> None:
    """A mode switch updates discovery instead of reusing an old projection."""

    handlers = RpcHandlers()
    started = handlers.dispatch("start_session", {"base_dir": str(tmp_path), "agent_mode": "agent"})
    expected_by_mode = {
        "agent": {"general", "explore"},
        # Plan is intentionally the same investigator set as agent mode.
        "plan": {"general", "explore"},
        "group": {"general_worker", "research_worker", "implement_worker", "review_worker", "evolution_worker"},
        # A new Team starts without members; root adds them explicitly.
        "team": set(),
    }

    for mode_id, expected_names in expected_by_mode.items():
        if mode_id != "agent":
            switched = handlers.dispatch("switch_agent_mode", {"agent_mode": mode_id})
            assert switched["runner_id"] == started["runner_id"]
        result = handlers.dispatch("list_available_agents", {})
        assert result["mode_id"] == mode_id
        assert {entry["name"] for entry in result["agents"]} == expected_names


def test_gateway_runtime_has_no_legacy_execution_owner_references() -> None:
    source = (Path(__file__).resolve().parents[3] / "adapters/stdio_gateway/runtime.py").read_text(
        encoding="utf-8"
    )

    for prohibited in (
        "GraphRuntime",
        "ModeProfile",
        "mode_profiles",
        "switch_prebuilt_runner",
        "reconfigure_prebuilt_runner",
        "_live_actors",
    ):
        assert prohibited not in source


def test_gateway_rejects_retired_memory_dream_command() -> None:
    """The one-time switch must not leave a broken legacy RPC route behind."""

    with pytest.raises(ValueError, match="Unknown method: run_dream"):
        RpcHandlers().dispatch("run_dream", {})
