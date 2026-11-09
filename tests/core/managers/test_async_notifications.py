"""Functional contracts for the durable async-task notification outbox."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import threading
import time

import pytest

from juice_agents.core.agent.attachments import (
    build_async_task_notification_attachment,
    build_inbox_message_attachment,
    serialize_runtime_attachments_text,
)
from juice_agents.core.managers import AsyncTaskManager


class RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[tuple[str, dict]] = []
        self.fail = fail

    def enqueue(self, owner_agent_id: str, event: dict) -> None:
        self.events.append((owner_agent_id, deepcopy(event)))
        if self.fail:
            raise OSError("inbox unavailable")


def test_recovery_replays_exact_notification_and_acknowledges_stable_id(tmp_path: Path) -> None:
    state_dir = tmp_path / "async"
    sink = RecordingSink()
    manager = AsyncTaskManager(state_dir=state_dir, event_sink=sink, scope_id="run-a")
    try:
        receipt = manager.launch(
            task_type="local_agent", owner_agent_id="root-id", owner_agent_name="root",
            description="Compute", runner=lambda _task, _path: {"answer": 42, "path": Path("artifact")},
            summary_builder=lambda _result: "The full original summary",
            metadata={"max_observation_chars": 314},
        )
        manager.wait_active_async_tasks(timeout_seconds=3)
        assert len(sink.events) == 1
        original = sink.events[0]
        assert original[1]["payload"]["result"] == {"answer": 42, "path": "artifact"}
        persisted = json.loads((state_dir / "registry.json").read_text())
        task = persisted["async_tasks"][0]
        assert task["status"] == "completed"
        assert task["notification"]["event"] == original[1]
        assert manager.get_task(receipt["async_task_id"])["notification"]["state"] == "pending"
    finally:
        manager.release()

    replay = RecordingSink()
    restored = AsyncTaskManager(state_dir=state_dir, event_sink=replay, scope_id="run-a")
    try:
        assert replay.events == [original]
        attachment = build_async_task_notification_attachment(replay.events[0][1])
        assert attachment["message_id"] == original[1]["event_id"]
        assert f'message_id="{attachment["message_id"]}"' in serialize_runtime_attachments_text([attachment])
        restored.mark_notifications_delivered({attachment["message_id"]})
        assert not restored.has_pending_notifications("root-id")
        assert restored.requeue_pending_notifications() == 0
    finally:
        restored.release()


def test_sink_failure_keeps_completed_result_pending_for_retry(tmp_path: Path) -> None:
    sink = RecordingSink(fail=True)
    manager = AsyncTaskManager(state_dir=tmp_path / "async", event_sink=sink)
    try:
        receipt = manager.launch(
            task_type="local_agent", owner_agent_id="root-id", owner_agent_name="root",
            description="Compute", runner=lambda _task, _path: "result",
            summary_builder=lambda _result: "Complete",
        )
        manager.wait_active_async_tasks(timeout_seconds=3)
        task = manager.get_task(receipt["async_task_id"])
        assert task["status"] == "completed"
        assert task["notification"]["delivery_error"] == "inbox unavailable"
        first_event = sink.events[0]
        sink.fail = False
        assert manager.requeue_pending_notifications() == 1
        assert sink.events[-1] == first_event
        assert manager.get_task(receipt["async_task_id"])["status"] == "completed"
    finally:
        manager.release()


def test_cancellation_never_persists_terminal_without_notification(tmp_path: Path) -> None:
    manager = AsyncTaskManager(state_dir=tmp_path / "async")
    started = threading.Event()
    snapshots: list[dict] = []
    persist = manager._persist_locked

    def record_persist() -> dict | None:
        snapshots.append(deepcopy(manager._snapshot_store_locked()))
        return persist()

    manager._persist_locked = record_persist

    def work(task: dict, _path: Path) -> None:
        started.set()
        manager.cancel_event_for(task["async_task_id"]).wait(3)

    try:
        receipt = manager.launch(
            task_type="local_agent", owner_agent_id="root-id", owner_agent_name="root",
            description="Wait", runner=work, summary_builder=lambda _result: "Done",
        )
        assert started.wait(2)
        assert manager.cancel(receipt["async_task_id"])["status"] == "killed"
        terminal = [task for snapshot in snapshots for task in snapshot["async_tasks"] if task["status"] == "killed"]
        assert terminal
        assert all(task["notification"]["event"]["payload"]["status"] == "killed" for task in terminal)
        assert all(task["notification"]["event"]["payload"]["closed_reason"] == "agent_requested" for task in terminal)
    finally:
        manager.release()


def test_team_attachment_preserves_frozen_identity_and_message_id() -> None:
    message = {"message_id": "m-1", "to": "alice", "team_run_id": "run-a", "target_generation": 3, "text": "Hi"}
    attachment = build_inbox_message_attachment(message)
    assert attachment["message_id"] == "m-1"
    assert attachment["payload"] == message


def test_task_assignment_attachment_reaches_model_with_frozen_identity() -> None:
    text = serialize_runtime_attachments_text([{
        "attachment_type": "task_assignment", "message_id": "assignment-1", "created_at": 1.0,
        "payload": {"task_id": "task-1", "text": "Investigate the bug", "team_run_id": "run-a", "generation": 2},
    }])
    assert '<task_assignment message_id="assignment-1" task_id="task-1" team_run_id="run-a" generation="2">' in text
    assert "Investigate the bug" in text


def test_shell_drains_both_output_pipes_before_terminal_notification(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LC_ALL", "C")
    sink = RecordingSink()
    manager = AsyncTaskManager(state_dir=tmp_path / "async", event_sink=sink)
    code = "import sys; sys.stdout.write('o' * 100000); sys.stderr.write('e' * 100000)"
    try:
        receipt = manager.launch_shell(
            owner_agent_id="root-id", owner_agent_name="root",
            command=f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}",
            cwd=tmp_path, timeout_seconds=5,
        )
        assert not manager.wait_active_async_tasks(timeout_seconds=7)
        assert manager.get_task(receipt["async_task_id"])["status"] == "completed"
        result = sink.events[0][1]["payload"]["result"]
        assert result["stdout"] == "o" * 100000
        assert result["stderr"] == "e" * 100000
    finally:
        manager.release()


@pytest.mark.skipif(sys.platform != "linux", reason="Checks the descendant state through /proc")
def test_stopping_shell_cancels_its_descendants_and_preserves_killed_notification(tmp_path: Path) -> None:
    manager = AsyncTaskManager(state_dir=tmp_path / "async")
    ready = tmp_path / "child.pid"
    code = "import os,time; from pathlib import Path; Path('child.pid').write_text(str(os.getpid())); time.sleep(30)"
    child_pid = None
    try:
        receipt = manager.launch_shell(
            owner_agent_id="root-id", owner_agent_name="root",
            # Keep a shell parent as well as a child inheriting both pipes.
            command=f"{shlex.quote(sys.executable)} -c {shlex.quote(code)} & wait",
            cwd=tmp_path, timeout_seconds=10,
        )
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        child_pid = int(ready.read_text())
        assert manager.cancel(receipt["async_task_id"])["status"] == "killed"
        assert not manager.wait_active_async_tasks(timeout_seconds=3)
        child_stat = Path(f"/proc/{child_pid}/stat")
        assert not child_stat.exists() or child_stat.read_text().split(") ", 1)[1].startswith("Z")
        task = manager.get_task(receipt["async_task_id"])
        assert task["notification"]["event"]["payload"]["status"] == "killed"
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        manager.release()
