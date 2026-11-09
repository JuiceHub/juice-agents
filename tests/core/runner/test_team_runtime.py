"""End-to-end Team business delivery through the unified Agent inbox scheduler."""

from __future__ import annotations

import time
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from juice_agents.core.agent.context import ActionStep, ContextTree
from juice_agents.core.registry import AgentRegistry
from juice_agents.core.runner import AgentBinding, Runner, RunnerConfig, TeamRunConfig, mode_registry
from adapters.stdio_gateway.serialization import serialize_runner_stream_event


class _SubmittedAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.agent_id = ""
        self.max_steps = 1
        self.tools: dict[str, Any] = {}
        self.tool_executor_max_workers = 1
        self.context = ContextTree(tree_id=f"test_{name}", system_prompt="team test")

    def bind_manager_infra(self, **_kwargs: Any) -> None:
        pass

    def set_log_file_path(self, _path: Path) -> None:
        pass

    def step(self, step: ActionStep) -> ActionStep:
        step.round_outcome = "submitted"
        step.output = f"done:{self.name}"
        return step


class _Registry:
    def instantiate(self, raw: Any, **_kwargs: Any) -> _SubmittedAgent:
        return _SubmittedAgent(str(getattr(raw, "name", raw)))


class _BlockingAgent(_SubmittedAgent):
    def __init__(self, name: str, entered: threading.Event, resume: threading.Event) -> None:
        super().__init__(name)
        self.entered, self.resume = entered, resume

    def step(self, step: ActionStep) -> ActionStep:
        self.entered.set()
        assert self.resume.wait(5.0)
        return super().step(step)


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Team work did not settle")
        time.sleep(0.01)


def _add_member(runner: Runner, name: str) -> None:
    """A minimal private role must work on a newly empty Team."""

    runner.create_team_member(name, config={
        "description": f"Team member {name}",
        "instructions": "Complete assigned work and report the result.",
    })


def _member_session_id(runner: Runner, name: str, generation: int = 1) -> str:
    return f"team--{runner.state['team_run_id']}--{name}--{generation}"


def test_team_dispatch_persists_member_session_and_acknowledges_inbox(tmp_path: Path) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    runner.agent_manager.registry = _Registry()  # type: ignore[assignment]
    runner.activate()
    _add_member(runner, "developer")
    manager = runner.team_manager
    assert manager is not None
    try:
        task = manager.add_task("Implement parser", eligible_members=["developer"])
        _wait_for(lambda: next(item for item in manager.list_tasks() if item["task_id"] == task["task_id"])["status"] == "completed")
        assert runner.agent_manager.load_snapshot(_member_session_id(runner, "developer")) is not None

        message = manager.send_message("root", "developer", "Please report tests")
        _wait_for(lambda: not manager.pending_messages("developer"))
        snapshot = runner.agent_manager.load_snapshot(_member_session_id(runner, "developer"))
        assert snapshot is not None
        # 从新的 ContextTree 结构中检查消息是否被接收
        nodes = snapshot["session"]["nodes"]
        assert any(
            attachment.get("payload", {}).get("message_id") == message["message_id"]
            for node in nodes.values()
            for attachment in node.get("step", {}).get("attachments", [])
        )
        _wait_for(lambda: not manager.has_pending_work())
    finally:
        runner.close()


def test_team_acknowledgement_can_request_rescan_while_dispatch_waits_on_inbox(tmp_path: Path, monkeypatch) -> None:
    """A producer callback must not invert dispatch and Agent durability locks."""
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    runner.agent_manager.registry = _Registry()
    _add_member(runner, "developer")
    runner.team_manager.stopping = True
    runner.team_manager.send_message("root", "developer", "Deliver once")
    runner.team_manager.stopping = False
    held, posting, rescanned = threading.Event(), threading.Event(), threading.Event()
    acquire = runner.team_manager.acquire_member

    def observed_acquire(*args, **kwargs):
        posting.set()
        return acquire(*args, **kwargs)

    monkeypatch.setattr(runner.team_manager, "acquire_member", observed_acquire)

    def acknowledge_at_commit() -> None:
        with runner.agent_manager._lock:
            held.set()
            assert posting.wait(3)
            runner.wake_team()
            rescanned.set()

    acknowledgement = threading.Thread(target=acknowledge_at_commit, daemon=True)
    dispatcher = threading.Thread(target=runner.wake_team, daemon=True)
    try:
        acknowledgement.start()
        assert held.wait(2)
        dispatcher.start()
        assert rescanned.wait(3)
        acknowledgement.join(3)
        dispatcher.join(3)
        assert not acknowledgement.is_alive() and not dispatcher.is_alive()
        runner.activate()
        _wait_for(lambda: not runner.team_manager.pending_messages("developer"))
        managed = runner.agent_manager.get_by_name("developer")
        assert len(managed.committed_message_ids) == 1
    finally:
        runner.close()


