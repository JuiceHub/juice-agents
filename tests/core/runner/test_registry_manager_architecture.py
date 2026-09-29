"""Architecture guards for the declarative Registry -> Manager -> Runner path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from juice_agents.core.agent.sessions import ActionStep, AgentSession
from juice_agents.core.prebuilt import create_prebuilt_runner
from juice_agents.core.runner import Runner
from juice_agents.core.runner.config import AgentBinding, ModeRegistry, RunnerConfig, mode_registry
from juice_agents.core.runner.persistence.store import build_layout


class _ManagedTestAgent:
    """Tiny Agent protocol double constructed exclusively by a fake Registry."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.agent_id = ""
        self.max_steps = 1
        self.tools: dict[str, Any] = {}
        self.tool_executor_max_workers = 1
        self.session = AgentSession(system_prompt="test")

    def bind_manager_infra(self, **_kwargs: Any) -> None:
        """The real Agent binds queue/workspace here; this double has none."""

    def set_log_file_path(self, _path: Path) -> None:
        """The Manager owns persistence; the double does not write logs."""

    def step(self, step: ActionStep) -> ActionStep:
        step.round_outcome = "submitted"
        step.output = f"done:{self.name}"
        return step


class _StaticRegistry:
    """A fresh-construction-only Registry double; no live state is retained."""

    def __init__(self) -> None:
        self.instantiated: list[str] = []

    def instantiate(self, raw: Any, **_kwargs: Any) -> _ManagedTestAgent:
        name = str(getattr(raw, "name", "") or (raw.get("name") if isinstance(raw, dict) else raw))
        self.instantiated.append(name)
        return _ManagedTestAgent(name)


def _runner(tmp_path: Path, mode_id: str) -> tuple[Runner, _StaticRegistry]:
    runner = Runner.create(runner_config=mode_id, base_dir=tmp_path)
    registry = _StaticRegistry()
    # The composition has already created its real static Registry. Replacing
    # it with a protocol double proves that the Runner only requests a fresh
    # instance via AgentManager at request time.
    runner.agent_manager.registry = registry  # type: ignore[assignment]
    return runner, registry


@pytest.mark.parametrize("mode_id", ["agent", "plan", "group", "team"])
def test_builtin_modes_share_the_same_runner_manager_path(tmp_path: Path, mode_id: str) -> None:
    runner, registry = _runner(tmp_path, mode_id)

    # Root acquisition is lazy; Runner.create must not construct an Agent.
    assert not runner.agent_manager.live_agents
    assert registry.instantiated == []

    events = list(runner.stream("hello"))
    assert registry.instantiated == [mode_registry.resolve(mode_id).root_binding.agent_name]
    assert [event["kind"] for event in events] == ["action_step", "round_end"]
    assert {event["mode_id"] for event in events} == {mode_id}
    assert events[-1]["output"] == f"done:{mode_registry.resolve(mode_id).root_binding.agent_name}"
    assert runner.describe_agent_sessions()["agents"][0]["agent_name"] == mode_registry.resolve(mode_id).root_binding.agent_name


def test_runner_rejects_live_root_and_old_manifest_schema(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="已移除"):
        Runner.create(runner_config="agent", base_dir=tmp_path, root_agent=object())

    layout = build_layout(tmp_path, "old-run")
    layout.root_dir.mkdir(parents=True)
    layout.manifest_path.write_text(json.dumps({"schema_version": 3}), encoding="utf-8")
    with pytest.raises(ValueError, match="旧 Runner 状态不支持恢复"):
        Runner.resume(runner_id="old-run", base_dir=tmp_path)


def test_runner_contains_managers_not_live_domain_maps(tmp_path: Path) -> None:
    runner, _ = _runner(tmp_path, "agent")
    assert hasattr(runner, "agent_manager")
    assert hasattr(runner, "tool_manager")
    assert hasattr(runner, "graph_manager")
    assert hasattr(runner, "async_task_manager")
    assert not hasattr(runner, "_live_actors")
    assert not hasattr(runner, "mode_coordinator")


def test_failed_lazy_root_acquisition_releases_the_request_gate(tmp_path: Path) -> None:
    """A static declaration error must not turn the Runner permanently busy."""

    runner, registry = _runner(tmp_path, "agent")

    class _FailingRegistry:
        def instantiate(self, _raw: Any, **_kwargs: Any) -> _ManagedTestAgent:
            raise ValueError("bad static declaration")

    runner.agent_manager.registry = _FailingRegistry()  # type: ignore[assignment]
    with pytest.raises(ValueError, match="bad static declaration"):
        list(runner.stream("first"))

    runner.agent_manager.registry = registry  # type: ignore[assignment]
    assert list(runner.stream("second"))[-1]["output"] == "done:root"


def test_runner_queues_user_messages_as_agent_attachments(tmp_path: Path) -> None:
    runner, _ = _runner(tmp_path, "agent")
    list(runner.stream("initialize root"))

    result = runner.queue_agent_user_message(
        agent_name=runner.root_agent_name,
        message="Inspect the failing tests.",
    )
    managed = runner.agent_manager.get_by_name(runner.root_agent_name)
    attachments = runner._attachments_for(managed, 1)

    assert result == {
        "accepted": True,
        "agent_name": runner.root_agent_name,
        "delivery": "agent_attachment",
    }
    assert attachments[0]["attachment_type"] == "agent_user_message"
    assert attachments[0]["payload"]["agent_name"] == runner.root_agent_name
    assert attachments[0]["payload"]["text"] == "Inspect the failing tests."


