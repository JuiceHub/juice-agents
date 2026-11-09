"""Functional coverage of Runner's durable input and autonomous round boundary."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import pytest

from juice_agents import Runner
from juice_agents.core.agent.context import ActionStep, ContextTree
from juice_agents.core.runner import AgentBinding, mode_registry


class _Agent:
    """Small protocol double: scheduling and persistence use the real Managers."""

    def __init__(self, name: str, on_step: Callable[[Any, ActionStep], None] | None = None) -> None:
        self.name = name
        self.max_steps = 2
        self.tools: dict[str, Any] = {}
        self.tool_executor_max_workers = 1
        self.context = ContextTree(tree_id=f"test_{name}", system_prompt="test")
        self.on_step = on_step

    def bind_manager_infra(self, **_kwargs: Any) -> None:
        pass

    def set_log_file_path(self, _path: Any) -> None:
        pass

    def step(self, step: ActionStep) -> ActionStep:
        if self.on_step is not None:
            self.on_step(self, step)
        step.round_outcome = "submitted"
        step.output = f"done:{self.name}"
        return step


class _Registry:
    def __init__(self, on_step: Callable[[Any, ActionStep], None] | None = None) -> None:
        self.on_step = on_step

    def instantiate(self, raw: Any, **_kwargs: Any) -> _Agent:
        return _Agent(str(getattr(raw, "name", raw)), self.on_step)


def _runner(tmp_path: Path, on_step: Callable[[Any, ActionStep], None] | None = None) -> Runner:
    runner = Runner.create(base_dir=tmp_path, runner_config="agent")
    runner.agent_manager.registry = _Registry(on_step)  # type: ignore[assignment]
    return runner


def test_post_waits_for_activation_and_subscription_observes_autonomous_round(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    seen: list[dict[str, Any]] = []
    ended = threading.Event()

    def listener(event: dict[str, Any]) -> None:
        seen.append(event)
        if event.get("kind") == "round_end":
            ended.set()

    unsubscribe = runner.subscribe(listener)
    try:
        receipt = runner.post("root", {
            "attachment_type": "user_message", "message_id": "durable-first",
            "payload": {"text": "Inspect project"},
        })
        # Accepting input is observable before activation; execution is not.
        assert [event["kind"] for event in seen if event["kind"] != "agent_state"] == ["message_received"]
        assert (runner.layout.agents_dir / "root" / "inbox.json").exists()
        runner.activate()
        runner.activate()
        assert ended.wait(5)
        assert [event["kind"] for event in seen if event.get("round_id") == receipt["round_id"]] == [
            "message_received", "agent_state", "round_start", "step_start", "step_end", "agent_state", "round_end",
        ]
        assert len({event["event_id"] for event in seen}) == len(seen)
        assert runner.state["status"] == "idle"
    finally:
        unsubscribe()
        runner.close()


def test_foreground_round_returns_while_independent_async_task_continues(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    entered, release = threading.Event(), threading.Event()
    try:
        runner._root()

        def background(_task: dict[str, Any], _path: Path) -> str:
            entered.set()
            assert release.wait(5)
            return "background result"

        task = runner.async_task_manager.launch(
            task_type="local_bash", owner_agent_id="root", owner_agent_name="root",
            description="blocked independent work", runner=background, summary_builder=lambda result: str(result),
        )
        assert entered.wait(5)
        assert runner.run("foreground request") == "done:root"
        assert runner.state["status"] == "idle"
        assert runner.get_async_task(task["async_task_id"])["status"] == "running"
    finally:
        release.set()
        runner.close()


def test_round_cancel_does_not_cancel_async_work_or_block_next_input(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()
    calls = 0

    def step(_agent: _Agent, _step: ActionStep) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(5)

    runner = _runner(tmp_path, step)
    events: list[dict[str, Any]] = []
    worker = threading.Thread(target=lambda: events.extend(runner.stream("first")))
    try:
        worker.start()
        assert entered.wait(5)
        round_id = runner.active_round_id
        assert round_id and runner.cancel_round(round_id)
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert events[-1]["data"]["result"]["status"] == "cancelled"
        assert runner.run("after cancellation") == "done:root"
        assert runner.cancel_round(round_id) is False
    finally:
        release.set()
        worker.join(5)
        runner.close()


def test_functional_owner_cannot_launch_detached_background_work(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    try:
        runner._root()
        child = runner.agent_manager.acquire("general", agent_name="call", lifecycle="functional")
        with pytest.raises(PermissionError, match="functional"):
            runner.launch_local_bash_async_task(
                owner_agent_name=child.agent_name, command="true", cwd=str(tmp_path), timeout_seconds=1,
            )
        with pytest.raises(PermissionError, match="functional"):
            runner.invoke_agent({
                "agent_ref": "general", "task": "detached", "owner_agent_name": child.agent_name,
                "execution": "background",
            })
    finally:
        runner.close()


def test_concurrent_user_input_waits_for_its_deferred_round(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()
    calls = 0

    def step(_agent: _Agent, _step: ActionStep) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(3)

    runner = _runner(tmp_path, step)
    first: list[dict] = []
    second: list[dict] = []
    worker = threading.Thread(target=lambda: first.extend(runner.stream("first")))
    other: threading.Thread | None = None
    worker.start()
    try:
        assert entered.wait(3)
        # The first model call is final: the second input must receive the
        # result of a new round while both foreground requests remain valid.
        stream = runner.stream("second")
        other = threading.Thread(target=lambda: second.extend(stream))
        other.start()
        release.set()
        worker.join(5)
        other.join(5)
        assert not worker.is_alive() and not other.is_alive()
        assert first[-1]["kind"] == second[-1]["kind"] == "round_end"
        assert first[-1]["round_id"] != second[-1]["round_id"]
        assert calls == 2
    finally:
        release.set()
        worker.join(5)
        if other is not None:
            other.join(5)
        runner.close()


def test_functional_root_finishes_inside_each_invocation(tmp_path: Path) -> None:
    config = replace(mode_registry.resolve("agent"), root_binding=AgentBinding("root", "root", "functional"))
    runner = Runner.create(base_dir=tmp_path, runner_config=config)
    runner.agent_manager.registry = _Registry()
    try:
        assert runner.run("first") == "done:root"
        assert not runner.agent_manager.live_agents
        assert runner.run("second") == "done:root"
        assert not runner.agent_manager.live_agents
        assert runner.agent_manager.load_snapshot("root")["session"]["round_checkpoint"]["status"] == "completed"
    finally:
        runner.close()


@pytest.mark.parametrize("lifecycle", ["persistent", "functional"])
@pytest.mark.parametrize("shutdown", ["stop", "close"])
def test_stopped_runner_rejects_new_rounds_without_constructing_an_agent(tmp_path, lifecycle, shutdown):
    config = replace(mode_registry.resolve("agent"), root_binding=AgentBinding("root", "root", lifecycle))
    runner = Runner.create(base_dir=tmp_path, runner_config=config)
    calls = []
    runner.agent_manager.registry = _Registry(lambda *_: calls.append("executed"))
    try:
        getattr(runner, shutdown)()
        with pytest.raises(RuntimeError, match="stopped or closed"):
            runner.run("must not execute")
        assert calls == []
        assert not runner.agent_manager.live_agents
    finally:
        runner.close()


def test_checkpoint_failure_reaches_request_without_a_fake_terminal_event(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    events, errors = [], []
    runner._root()
    runner.subscribe_events(events.append)

    def fail_save(_snapshot):
        raise OSError("checkpoint unavailable")

    saved = runner.agent_manager.snapshot_store.save
    monkeypatch.setattr(runner.agent_manager.snapshot_store, "save", fail_save)

    def request():
        try:
            runner.run_round("durable failure")
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=request, daemon=True)
    try:
        worker.start()
        worker.join(3)
        assert not worker.is_alive(), "a failed checkpoint must not leave request waiters blocked"
        assert len(errors) == 1 and isinstance(errors[0], OSError)
        assert not any(event["kind"] == "round_end" for event in events)
        assert runner._active_requests == 0
    finally:
        monkeypatch.setattr(runner.agent_manager.snapshot_store, "save", saved)
        runner.close()


@pytest.mark.parametrize("lifecycle", ["persistent", "functional"])
def test_background_child_failure_is_a_failed_task(tmp_path, lifecycle):
    def action(agent, _step):
        if agent.name != "root":
            raise RuntimeError("background child failed")

    runner = _runner(tmp_path, action)
    try:
        runner._root()
        receipt = runner.invoke_agent({"agent_ref": "general", "task": "work", "execution": "background", "lifecycle": lifecycle})
        assert not runner.async_task_manager.wait_active_async_tasks(timeout_seconds=3)
        task = runner.get_async_task(receipt["async_task_id"])
        assert task["status"] == "failed"
        assert task["error"] == "background child failed"
    finally:
        runner.close()


@pytest.mark.parametrize("lifecycle", ["persistent", "functional"])
def test_root_cancellation_waits_for_synchronous_child_result(tmp_path, lifecycle):
    entered = threading.Event()

    def action(agent, _step):
        if agent.name == "root":
            runner.invoke_agent({"agent_ref": "general", "task": "wait", "execution": "sync", "lifecycle": lifecycle})
        else:
            entered.set()
            assert agent.runner_context.cancel_event.wait(3)

    runner = _runner(tmp_path, action)
    results, events = [], []
    runner.subscribe_events(events.append)
    worker = threading.Thread(target=lambda: results.append(runner.run_round("start child")), daemon=True)
    try:
        worker.start()
        assert entered.wait(3)
        assert runner.cancel_round(runner.active_round_id)
        worker.join(4)
        assert not worker.is_alive()
        assert results[0].status == "cancelled"
        ended = [event for event in events if event["kind"] == "round_end"]
        assert len(ended) == 2
        assert ended[0]["agent_role"] != "root" and ended[1]["agent_role"] == "root"
        assert all(event["data"]["result"]["status"] == "cancelled" for event in ended)
    finally:
        runner.close()
        worker.join(4)


def test_close_keeps_observers_bound_until_started_work_finishes(tmp_path):
    entered, unwind, close_done = threading.Event(), threading.Event(), threading.Event()

    def action(agent, _step):
        entered.set()
        assert agent.runner_context.cancel_event.wait(3)
        assert unwind.wait(3)

    runner = _runner(tmp_path, action)
    events = []
    runner.subscribe_events(events.append)
    request = threading.Thread(target=lambda: list(runner.stream("work")), daemon=True)
    shutdown = threading.Thread(target=lambda: (runner.close(), close_done.set()), daemon=True)
    try:
        request.start()
        assert entered.wait(3)
        shutdown.start()
        assert not close_done.wait(0.05)
        assert not any(event["kind"] == "round_end" for event in events)
        unwind.set()
        assert close_done.wait(3)
        request.join(3)
        assert not request.is_alive()
        assert [event for event in events if event["kind"] == "round_end"][0]["data"]["result"]["status"] == "cancelled"
    finally:
        unwind.set()
        shutdown.join(3)
        request.join(3)
        runner.close()