def test_team_message_acknowledgement_preserves_busy_until_round_end(tmp_path: Path) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    _add_member(runner, "developer")
    entered, release = threading.Event(), threading.Event()
    runner.agent_manager.registry = type("Registry", (), {
        "instantiate": lambda self, raw, **kwargs: _BlockingAgent(str(getattr(raw, "name", raw)), entered, release),
    })()
    snapshots: list[dict] = []
    runner.subscribe(lambda event: snapshots.append(event["data"]["snapshot"])
                     if event.get("kind") == "team_update" and event.get("data", {}).get("update", {}).get("type") == "messages_acknowledged" else None)
    try:
        runner.activate()
        runner.team_manager.send_message("root", "developer", "Run this message")
        assert entered.wait(3)
        assert not runner.team_manager.pending_messages("developer")
        assert runner.team_manager.has_pending_work()
        assert snapshots and next(member for member in snapshots[0]["members"] if member["name"] == "developer")["busy"]
        release.set()
        _wait_for(lambda: not runner.team_manager.has_pending_work())
    finally:
        release.set()
        runner.close()


def test_recovery_reconciles_final_session_before_team_business_ack(tmp_path: Path, monkeypatch) -> None:
    """A crash after final session commit must settle the existing claim."""
    monkeypatch.setattr(AgentRegistry, "instantiate", lambda self, raw, **kwargs: _Registry().instantiate(raw, **kwargs))
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    _add_member(runner, "developer")
    try:
        task = runner.team_manager.add_task("Finish once", eligible_members=["developer"])
        managed = runner.agent_manager.get_by_name("developer")
        # No scheduler activation: direct consumption simulates the session
        # commit with its round_end business observer lost to process death.
        original = runner.agent_manager.callbacks
        runner.agent_manager.callbacks = __import__("dataclasses").replace(original, on_event=None)
        runner.agent_manager.run(managed, "Finish once")
        runner.agent_manager.callbacks = original
        assert managed.round_checkpoint["status"] == "completed"
        assert runner.team_manager.list_tasks()[0]["status"] == "in_progress"
        runner_id = runner.runner_id
    finally:
        runner.close()
    restored = Runner.resume(runner_id=runner_id, base_dir=tmp_path)
    try:
        current = restored.team_manager.list_tasks()[0]
        assert current["task_id"] == task["task_id"]
        assert current["status"] == "completed"
        restored.activate()
        assert not restored.team_manager.has_pending_work()
    finally:
        restored.close()


def test_team_use_reuses_member_definition_with_a_new_isolated_board(tmp_path: Path) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    runner.agent_manager.registry = _Registry()  # type: ignore[assignment]
    runner.activate()
    try:
        assert [member["name"] for member in runner.team_manager.snapshot()["members"]] == ["root"]

        runner.create_team("review")
        assert runner.team_manager.list_tasks() == []
        _add_member(runner, "developer")
        first_run_id = runner.state["team_run_id"]
        task = runner.team_manager.add_task("Review change", eligible_members=["developer"])
        _wait_for(lambda: runner.team_manager.list_tasks()[0]["status"] == "completed")
        old_session = _member_session_id(runner, "developer")
        assert runner.agent_manager.load_snapshot(old_session) is not None
        assert runner.team_manager.list_tasks()[0]["task_id"] == task["task_id"]

        _wait_for(lambda: not runner._active_team_calls())
        runner.finish_team()
        reused = runner.use_team("review")
        assert runner.state["team_run_id"] != first_run_id
        assert [member["name"] for member in reused["members"]] == ["root", "developer"]
        assert reused["tasks"] == []
        assert runner.agent_manager.load_snapshot(_member_session_id(runner, "developer")) is None

        audit = runner.create_team("audit")
        assert audit["team_name"] == "audit"
        assert [member["name"] for member in audit["members"]] == ["root"]
        assert audit["tasks"] == []
        assert runner.agent_manager.load_snapshot(old_session) is not None
    finally:
        runner.close()