@pytest.mark.parametrize("mode_id", ["agent", "plan", "group", "team"])
def test_prebuilt_factory_seeds_only_static_declarations(tmp_path: Path, mode_id: str) -> None:
    """Prebuilt setup seeds workers but never writes a root declaration."""

    runner = create_prebuilt_runner(runner_config=mode_id, base_dir=tmp_path)
    assert not (tmp_path / ".juice" / "agents" / "root.yaml").exists()
    assert not runner.agent_manager.live_agents
    assert runner.root_agent_name == "root"
    runner.close()


@pytest.mark.parametrize("mode_id", ["agent", "plan", "group", "team"])
def test_builtin_modes_construct_root_from_an_in_memory_agent_config(tmp_path: Path, mode_id: str) -> None:
    """Root is a detached AgentConfig and cannot become Registry YAML."""

    runner = create_prebuilt_runner(runner_config=mode_id, base_dir=tmp_path)
    try:
        instance = runner.agent_registry.instantiate(runner.root_config, model=object())
        assert instance.name == "root"
        assert not hasattr(instance, "tool_executor")
        assert not (tmp_path / ".juice" / "agents" / "root.yaml").exists()
    finally:
        runner.close()


def test_custom_mode_registry_accepts_data_templates_not_execution_code() -> None:
    registry = ModeRegistry()
    template = RunnerConfig(
        mode_id="review",
        root_binding=AgentBinding("reviewer", "root", "persistent"),
        member_binding=AgentBinding("reviewer", "member"),
    )

    assert registry.register(template) is template
    assert registry.resolve("review") is template
    with pytest.raises(TypeError, match="RunnerConfig"):
        registry.register(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        RunnerConfig(  # type: ignore[call-arg]
            mode_id="invalid",
            root_binding=template.root_binding,
            member_binding=template.member_binding,
            coordinator_factory=lambda: None,
        )


def test_runner_enforces_configured_agent_allow_list_for_all_entry_points(tmp_path: Path) -> None:
    """Direct Runner dispatch cannot bypass AgentTool's policy projection."""

    runner, _ = _runner(tmp_path, "plan")
    list(runner.stream("initialize root"))

    with pytest.raises(PermissionError, match="当前 Runner 不允许调用 Agent"):
        runner.invoke_agent({"agent_ref": "forbidden", "task": "not allowed", "execution": "sync"})


def test_mode_switch_preserves_runner_id_root_identity_and_plan_location(tmp_path: Path) -> None:
    """The mode changes at an idle boundary without creating a new session."""

    runner, registry = _runner(tmp_path, "agent")
    list(runner.stream("initialize root"))
    original_id = runner.runner_id

    runner.reconfigure(mode_id="plan", plan_return_mode="agent")

    assert runner.runner_id == original_id
    assert runner.root_agent_name == "root"
    assert runner.mode_id == "plan"
    assert runner.state["plan_return_mode"] == "agent"
    assert runner.plan_file_path() == runner.layout.plans_dir / "root.md"
    assert runner.root_config.name == "root"
    assert {ref.name for ref in runner.root_config.tools} >= {"plan", "exit_plan", "agent_tool"}
    assert all(item == "root" for item in registry.instantiated)


def test_mode_switch_rejects_active_work_without_mutating_current_mode(tmp_path: Path) -> None:
    runner, _ = _runner(tmp_path, "agent")
    runner.state["status"] = "running"

    with pytest.raises(Exception, match="不能切换模式"):
        runner.reconfigure(mode_id="plan")

    assert runner.mode_id == "agent"


def test_unload_state_is_runner_and_mode_local_and_can_be_reactivated(tmp_path: Path) -> None:
    """Unload changes effective availability without rewriting Agent YAML."""

    runner = create_prebuilt_runner(runner_config="agent", base_dir=tmp_path)
    try:
        original = runner.agent_registry.load_config("explore").to_dict()
        assert {item["name"] for item in runner.list_available_agents()["agents"]} >= {"general", "explore"}

        runner.disable_agent("explore")
        assert "explore" not in {item["name"] for item in runner.list_available_agents()["agents"]}
        assert runner.agent_registry.load_config("explore").to_dict() == original

        runner.enable_agent("explore")
        assert "explore" in {item["name"] for item in runner.list_available_agents()["agents"]}
    finally:
        runner.close()


def test_team_runner_initializes_empty_default_manifest(tmp_path: Path) -> None:
    """A new Team starts without members or a separate teamlead declaration."""

    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    try:
        assert runner.team_name == "default"
        assert (tmp_path / ".juice" / "teams" / "default" / "manifest.yaml").is_file()
        assert runner.available_agent_names() == frozenset()
        assert {item["name"] for item in runner.list_available_agents()["agents"]} == set()
        assert [member["name"] for member in runner.team_manager.snapshot()["members"]] == ["root"]
        assert not (tmp_path / ".juice" / "agents" / "teamlead.yaml").exists()
    finally:
        runner.close()


def test_runner_has_no_domain_executor_or_subprocess_creation() -> None:
    """Runner dispatches only; manager modules own domain execution resources."""

    import inspect

    source = inspect.getsource(Runner)
    assert ".instantiate(" not in source
    assert "subprocess." not in source
    assert "_live_actors" not in source
