"""Persistent message handoff, round recovery and cancellation contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable

import pytest

from juice_agents.core.agent.context import ActionStep, ContextTree, TaskStep
from juice_agents.core.managers.agents import AgentManager, AgentManagerCallbacks, AgentStatus


class ScriptedAgent:
    """Fresh instance whose steps expose concurrency and budget behavior."""

    def __init__(self, name: str, action: Callable | None = None) -> None:
        self.name = name
        self.agent_id = ""
        self.max_steps = 2
        self.context = ContextTree(tree_id=f"test_{name}", system_prompt="Inbox test")
        self.action = action
        self.calls: list[int] = []
        self.cancel_event: threading.Event | None = None

    def step(self, step: ActionStep) -> ActionStep:
        self.calls.append(step.step_num)
        if self.action is not None:
            return self.action(self, step)
        step.round_outcome = "submitted"
        step.output = "Done"
        return step


class Registry:
    def __init__(self, action: Callable | None = None) -> None:
        self.instances: list[ScriptedAgent] = []
        self.action = action

    def instantiate(self, raw: Any, **_kwargs: Any) -> ScriptedAgent:
        agent = ScriptedAgent(str(raw.get("name") if isinstance(raw, dict) else raw), self.action)
        self.instances.append(agent)
        return agent


def manager_at(tmp_path: Path, registry: Registry, *, events: list[dict] | None = None,
               acknowledge: Callable | None = None) -> AgentManager:
    def bind(managed: Any, cancel: threading.Event) -> None:
        managed.instance.cancel_event = cancel

    return AgentManager(
        registry=registry, runner_id="run-a", state_dir=tmp_path,
        callbacks=AgentManagerCallbacks(
            bind_runtime=bind, acknowledge=acknowledge,
            on_event=None if events is None else lambda _managed, event: events.append(deepcopy(event)),
        ),
    )


def attachment(message_id: str) -> dict:
    return {"message_id": message_id, "attachment_type": "agent_user_message", "created_at": 1.0,
            "payload": {"message_id": message_id, "text": message_id, "from": "user"}}


def wait_for(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Agent round did not settle")
        time.sleep(0.005)


def end_events(events: list[dict]) -> list[dict]:
    return [event for event in events if event["kind"] == "round_end"]


def saved_attachments(managed: Any) -> list[dict]:
    return [value for node in managed.context.nodes.values() for value in getattr(node.step, "attachments", [])]


def test_inactive_post_restores_then_activates_once_after_callbacks_bind(tmp_path: Path) -> None:
    first_registry = Registry()
    first = manager_at(tmp_path, first_registry)
    root = first.acquire_root({"name": "root"})
    receipt = first.post(root.agent_id, attachment("message-1"))
    assert first_registry.instances[0].calls == []
    assert receipt["status"] == "pending"
    first.close()

    events: list[dict] = []
    registry = Registry()
    restored = manager_at(tmp_path, registry, events=events)
    try:
        root = restored.acquire_root({"name": "root"})
        assert registry.instances[0].calls == []
        assert not [event for event in events if event["kind"] != "agent_state"]
        restored.activate()
        restored.activate()
        wait_for(lambda: len(end_events(events)) == 1)
        assert registry.instances[0].calls == [1]
        assert end_events(events)[0]["round_id"] == receipt["round_id"]
        assert [value["message_id"] for value in saved_attachments(root)] == ["message-1"]
        assert restored.post(root.agent_id, attachment("message-1"))["status"] == "committed"
        assert json.loads((tmp_path / "agents" / "root" / "inbox.json").read_text())["messages"] == []
    finally:
        restored.close()


def test_ack_failure_recovery_retries_handoff_without_duplicate_context_or_round(tmp_path: Path) -> None:
    events: list[dict] = []

    def failed_ack(_managed: Any, _attachments: list[dict]) -> None:
        raise OSError("producer unavailable")

    first = manager_at(tmp_path, Registry(), events=events, acknowledge=failed_ack)
    root = first.acquire_root({"name": "root"})
    first.post(root.agent_id, attachment("message-1"))
    first.activate()
    wait_for(lambda: len(end_events(events)) == 1)
    assert len(json.loads((tmp_path / "agents" / "root" / "inbox.json").read_text())["messages"]) == 1
    first.close()

    acknowledged: list[str] = []
    replay_events: list[dict] = []
    registry = Registry()
    restored = manager_at(tmp_path, registry, events=replay_events,
                          acknowledge=lambda _managed, values: acknowledged.extend(value["message_id"] for value in values))
    try:
        root = restored.acquire_root({"name": "root"})
        restored.activate()
        assert acknowledged == ["message-1"]
        assert registry.instances[0].calls == []
        assert end_events(replay_events) == []
        assert [value["message_id"] for value in saved_attachments(root)] == ["message-1"]
        assert json.loads((tmp_path / "agents" / "root" / "inbox.json").read_text())["messages"] == []
    finally:
        restored.close()


def test_active_checkpoint_resumes_input_and_remaining_budget(tmp_path: Path) -> None:
    seed = manager_at(tmp_path, Registry())
    root = seed.acquire_root({"name": "root"})
    root.context.append_step(TaskStep(task="Original input"))
    input_node = root.context.active_leaf_ids[0]
    root.context.append_step(ActionStep(step_num=1, round_outcome="continue", model_output="First step"))
    root.round_checkpoint = {"round_id": "round-active", "status": "active", "input_node_id": input_node,
                             "next_step": 2, "max_steps": 2, "started_at": 1.0}
    seed.persist(root)
    # No seed worker was started; load the last disk checkpoint as a new owner.
    def continue_until_limit(_agent: Any, step: ActionStep) -> ActionStep:
        step.round_outcome = "submitted" if step.step_num == 3 else "continue"
        return step

    events: list[dict] = []
    registry = Registry(continue_until_limit)
    restored = manager_at(tmp_path, registry, events=events)
    try:
        root = restored.acquire_root({"name": "root"})
        registry.instances[0].max_steps = 100
        restored.activate()
        wait_for(lambda: len(end_events(events)) == 1)
        assert registry.instances[0].calls == [2, 3]
        assert [step.task for step in root.context.steps if isinstance(step, TaskStep)] == [
            "Original input", "[runtime] remaining_steps=0",
        ]
        assert root.round_checkpoint["input_node_id"] == input_node
        assert root.round_checkpoint["round_id"] == "round-active"
        assert root.round_checkpoint["status"] == "completed"
        assert next(event for event in events if event["kind"] == "round_start")["data"]["resumed"] is True
    finally:
        restored.close()


def test_final_commit_recovers_without_old_round_and_repairs_deferred_id(tmp_path: Path) -> None:
    seed = manager_at(tmp_path, Registry())
    root = seed.acquire_root({"name": "root"})
    seed.run(root, "Original input", round_id="round-finished")
    old_checkpoint = deepcopy(root.round_checkpoint)
    # Exit after terminal session commit but before pending inbox reassignment.
    seed._save_inbox(root.agent_id, [{"message_id": "deferred", "round_id": "round-finished",
                                    "status": "pending", "attachment": attachment("deferred")}])
    seed.close()

    events: list[dict] = []
    registry = Registry()
    restored = manager_at(tmp_path, registry, events=events)
    try:
        root = restored.acquire_root({"name": "root"})
        assert root.round_checkpoint == old_checkpoint
        restored.activate()
        wait_for(lambda: len(end_events(events)) == 1)
        assert registry.instances[0].calls == [1]
        assert end_events(events)[0]["round_id"] != "round-finished"
        deferred = next(event for event in events if event["kind"] == "message_deferred")
        assert deferred["data"]["previous_round_id"] == "round-finished"
        assert deferred["round_id"] == root.round_checkpoint["round_id"]
        assert len([step for step in root.context.steps if isinstance(step, TaskStep)]) == 2
    finally:
        restored.close()


def test_messages_during_final_step_defer_to_one_serial_round(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()
    events: list[dict] = []
    concurrent = 0
    peak = 0
    guard = threading.Lock()

    def action(agent: Any, step: ActionStep) -> ActionStep:
        nonlocal concurrent, peak
        with guard:
            concurrent += 1
            peak = max(peak, concurrent)
        try:
            if len(agent.calls) == 1:
                entered.set()
                assert release.wait(3)
            step.round_outcome = "submitted"
            return step
        finally:
            with guard:
                concurrent -= 1

    registry = Registry(action)
    manager = manager_at(tmp_path, registry, events=events)
    root = manager.acquire_root({"name": "root"})
    original = manager.post(root.agent_id, attachment("initial"))
    manager.activate()
    try:
        assert entered.wait(2)
        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(lambda index: manager.post(root.agent_id, attachment(f"m-{index}")), range(12)))
        assert {receipt["round_id"] for receipt in receipts} == {original["round_id"]}
        release.set()
        wait_for(lambda: len(end_events(events)) == 2)
        assert registry.instances[0].calls == [1, 1]
        assert peak == 1
        assert len({event["round_id"] for event in end_events(events)}) == 2
        deferred = [event for event in events if event["kind"] == "message_deferred"]
        assert len(deferred) == 12
        assert len({event["round_id"] for event in deferred}) == 1
        assert {value["message_id"] for value in saved_attachments(root)} == {"initial", *(f"m-{index}" for index in range(12))}
    finally:
        release.set()
        manager.close()


def test_distinct_agents_execute_parallel_rounds(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)

    def action(_agent: Any, step: ActionStep) -> ActionStep:
        barrier.wait(timeout=2)
        step.round_outcome = "submitted"
        return step

    events: list[dict] = []
    manager = manager_at(tmp_path, Registry(action), events=events)
    try:
        alice = manager.acquire({"name": "alice"}, lifecycle="persistent")
        bob = manager.acquire({"name": "bob"}, lifecycle="persistent")
        manager.post(alice.agent_id, attachment("alice-input"))
        manager.post(bob.agent_id, attachment("bob-input"))
        manager.activate()
        wait_for(lambda: len(end_events(events)) == 2)
        assert alice.round_checkpoint["status"] == bob.round_checkpoint["status"] == "completed"
    finally:
        manager.close()


def test_cancelled_round_stays_terminal_and_new_message_wakes(tmp_path: Path) -> None:
    entered = threading.Event()

    def action(agent: Any, step: ActionStep) -> ActionStep:
        if len(agent.calls) == 1:
            entered.set()
            assert agent.cancel_event.wait(2)
            raise RuntimeError("provider cancelled")
        step.round_outcome = "submitted"
        return step

    events: list[dict] = []
    registry = Registry(action)
    manager = manager_at(tmp_path, registry, events=events)
    root = manager.acquire_root({"name": "root"})
    cancelled = manager.post(root.agent_id, attachment("cancel-me"))
    manager.activate()
    try:
        assert entered.wait(2)
        assert manager.cancel_round(cancelled["round_id"])
        assert manager.load_snapshot(root.agent_id)["session"]["round_checkpoint"]["cancel_requested"] is True
        wait_for(lambda: len(end_events(events)) == 1)
        assert root.status is AgentStatus.IDLE
        assert not manager.cancel_round(cancelled["round_id"])
        next_receipt = manager.post(root.agent_id, attachment("new-input"))
        assert next_receipt["round_id"] != cancelled["round_id"]
        wait_for(lambda: len(end_events(events)) == 2)
        assert root.round_checkpoint["status"] == "completed"
        assert not manager.cancel_round(cancelled["round_id"])
        assert registry.instances[0].calls == [1, 1]
    finally:
        manager.close()
    resumed_registry = Registry()
    restored = manager_at(tmp_path, resumed_registry)
    try:
        restored.acquire_root({"name": "root"})
        restored.activate()
        assert resumed_registry.instances[0].calls == []
    finally:
        restored.close()


def test_post_rejects_unknown_and_functional_targets(tmp_path: Path) -> None:
    manager = manager_at(tmp_path, Registry())
    try:
        with pytest.raises(KeyError):
            manager.post("missing", attachment("message-1"))
        functional = manager.acquire({"name": "worker"}, lifecycle="functional")
        with pytest.raises(ValueError, match="functional"):
            manager.post(functional.agent_id, attachment("message-2"))
    finally:
        manager.close()


def test_cancelling_next_input_does_not_replace_unwinding_round_checkpoint(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()

    def action(agent: Any, step: ActionStep) -> ActionStep:
        if len(agent.calls) == 1:
            entered.set()
            assert release.wait(3)
        step.round_outcome = "submitted"
        return step

    events: list[dict] = []
    registry = Registry(action)
    manager = manager_at(tmp_path, registry, events=events)
    root = manager.acquire_root({"name": "root"})
    first = manager.post(root.agent_id, attachment("first"))
    manager.activate()
    try:
        assert entered.wait(2)
        assert manager.cancel_round(first["round_id"])
        second = manager.post(root.agent_id, attachment("second"))
        assert second["round_id"] != first["round_id"]
        assert manager.cancel_round(second["round_id"])
        assert root.round_checkpoint["round_id"] == first["round_id"]
        cancelled = json.loads((tmp_path / "agents" / "root" / "inbox.json").read_text())["messages"]
        assert cancelled[0]["status"] == "cancelled"
        release.set()
        wait_for(lambda: len(end_events(events)) == 2)
        assert [(event["round_id"], event["data"]["result"]["status"]) for event in end_events(events)] == [
            (first["round_id"], "cancelled"), (second["round_id"], "cancelled"),
        ]
        assert registry.instances[0].calls == [1]
        manager.post(root.agent_id, attachment("third"))
        wait_for(lambda: len(end_events(events)) == 3)
        assert end_events(events)[-1]["data"]["result"]["status"] == "completed"
    finally:
        release.set()
        manager.close()


def test_pending_cancel_intent_survives_crash_before_context_handoff(tmp_path: Path, monkeypatch) -> None:
    seed = manager_at(tmp_path, Registry())
    root = seed.acquire_root({"name": "root"})
    receipt = seed.post(root.agent_id, attachment("cancel-before-crash"))

    def crash_before_handoff(*_args: Any) -> None:
        raise OSError("context store unavailable")

    monkeypatch.setattr(seed, "_complete_cancelled_input", crash_before_handoff)
    with pytest.raises(OSError, match="context store unavailable"):
        seed.cancel_round(receipt["round_id"])
    seed.close()
    events: list[dict] = []
    registry = Registry()
    restored = manager_at(tmp_path, registry, events=events)
    try:
        root = restored.acquire_root({"name": "root"})
        restored.activate()
        wait_for(lambda: len(end_events(events)) == 1)
        assert end_events(events)[0]["round_id"] == receipt["round_id"]
        assert end_events(events)[0]["data"]["result"]["status"] == "cancelled"
        assert registry.instances[0].calls == []
        assert root.status is AgentStatus.IDLE
        assert [value["message_id"] for value in saved_attachments(root)] == ["cancel-before-crash"]
    finally:
        restored.close()


def test_failed_inbox_write_never_acknowledges_or_wakes(tmp_path: Path, monkeypatch: Any) -> None:
    events: list[dict] = []
    registry = Registry()
    manager = manager_at(tmp_path, registry, events=events)
    try:
        root = manager.acquire_root({"name": "root"})
        save = manager._save_inbox

        def fail_write(_agent_id: str, _entries: list[dict]) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(manager, "_save_inbox", fail_write)
        with pytest.raises(OSError, match="disk full"):
            manager.post(root.agent_id, attachment("retry-me"))
        manager.activate()
        assert registry.instances[0].calls == []
        assert not [event for event in events if event["kind"] != "agent_state"]
        monkeypatch.setattr(manager, "_save_inbox", save)
        receipt = manager.post(root.agent_id, attachment("retry-me"))
        assert receipt["status"] == "pending"
        wait_for(lambda: len(end_events(events)) == 1)
        assert registry.instances[0].calls == [1]
    finally:
        manager.close()


def test_failed_context_write_cannot_acknowledge_in_memory_message(tmp_path: Path, monkeypatch) -> None:
    events: list[dict] = []
    acknowledged: list[str] = []
    manager = manager_at(tmp_path, Registry(), events=events,
                         acknowledge=lambda _item, values: acknowledged.extend(value["message_id"] for value in values))
    root = manager.acquire_root({"name": "root"})
    save = manager.snapshot_store.save

    def fail_save(*_args: Any) -> None:
        raise OSError("session disk full")

    try:
        manager.post(root.agent_id, attachment("must-not-ack"))
        monkeypatch.setattr(manager.snapshot_store, "save", fail_save)
        manager.activate()
        wait_for(lambda: not manager._workers)
        assert end_events(events) == []  # failed storage cannot authorize a terminal event
        assert acknowledged == []
        assert manager.load_snapshot(root.agent_id)["session"]["committed_message_ids"] == []
        with pytest.raises(OSError, match="session disk full"):
            manager.activate()
        assert acknowledged == []
        monkeypatch.setattr(manager.snapshot_store, "save", save)
        manager.activate()
        assert acknowledged == ["must-not-ack"]
        assert manager.load_snapshot(root.agent_id)["session"]["committed_message_ids"] == ["must-not-ack"]
    finally:
        monkeypatch.setattr(manager.snapshot_store, "save", save)
        manager.close()


@pytest.mark.parametrize("terminal", ["completed", "cancelled", "failed"])
def test_terminal_checkpoint_without_pending_input_is_never_resumed(tmp_path: Path, terminal: str) -> None:
    seed = manager_at(tmp_path, Registry())
    root = seed.acquire_root({"name": "root"})
    seed.run(root, "Original input")
    root.round_checkpoint["status"] = terminal
    seed.persist(root)
    seed.close()
    events: list[dict] = []
    registry = Registry()
    restored = manager_at(tmp_path, registry, events=events)
    try:
        root = restored.acquire_root({"name": "root"})
        restored.activate()
        assert registry.instances[0].calls == []
        assert not [event for event in events if event["kind"] != "agent_state"]
        assert root.round_checkpoint["status"] == terminal
    finally:
        restored.close()


def test_cancel_before_activation_persists_input_without_running_it(tmp_path: Path) -> None:
    events: list[dict] = []
    registry = Registry()
    manager = manager_at(tmp_path, registry, events=events)
    try:
        root = manager.acquire_root({"name": "root"})
        queued = manager.post(root.agent_id, attachment("queued"))
        assert manager.cancel_round(queued["round_id"])
        saved = manager.load_snapshot(root.agent_id)
        assert saved["session"]["round_checkpoint"]["status"] == "cancelled"
        manager.activate()
        assert registry.instances[0].calls == []
        assert [value["message_id"] for value in saved_attachments(root)] == ["queued"]
        fresh = manager.post(root.agent_id, attachment("fresh"))
        assert fresh["round_id"] != queued["round_id"]
        wait_for(lambda: len(registry.instances[0].calls) == 1)
        wait_for(lambda: root.round_checkpoint["status"] == "completed")
    finally:
        manager.close()


def test_functional_child_provider_observes_parent_round_cancellation(tmp_path: Path) -> None:
    entered = threading.Event()
    parent_cancel = threading.Event()

    def action(agent: Any, step: ActionStep) -> ActionStep:
        entered.set()
        assert agent.cancel_event.wait(2)
        raise RuntimeError("provider saw caller cancellation")

    manager = manager_at(tmp_path, Registry(action))
    child = manager.acquire({"name": "child"}, lifecycle="functional", external_cancel=parent_cancel)
    errors: list[Exception] = []

    def run_child() -> None:
        errors.append(manager.run(child, "Synchronous work"))

    worker = threading.Thread(target=run_child)
    worker.start()
    try:
        assert entered.wait(2)
        parent_cancel.set()
        worker.join(3)
        assert not worker.is_alive()
        assert len(errors) == 1
        assert manager.load_snapshot(child.agent_id)["session"]["round_checkpoint"]["status"] == "cancelled"
    finally:
        parent_cancel.set()
        worker.join(3)
        manager.close()


def test_running_user_input_is_visible_at_next_boundary_with_images(tmp_path: Path) -> None:
    from juice_agents.core.agent.agent_type import ObservationImage

    entered, release = threading.Event(), threading.Event()
    seen: list[str] = []

    def action(agent: Any, step: ActionStep) -> ActionStep:
        if step.step_num == 1:
            entered.set()
            assert release.wait(2)
            step.round_outcome = "continue"
        else:
            seen.append(json.dumps(agent.context.to_messages(), ensure_ascii=False))
            step.round_outcome = "submitted"
        return step

    manager = manager_at(tmp_path, Registry(action))
    root = manager.acquire_root({"name": "root"})
    events: list[dict] = []
    manager.callbacks = AgentManagerCallbacks(
        on_event=lambda _managed, event: events.append(event),
    )
    manager.post(root.agent_id, attachment("initial"))
    manager.activate()
    try:
        assert entered.wait(2)
        manager.post(root.agent_id, {
            "attachment_type": "user_message", "message_id": "follow-up",
            "payload": {"text": "Inspect the new screenshot", "task_images": [
                ObservationImage(image_url="https://example.test/screenshot.png"),
            ]},
        })
        release.set()
        wait_for(lambda: len(end_events(events)) == 1)
        assert "Inspect the new screenshot" in seen[0]
        assert "https://example.test/screenshot.png" in seen[0]
        assert '"type": "image_url"' in seen[0]
    finally:
        release.set()
        manager.close()


def test_producer_ack_can_post_follow_up_without_losing_it(tmp_path: Path) -> None:
    events: list[dict] = []
    posted = False

    def acknowledge(managed: Any, values: list[dict]) -> None:
        nonlocal posted
        if values[0]["message_id"] == "first" and not posted:
            posted = True
            manager.post(managed.agent_id, attachment("follow-up"))

    manager = manager_at(tmp_path, Registry(), events=events, acknowledge=acknowledge)
    root = manager.acquire_root({"name": "root"})
    try:
        manager.post(root.agent_id, attachment("first"))
        manager.activate()
        wait_for(lambda: bool(end_events(events)))
        assert {value["message_id"] for value in saved_attachments(root)} == {"first", "follow-up"}
        assert "follow-up" in root.committed_message_ids
    finally:
        manager.close()
