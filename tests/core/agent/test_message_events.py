"""Messages become visible after inbox persistence, independently of model work."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import threading

import pytest

from adapters.stdio_gateway.serialization import serialize_runner_stream_event
from tests.core.managers.agents.test_durable_inbox import Registry, attachment, manager_at, wait_for
from tests.core.runner.test_persistent_rounds import _runner


def test_acceptance_event_follows_durable_write_and_deduplicates_retries(tmp_path, monkeypatch):
    events = []
    manager = manager_at(tmp_path, Registry(), events=events)
    root = manager.acquire_root({"name": "root"})
    inbox_path = tmp_path / "agents" / "root" / "inbox.json"
    accepted_on_disk = []

    def observe(_agent, event):
        events.append(deepcopy(event))
        if event["kind"] == "message_received":
            accepted_on_disk.append(json.loads(inbox_path.read_text())["messages"])

    manager.callbacks = replace(manager.callbacks, on_event=observe)
    try:
        original = attachment("message-1")
        receipt = manager.post(root.agent_id, original)
        assert manager.post(root.agent_id, original) == receipt
        notice = [event for event in events if event["kind"] == "message_received"]
        assert len(notice) == 1
        assert notice[0]["round_id"] == receipt["round_id"]
        assert notice[0]["data"]["attachments"][0]["message_id"] == "message-1"
        assert accepted_on_disk[0][0]["attachment"] == notice[0]["data"]["attachments"][0]
        assert root.instance.calls == []

        # A failed durable write cannot fabricate acceptance in the UI.
        def fail_write(*_args):
            raise OSError("inbox unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(manager, "_save_inbox", fail_write)
            with pytest.raises(OSError, match="inbox unavailable"):
                manager.post(root.agent_id, attachment("not-accepted"))
        assert len([event for event in events if event["kind"] == "message_received"]) == 1

        manager.activate()
        wait_for(lambda: root.round_checkpoint.get("status") == "completed")
        manager.post(root.agent_id, original)
        assert len([event for event in events if event["kind"] == "message_received"]) == 1
    finally:
        manager.close()


def test_functional_round_exposes_its_saved_input_without_an_inbox(tmp_path):
    events = []
    manager = manager_at(tmp_path, Registry(), events=events)
    worker = manager.acquire({"name": "worker"}, lifecycle="functional")
    source = attachment("child-input")
    try:
        result = manager.run(worker, "Investigate weather", attachments=[source])
        start = next(event for event in events if event["kind"] == "round_start")
        assert start["round_id"] == result.round_id
        assert start["data"]["input"] == {"task": "Investigate weather", "attachments": [source]}
        wire = serialize_runner_stream_event(start)
        assert json.loads(json.dumps(wire))["data"]["input"] == start["data"]["input"]
        history = manager.describe_sessions(worker.agent_id)["agents"][0]["steps"]
        assert history[0]["task"] == start["data"]["input"]["task"]
        assert history[0]["attachments"] == [source]
        assert not [event for event in events if event["kind"] == "message_received"]
    finally:
        manager.close()


def test_reconnect_snapshot_reads_pending_input_without_execution_or_ack(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def step(_agent, action):
        entered.set()
        assert release.wait(5)
        action.round_outcome = "submitted"
        return action

    events = []
    manager = manager_at(tmp_path, Registry(step), events=events)
    root = manager.acquire_root({"name": "root"})
    try:
        manager.post(root.agent_id, attachment("initial"))
        manager.activate()
        assert entered.wait(5)
        manager.post(root.agent_id, attachment("arrived-while-running"))
        wait_for(lambda: len([event for event in events if event["kind"] == "message_received"]) == 2)
        files_before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
        live = manager.describe_sessions(root.agent_id)["agents"][0]
        cold = manager_at(tmp_path, Registry())
        try:
            restored = cold.describe_sessions(root.agent_id)["agents"][0]
            assert live["pending_messages"] == restored["pending_messages"] == [attachment("arrived-while-running")]
            assert not cold.live_agents
            assert root.instance.calls == [1]
            assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == files_before
            assert not any(value["message_id"] == "arrived-while-running"
                           for entry in live["steps"] for value in entry.get("attachments", []))
        finally:
            cold.close()
    finally:
        release.set()
        manager.close()


@pytest.mark.parametrize("fail", [False, True])
def test_async_completion_is_visible_before_owner_model_finishes(tmp_path, fail):
    collecting, finish_collection = threading.Event(), threading.Event()
    calls = 0

    def step(_agent, _step):
        nonlocal calls
        calls += 1
        if calls == 2:
            collecting.set()
            assert finish_collection.wait(5)

    runner = _runner(tmp_path, step)
    events = []
    receipts_on_disk = []

    def observe(event):
        events.append(deepcopy(event))
        if event["kind"] == "message_received" and event["data"]["attachments"][0]["attachment_type"] == "async_task_notification":
            # Event delivery precedes owner execution and producer ack. The
            # task outcome and inbox receipt must already be durable here.
            registry = json.loads(runner.layout.async_task_registry_path.read_text())
            inbox = json.loads((runner.layout.agents_dir / "root" / "inbox.json").read_text())
            receipts_on_disk.append((registry["async_tasks"][0], inbox["messages"]))

    unsubscribe = runner.subscribe_events(observe)
    try:
        assert runner.run("Start independent work") == "done:root"
        events.clear()

        def background(_task, _path):
            if fail:
                raise RuntimeError("background failed")
            return "background result"

        launched = runner.async_task_manager.launch(
            task_type="local_bash", owner_agent_id="root", owner_agent_name="root",
            description="independent work", runner=background, summary_builder=lambda result: str(result),
        )
        assert collecting.wait(5)
        assert receipts_on_disk
        saved_task, saved_inbox = receipts_on_disk[0]
        status = "failed" if fail else "completed"
        assert saved_task["status"] == status
        assert saved_task["notification"]["state"] == "pending"
        notice = next(event for event in events if event["kind"] == "message_received")
        received = notice["data"]["attachments"][0]
        assert received["payload"]["status"] == status
        assert received["payload"]["async_task_id"] == launched["async_task_id"]
        assert saved_inbox[0]["attachment"] == received
        assert not [event for event in events if event["kind"] == "round_end"]
        assert runner.state["status"] == "running"

        finish_collection.set()
        wait_for(lambda: any(event["kind"] == "round_end" for event in events))
        assert runner.get_async_task(launched["async_task_id"])["notification"]["state"] == "delivered"
        history = runner.describe_agent_sessions("root")["agents"][0]["steps"]
        assert sum(value.get("message_id") == received["message_id"] for entry in history for value in entry.get("attachments", [])) == 1
        assert json.loads((runner.layout.agents_dir / "root" / "inbox.json").read_text())["messages"] == []
    finally:
        finish_collection.set()
        unsubscribe()
        runner.close()