def test_failed_task_notifies_root_and_waits_for_review(tmp_path: Path) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    _add_member(runner, "developer")
    runner.team_manager.stopping = True
    try:
        manager = runner.team_manager
        task = manager.add_task("Check change", eligible_members=["developer"])
        manager.claim_task(task["task_id"], "developer")
        failed = manager.fail_task(task["task_id"], "developer", error="tests failed")

        assert failed["status"] == "in_progress"
        assert failed["error"] == "tests failed"
        assert manager.claim_next("developer") is None
        messages = manager.pending_messages("root")
        assert len(messages) == 1
        assert task["task_id"] in messages[0]["text"]
    finally:
        runner.close()


def test_team_pending_work_blocks_mode_switch_and_resume_keeps_unread_inbox(tmp_path: Path, monkeypatch) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    runner.agent_manager.registry = _Registry()  # type: ignore[assignment]
    _add_member(runner, "developer")
    manager = runner.team_manager
    assert manager is not None
    runner.team_manager.stopping = True
    message = manager.send_message("root", "developer", "Resume this message")
    try:
        assert "Team" in str(runner.mode_switch_block_reason())
        runner_id = runner.runner_id
    finally:
        runner.close()

    monkeypatch.setattr(AgentRegistry, "instantiate", lambda self, raw, **kwargs: _Registry().instantiate(raw, **kwargs))
    resumed = Runner.resume(runner_id=runner_id, base_dir=tmp_path)
    try:
        assert resumed.team_manager.pending_messages("developer")
        resumed.activate()
        _wait_for(lambda: not resumed.team_manager.pending_messages("developer"))
        assert any(item["message_id"] == message["message_id"] for item in resumed.team_manager.snapshot()["messages"])
    finally:
        resumed.close()


def test_team_stop_keeps_failed_claim_for_root_review(tmp_path: Path) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    _add_member(runner, "developer")
    entered, resume = threading.Event(), threading.Event()
    runner.agent_manager.registry = type("Registry", (), {
        "instantiate": lambda self, raw, **kwargs: _BlockingAgent(str(getattr(raw, "name", raw)), entered, resume),
    })()  # type: ignore[assignment]
    manager = runner.team_manager
    assert manager is not None
    runner.activate()
    task = manager.add_task("Block briefly", eligible_members=["developer"])
    assert entered.wait(5.0)
    stopped = threading.Thread(target=runner.stop, kwargs={"reason": "test_stop"})
    stopped.start()
    _wait_for(lambda: runner.cancel_event.is_set())
    resume.set()
    stopped.join(5.0)
    assert not stopped.is_alive()
    current = next(item for item in manager.list_tasks() if item["task_id"] == task["task_id"])
    assert current["status"] == "in_progress"
    assert current["error"]
    assert not any(item["status"] == "pending" for item in manager.list_tasks())
    runner.close()


def test_closing_unstarted_stream_releases_request_gate(tmp_path: Path) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    runner.agent_manager.registry = _Registry()  # type: ignore[assignment]
    try:
        stream = runner.stream("first")
        stream.close()
        assert list(runner.stream("second"))[-1]["kind"] == "round_end"
    finally:
        runner.close()


