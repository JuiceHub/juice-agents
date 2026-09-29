"""Contracts for manager-owned tool, graph and async-task runtime state."""

from __future__ import annotations

import threading
import time
import sys
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from juice_agents.core.agent.tools.runtime import (
    Tool,
    ToolExecutionContext,
    ToolExecutionMode,
    ToolExecutionPolicy,
)
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.graph import GraphCancelledError
from juice_agents.core.graph.runs import GraphRunStore
from juice_agents.core.managers import AsyncTaskManager, GraphRunManager, ToolManager
from juice_agents.core.runner.config import AgentBinding, ToolPolicy, RunnerConfig


class _EchoTool(Tool):
    name = "echo"

    def execution_policy(self, args: dict[str, Any], context: Any) -> ToolExecutionPolicy:
        del args, context
        return ToolExecutionPolicy(mode=ToolExecutionMode.PARALLEL_SAFE)

    def forward(self, value: str) -> dict[str, str]:
        return {"value": value}


def test_tool_manager_persists_terminal_audits(tmp_path: Path) -> None:
    manager = ToolManager([_EchoTool()], state_dir=tmp_path / "tools")
    try:
        results = manager.execute([{"name": "echo", "args": {"value": "one"}}])
        assert results[0].status == "completed"
        assert results[0].observation == {"value": "one"}
        audit_lines = (tmp_path / "tools" / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(audit_lines) == 1
        assert '"status": "completed"' in audit_lines[0]
    finally:
        manager.release()


def test_tool_manager_keeps_agent_execution_state_outside_the_agent(tmp_path: Path) -> None:
    """An Agent receives no executor; RunnerContext selects its binding by id."""

    class _Agent:
        tools = {"echo": _EchoTool()}
        tool_executor_max_workers = 1

    agent = _Agent()
    manager = ToolManager(state_dir=tmp_path / "tools")
    try:
        manager.bind_agent(agent, agent_id="agent-1", context=manager.context)
        assert not hasattr(agent, "tool_executor")
        results = manager.execute_for_agent(
            agent_id="agent-1",
            actions={"name": "echo", "args": {"value": "context-route"}},
            context=manager.context,
        )
        assert results[0].observation == {"value": "context-route"}
    finally:
        manager.release()


def test_tool_manager_applies_runner_policy_before_agent_permission(tmp_path: Path) -> None:
    """A Manager-owned configuration denial cannot be bypassed by an Agent hook."""

    manager = ToolManager(
        [_EchoTool()],
        state_dir=tmp_path / "tools",
        policy_check=lambda _tool, _args: {"allowed": False, "reason": "mode disabled"},
    )
    try:
        results = manager.execute(
            {"name": "echo", "args": {"value": "blocked"}},
            context=ToolExecutionContext(permission_check=lambda _tool, _args: True),
        )
        assert results[0].status == "denied"
        assert results[0].error == "mode disabled"
    finally:
        manager.release()


def test_tool_manager_rebinds_resolved_action_to_policy_context(tmp_path: Path) -> None:
    """A stale resolved action cannot carry a pre-policy permission hook."""

    manager = ToolManager(
        [_EchoTool()],
        state_dir=tmp_path / "tools",
        policy_check=lambda _tool, _args: {"allowed": False, "reason": "mode disabled"},
    )
    try:
        # This is the path used by CodeAct: resolve first, execute later.  The
        # action's original context permits execution, but the Runner policy
        # must still be authoritative when ToolManager receives the action.
        stale = manager.resolve(
            {"name": "echo", "args": {"value": "blocked"}},
            context=ToolExecutionContext(permission_check=lambda _tool, _args: True),
        )
        result = manager.execute_action(
            stale,
            context=ToolExecutionContext(permission_check=lambda _tool, _args: True),
        )
        assert result.status == "denied"
        assert result.error == "mode disabled"
    finally:
        manager.release()


def test_declarative_read_only_exception_survives_config_serialization(tmp_path: Path) -> None:
    """Workflow exceptions are data, never a plan-mode callback in Runner."""

    config = RunnerConfig(
        mode_id="review",
        root_binding=AgentBinding("reviewer", "root", "persistent"),
        member_binding=AgentBinding("reviewer", "member"),
        tool_policy=ToolPolicy(read_only=True, read_only_exceptions=frozenset({"echo"})),
    )
    restored = RunnerConfig.from_dict(config.to_dict())
    assert restored.tool_policy.read_only_exceptions == frozenset({"echo"})

    manager = ToolManager(
        [_EchoTool()],
        state_dir=tmp_path / "tools",
        policy_check=lambda tool, _args: (
            True
            if tool.name in restored.tool_policy.read_only_exceptions
            else {"allowed": False, "reason": "read-only policy"}
        ),
    )
    try:
        assert manager.execute({"name": "echo", "args": {"value": "allowed"}})[0].status == "completed"
    finally:
        manager.release()


def test_async_task_manager_runs_cancels_and_recovers(tmp_path: Path) -> None:
    state_dir = tmp_path / "async"
    manager = AsyncTaskManager(state_dir=state_dir, scope_id="scope-a", max_workers=2)
    try:
        receipt = manager.launch(
            task_type="local_agent",
            owner_agent_id="agent:root",
            owner_agent_name="root",
            description="success",
            runner=lambda _task, _dir: {"answer": 42},
            summary_builder=lambda result: f"answer={result['answer']}",
        )
        manager.wait_active_async_tasks(timeout_seconds=2)
        completed = manager.get_task(receipt["async_task_id"])
        assert completed is not None
        assert completed["type"] == "local_agent"
        assert completed["status"] == "completed"
        assert manager.read_output(receipt["async_task_id"])["latest_result"] == {"answer": 42}

        # A second manager sees durable work from a dead process as killed;
        # it must not attempt to rerun an opaque Python callback.
        gate = threading.Event()
        pending = manager.launch(
            task_type="local_agent",
            owner_agent_id="agent:root",
            owner_agent_name="root",
            description="recoverable",
            runner=lambda _task, _dir: gate.wait(2),
            summary_builder=lambda _result: "done",
        )
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            task = manager.get_task(pending["async_task_id"])
            if task and task["status"] == "running":
                break
            time.sleep(0.01)
        restored = AsyncTaskManager(state_dir=state_dir, scope_id="scope-a")
        try:
            recovered = restored.recover()
            assert [item["async_task_id"] for item in recovered] == [pending["async_task_id"]]
            assert restored.get_task(pending["async_task_id"])["status"] == "killed"  # type: ignore[index]
        finally:
            restored.release()
            gate.set()
    finally:
        manager.release()


def test_async_task_manager_owns_background_shell_creation(tmp_path: Path) -> None:
    """Shell processes are created by AsyncTaskManager, not by Runner."""

    manager = AsyncTaskManager(state_dir=tmp_path / "async", scope_id="scope-shell")
    try:
        receipt = manager.launch_shell(
            owner_agent_id="agent:root",
            owner_agent_name="root",
            command=f'{sys.executable} -c "print(\"managed shell\")"',
            cwd=tmp_path,
            timeout_seconds=2,
        )
        manager.wait_active_async_tasks(timeout_seconds=3)
        task = manager.get_task(receipt["async_task_id"])
        assert task is not None
        assert task["type"] == "local_bash"
        assert task["status"] == "completed"
    finally:
        manager.release()


def test_async_task_manager_persists_agent_runtime_state(tmp_path: Path) -> None:
    """The private task store follows the manager's agent-only vocabulary."""

    manager = AsyncTaskManager(state_dir=tmp_path / "async", scope_id="scope-agent")
    try:
        registered = manager.register_agent(
            agent_name="worker",
            agent_id="agent:worker",
            role="persistent",
        )
        assert registered["agent_id"] == "agent:worker"

        task = manager.ensure_persistent_agent("worker", description="durable worker")
        assert task["owner_agent_name"] == "worker"
        assert manager.acquire_agent_run("worker", run_id="round-1")
        assert manager.finish_agent_run("worker", run_id="round-1")

        persisted = json.loads((tmp_path / "async" / "registry.json").read_text(encoding="utf-8"))
        assert persisted["agents"]["worker"]["agent_id"] == "agent:worker"
        assert "actors" not in persisted
    finally:
        manager.release()


@dataclass
class _GraphMetadata:
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": ""}


class _Graph:
    def stream(self, payload: dict[str, Any], *, config: dict[str, Any]):
        yield {"node": "start", "state": {"value": payload["value"]}}


class _BlockingGraph:
    def stream(self, payload: dict[str, Any], *, config: dict[str, Any]):
        del payload
        while not config["cancel_event"].is_set():
            time.sleep(0.01)
        raise GraphCancelledError("cancelled")


class _GraphRegistry:
    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self.instantiate_calls = 0

    def get_metadata(self, name: str) -> _GraphMetadata:
        return _GraphMetadata(name)

    def instantiate(self, name: str, *, context: Any) -> Any:
        del name, context
        self.instantiate_calls += 1
        return self.graph

    def instantiate_from_path(self, path: Path, *, context: Any) -> Any:
        del path, context
        self.instantiate_calls += 1
        return self.graph


def _graph_manager(tmp_path: Path, graph: Any) -> tuple[GraphRunManager, _GraphRegistry]:
    registry = _GraphRegistry(graph)
    manager = GraphRunManager(
        store=GraphRunStore(tmp_path / "runner"),
        registry=registry,  # type: ignore[arg-type]
        config_context=ConfigurationContext.from_workspace(tmp_path),
    )
    return manager, registry


def test_graph_run_manager_instantiates_executes_and_stops(tmp_path: Path) -> None:
    manager, registry = _graph_manager(tmp_path, _Graph())
    try:
        completed = manager.run("demo", {"value": "ok"})
        assert completed["status"] == "completed"
        assert registry.instantiate_calls == 1
        result_path = manager.store.run_dir(completed["graph_run_id"]) / "result.json"
        assert result_path.exists()
    finally:
        manager.release()

    manager, _ = _graph_manager(tmp_path, _BlockingGraph())
    try:
        manifest = manager.create_run("blocking", {})
        future = manager.submit(manifest["graph_run_id"])
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if manager.store.get(manifest["graph_run_id"])["status"] == "running":
                break
            time.sleep(0.01)
        manager.stop(manifest["graph_run_id"])
        assert future.result(timeout=2)["status"] == "stopped"
    finally:
        manager.release()


def test_graph_manager_receives_a_narrow_agent_dispatcher_not_runner() -> None:
    """Graphs may request Agent work without making a Manager own Runner."""

    source = inspect.getsource(GraphRunManager)
    assert "runtime_context" not in source
    assert "from juice_agents.core.runner" not in source
    assert "agent_dispatcher" in source
