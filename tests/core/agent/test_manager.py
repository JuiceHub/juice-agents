"""Lifecycle tests for the Registry -> AgentManager runtime boundary."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from juice_agents.core.agent.manager import (
    AgentExecutionInterrupted,
    AgentLifecycle,
    AgentManager,
    AgentManagerCallbacks,
    AgentStatus,
)
from juice_agents.core.agent.sessions import ActionStep, AgentSession, TaskStep


class _ScriptedAgent:
    """Small Agent protocol double; Registry remains the only constructor."""

    def __init__(self, name: str, *, wait_for_cancel: bool = False) -> None:
        self.name = name
        self.agent_id = ""
        self.max_steps = 1
        self.session = AgentSession(system_prompt=f"system for {name}")
        self.wait_for_cancel = wait_for_cancel
        self.cancel_event: threading.Event | None = None
        self.step_started = threading.Event()
        self.reset_count = 0

    def _reset_tool_runtime_state(self) -> None:
        self.reset_count += 1

    def step(self, step: ActionStep) -> ActionStep:
        self.step_started.set()
        if self.wait_for_cancel:
            assert self.cancel_event is not None
            self.cancel_event.wait(timeout=3)
            raise RuntimeError("provider cancelled")
        step.model_output = "completed"
        step.round_outcome = "submitted"
        step.output = f"output:{self.name}"
        return step


class _Registry:
    """Test double exposing only the new fresh-construction Registry API."""

    def __init__(self, *, wait_for_cancel: bool = False) -> None:
        self.wait_for_cancel = wait_for_cancel
        self.instantiate_calls: list[tuple[Any, dict[str, Any]]] = []
        self.instances: list[_ScriptedAgent] = []

    def instantiate(self, binding: Any, **kwargs: Any) -> _ScriptedAgent:
        self.instantiate_calls.append((binding, dict(kwargs)))
        name = str(getattr(binding, "name", None) or binding.get("name") if isinstance(binding, dict) else binding)
        agent = _ScriptedAgent(name, wait_for_cancel=self.wait_for_cancel)
        self.instances.append(agent)
        return agent


def _manager(
    tmp_path: Path,
    registry: _Registry,
    *,
    callbacks: AgentManagerCallbacks | None = None,
) -> AgentManager:
    return AgentManager(
        registry=registry,  # type: ignore[arg-type] - intentional protocol double
        runner_id="run-1",
        state_dir=tmp_path,
        callbacks=callbacks,
    )


def test_root_is_lazily_acquired_fresh_and_restores_session(tmp_path: Path) -> None:
    registry = _Registry()
    manager = _manager(tmp_path, registry)

    root = manager.acquire_root({"name": "root"})
    assert registry.instantiate_calls == [({"name": "root"}, {})]
    assert root.agent_id == "root"
    assert root.status is AgentStatus.READY
    assert manager.run(root, "first request") == "output:root"
    manager.release(root)

    resumed = _manager(tmp_path, registry).acquire_root({"name": "root"})
    assert len(registry.instances) == 2  # resume never revives the released object
    assert resumed.instance is not registry.instances[0]
    assert [step.task for step in resumed.session.steps if isinstance(step, TaskStep)] == ["first request"]
    assert resumed.status is AgentStatus.READY


def test_functional_agent_is_released_but_transcript_remains_inspectable(tmp_path: Path) -> None:
    registry = _Registry()
    manager = _manager(tmp_path, registry)

    worker = manager.acquire(
        {"name": "worker"},
        lifecycle=AgentLifecycle.FUNCTIONAL,
        role="worker",
    )
    assert manager.run(worker, "do work", reset_session=True) == "output:worker"

    assert worker.instance is None
    assert worker.status is AgentStatus.RELEASED
    with pytest.raises(KeyError):
        manager.get(worker.agent_id)
    snapshot = manager.load_snapshot(worker.agent_id)
    assert snapshot is not None
    assert snapshot["status"] == "released"
    assert [step["task"] for step in snapshot["session"]["steps"] if step["type"] == "task"] == ["do work"]
    report = manager.describe_sessions()
    assert report["agents"][0]["agent_id"] == worker.agent_id


def test_functional_root_still_uses_the_manager_lifecycle(tmp_path: Path) -> None:
    """Root role must not silently override a declarative functional lifetime."""

    registry = _Registry()
    manager = _manager(tmp_path, registry)
    root = manager.acquire_root({"name": "root", "lifecycle": "functional"})

    assert root.lifecycle is AgentLifecycle.FUNCTIONAL
    assert manager.run(root, "one request") == "output:root"
    assert root.status is AgentStatus.RELEASED
    assert manager.load_snapshot("root")["lifecycle"] == "functional"  # type: ignore[index]


def test_persistent_agent_reuses_live_instance_and_checkpoints_each_step(tmp_path: Path) -> None:
    registry = _Registry()
    persisted_steps: list[int] = []
    manager = _manager(
        tmp_path,
        registry,
        callbacks=AgentManagerCallbacks(
            after_persist=lambda snapshot: persisted_steps.append(len(snapshot["session"]["steps"])),
        ),
    )

    first = manager.acquire({"name": "planner"}, lifecycle="persistent", role="member")
    second = manager.acquire({"name": "planner"}, lifecycle="persistent", role="member")
    assert first is second
    assert manager.run(first, "one") == "output:planner"
    assert manager.run(second, "two") == "output:planner"

    tasks = [step.task for step in first.session.steps if isinstance(step, TaskStep)]
    assert tasks == ["one", "two"]
    assert len(registry.instances) == 1
    assert max(persisted_steps) >= 4  # task + action are checkpointed for both rounds


def test_runtime_binder_attachment_callback_and_interrupt_share_manager_token(tmp_path: Path) -> None:
    registry = _Registry(wait_for_cancel=True)
    bound: list[tuple[str, str, str]] = []

    def bind_runtime(managed, cancel_event) -> None:
        assert managed.instance is not None
        managed.instance.cancel_event = cancel_event
        bound.append((managed.agent_name, managed.agent_id, managed.role))

    manager = _manager(tmp_path, registry, callbacks=AgentManagerCallbacks(bind_runtime=bind_runtime))
    managed = manager.acquire({"name": "slow"}, lifecycle="persistent", role="teammate")
    assert bound == [("slow", managed.agent_id, "teammate")]

    outcome: list[BaseException] = []

    def run() -> None:
        try:
            manager.run(managed, "wait")
        except BaseException as exc:  # capture the worker's expected interruption
            outcome.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert registry.instances[0].step_started.wait(timeout=2)
    assert manager.interrupt(managed, reason="user_cancelled") == [managed.agent_id]
    thread.join(timeout=4)

    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], AgentExecutionInterrupted)
    assert manager.load_snapshot(managed.agent_id)["status"] == "interrupted"  # type: ignore[index]


def test_manager_has_no_runner_or_static_config_dependency() -> None:
    source = Path(AgentManager.__module__.replace(".", "/") + ".py")
    # Inspect the installed source through the class file instead of assuming
    # pytest's cwd; this protects the architecture boundary in CI as well.
    import inspect

    content = Path(inspect.getfile(AgentManager)).read_text(encoding="utf-8")
    assert "from juice_agents.core.runner" not in content
    assert ".yaml" not in content
    assert source.name == "manager.py"


def test_agents_cannot_rebuild_live_instances_outside_the_manager() -> None:
    """Evolution changes declarations and waits for the next Manager acquire."""

    import inspect
    from juice_agents.core.agent.agents import MultiStepAgent
    from juice_agents.core.agent import runtime_reconciler

    agent_source = Path(inspect.getfile(MultiStepAgent)).read_text(encoding="utf-8")
    reconciler_source = Path(inspect.getfile(runtime_reconciler)).read_text(encoding="utf-8")
    assert "def reinstantiate(" not in agent_source
    assert "apply_pending_capability_refresh" not in agent_source
    assert ".instantiate(" not in reconciler_source
    assert '"effective_from": "fresh_acquire"' in reconciler_source