def test_team_updates_reach_serialized_runner_stream(tmp_path: Path) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    runner.agent_manager.registry = _Registry()  # type: ignore[assignment]
    _add_member(runner, "developer")
    runner.team_manager.stopping = True
    try:
        runner.team_manager.add_task("Queued update", eligible_members=["developer"])
        events = [serialize_runner_stream_event(event) for event in runner.stream("inspect team")]
        update = next(
            event for event in events
            if event["kind"] == "team_update" and event["data"]["update"]["type"] == "task_added"
        )
        assert update["data"]["snapshot"]["tasks"][0]["title"] == "Queued update"
        assert update["data"]["update"]["type"] == "task_added"
    finally:
        runner.close()


def test_live_member_completion_arrives_after_foreground_round(tmp_path: Path) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    _add_member(runner, "developer")
    entered, resume = threading.Event(), threading.Event()

    class _SchedulingRoot(_SubmittedAgent):
        def step(self, step: ActionStep) -> ActionStep:
            self.runner_context.runner.team_manager.add_task("Live task", eligible_members=["developer"])
            return super().step(step)

    class _SchedulingRegistry:
        def instantiate(self, raw: Any, **_kwargs: Any) -> _SubmittedAgent:
            name = str(getattr(raw, "name", raw))
            return _SchedulingRoot(name) if name == "root" else _BlockingAgent(name, entered, resume)

    runner.agent_manager.registry = _SchedulingRegistry()  # type: ignore[assignment]
    events: list[dict[str, Any]] = []
    ambient: list[dict[str, Any]] = []
    unsubscribe = runner.subscribe(ambient.append)
    worker = threading.Thread(target=lambda: events.extend(runner.stream("start team")))
    try:
        worker.start()
        assert entered.wait(5.0)
        worker.join(5.0)
        assert not worker.is_alive()
        assert events[-1]["kind"] == "round_end"
        assert runner.state["status"] == "idle"
        resume.set()
        _wait_for(lambda: any(event.get("data", {}).get("update", {}).get("type") == "task_completed" for event in ambient))
        completed = next(index for index, event in enumerate(ambient) if event.get("data", {}).get("update", {}).get("type") == "task_completed")
        root_end = next(index for index, event in enumerate(ambient) if event.get("kind") == "round_end" and event.get("agent_name") == "root")
        assert completed > root_end
    finally:
        unsubscribe()
        resume.set()
        runner.close()


def test_member_restart_uses_new_session_and_close_requeues_work(tmp_path: Path) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    runner.agent_manager.registry = _Registry()  # type: ignore[assignment]
    runner.activate()
    _add_member(runner, "developer")
    manager = runner.team_manager
    assert manager is not None
    try:
        first = manager.send_message("root", "developer", "First message")
        _wait_for(lambda: not manager.has_pending_work())
        assert runner.restart_team_member("developer")["generation"] == 2
        second = manager.send_message("root", "developer", "Second message")
        _wait_for(lambda: not manager.has_pending_work())
        old = runner.agent_manager.load_snapshot(_member_session_id(runner, "developer", 1))
        new = runner.agent_manager.load_snapshot(_member_session_id(runner, "developer", 2))
        assert old is not None and new is not None
        # 检查新的 ContextTree 结构：将整个 session 转为字符串来查找 message_id
        old_text = str(old["session"])
        new_text = str(new["session"])
        assert first["message_id"] in old_text and first["message_id"] not in new_text
        assert second["message_id"] in new_text and second["message_id"] not in old_text

        with runner.team_manager._dispatch_lock:
            runner.team_manager.stopping = True
            task = manager.add_task("Review me", eligible_members=["developer"])
        assert runner.close_team_member("developer")["status"] == "closed"
        queued = next(item for item in manager.list_tasks() if item["task_id"] == task["task_id"])
        assert queued["status"] == "pending"
        assert queued["eligible_members"] == ["developer"]
    finally:
        runner.close()


def test_team_config_round_trip_requires_team_policy() -> None:
    config = RunnerConfig(
        mode_id="team",
        root_binding=AgentBinding("root", "root", "persistent"),
        member_binding=AgentBinding("developer", "teammate", "persistent"),
        team=TeamRunConfig(auto_dispatch=False),
    )
    assert RunnerConfig.from_dict(config.to_dict()) == config
    old = config.to_dict()
    old.pop("team")
    try:
        RunnerConfig.from_dict(old)
    except ValueError as exc:
        assert "旧 Team 状态不支持迁移" in str(exc)
    else:
        raise AssertionError("Old Team config was accepted")


def test_agents_run_independently_of_async_pool_limit(tmp_path: Path) -> None:
    config = replace(mode_registry.resolve("team"), concurrency_limit=1)
    runner = Runner.create(runner_config=config, base_dir=tmp_path)
    _add_member(runner, "developer")
    _add_member(runner, "researcher")
    entered, resume = threading.Event(), threading.Event()
    started: list[str] = []

    class _PoolRegistry:
        def instantiate(self, raw: Any, **_kwargs: Any) -> _SubmittedAgent:
            name = str(getattr(raw, "name", raw))
            if name == "developer":
                return _BlockingAgent(name, entered, resume)
            started.append(name)
            return _SubmittedAgent(name)

    runner.agent_manager.registry = _PoolRegistry()  # type: ignore[assignment]
    manager = runner.team_manager
    assert manager is not None
    runner.activate()
    first = manager.add_task("Running", eligible_members=["developer"])
    assert entered.wait(5.0)
    second = manager.add_task("Queued", eligible_members=["researcher"])
    _wait_for(lambda: next(item for item in manager.list_tasks() if item["task_id"] == second["task_id"])["status"] == "completed")
    stopped = threading.Thread(target=runner.stop, kwargs={"reason": "stop_queue"})
    stopped.start()
    _wait_for(lambda: runner.cancel_event.is_set())
    resume.set()
    stopped.join(5.0)
    assert not stopped.is_alive()
    assert "researcher" in started
    assert {item["task_id"]: item["status"] for item in manager.list_tasks()} == {
        first["task_id"]: "in_progress", second["task_id"]: "completed",
    }
    assert next(item for item in manager.list_tasks() if item["task_id"] == first["task_id"])["error"]
    runner.close()


def test_member_message_wakes_independent_root_round(tmp_path: Path) -> None:
    runner = Runner.create(runner_config="team", base_dir=tmp_path)
    _add_member(runner, "developer")
    root_calls: list[int] = []

    class _CoordinatingRoot(_SubmittedAgent):
        def step(self, step: ActionStep) -> ActionStep:
            # 使用 ContextTree 的节点数来追踪调用次数
            root_calls.append(len(self.context.nodes))
            if len(root_calls) == 1:
                self.runner_context.runner.team_manager.add_task("Report back", eligible_members=["developer"])
            return super().step(step)

    class _ReplyingMember(_SubmittedAgent):
        def step(self, step: ActionStep) -> ActionStep:
            self.runner_context.runner.team_manager.send_message("developer", "root", "Completed work")
            return super().step(step)

    class _ReplyRegistry:
        def instantiate(self, raw: Any, **_kwargs: Any) -> _SubmittedAgent:
            name = str(getattr(raw, "name", raw))
            return _CoordinatingRoot(name) if name == "root" else _ReplyingMember(name)

    runner.agent_manager.registry = _ReplyRegistry()  # type: ignore[assignment]
    ambient: list[dict[str, Any]] = []
    unsubscribe = runner.subscribe(ambient.append)
    try:
        events = list(runner.stream("start collaboration"))
        _wait_for(lambda: len(root_calls) == 2 and not runner.team_manager.pending_messages("root"))
        root_ends = [event for event in ambient if event.get("kind") == "round_end" and event.get("agent_name") == "root"]
        _wait_for(lambda: len([event for event in ambient if event.get("kind") == "round_end" and event.get("agent_name") == "root"]) == 2)
        assert [event["kind"] for event in events].count("step_end") == 1
        assert len({event["round_id"] for event in ambient if event.get("kind") == "round_end" and event.get("agent_name") == "root"}) == 2
        assert not runner.team_manager.pending_messages("root")
        message_id = runner.team_manager.snapshot()["messages"][0]["message_id"]
        root_snapshot = runner.agent_manager.load_snapshot("root")
        # 检查新的 ContextTree 结构：将整个 session 转为字符串来查找 message_id
        assert message_id in str(root_snapshot["session"])
    finally:
        unsubscribe()
        runner.close()
