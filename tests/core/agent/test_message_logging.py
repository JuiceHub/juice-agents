"""Agent file diagnostics follow durable input acceptance without owning it."""

from __future__ import annotations

import json
from types import MethodType

import pytest

from juice_agents.core.agent.agents import MultiStepAgent
from tests.core.managers.agents.test_durable_inbox import Registry, attachment, manager_at, wait_for


def bind_file_log(agent, path):
    """Exercise the actual Agent file writer with the scheduling test double."""
    agent.log_file_path = path
    agent._append_file_log = MethodType(MultiStepAgent._append_file_log, agent)
    agent._pretty_log = MethodType(MultiStepAgent._pretty_log, agent)


def test_received_notification_is_logged_after_save_once_without_claiming_consumption(tmp_path, monkeypatch):
    events = []
    manager = manager_at(tmp_path, Registry(), events=events)
    root = manager.acquire_root({"name": "root"})
    path = tmp_path / "agents" / "root" / "log.txt"
    inbox = path.with_name("inbox.json")
    bind_file_log(root.instance, path)
    saved_at_log = []
    pretty_log = root.instance._pretty_log

    def log_after_save(title, content):
        saved_at_log.append(json.loads(inbox.read_text())["messages"])
        pretty_log(title, content)

    root.instance._pretty_log = log_after_save
    notification = {
        "message_id": "completed-job", "attachment_type": "async_task_notification",
        "payload": {"async_task_id": "weather-job", "status": "completed", "summary": "Weather collected"},
    }
    try:
        receipt = manager.post(root.agent_id, notification)
        saved = json.loads(inbox.read_text())["messages"]
        text = path.read_text()
        assert saved[0]["message_id"] == receipt["message_id"]
        assert saved[0]["status"] == "pending"
        assert saved_at_log == [saved]
        assert "Message received" in text and "awaiting context commit" in text
        assert 'message_id="completed-job"' in text and 'status="completed"' in text
        assert "Weather collected" in text
        assert root.instance.calls == []

        # Neither receipt retries nor a failed write may fabricate log entries.
        manager.post(root.agent_id, notification)
        assert path.read_text() == text

        def fail_save(*_args):
            raise OSError("inbox unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(manager, "_save_inbox", fail_save)
            with pytest.raises(OSError, match="inbox unavailable"):
                manager.post(root.agent_id, attachment("not-accepted"))
        assert path.read_text() == text

        manager.activate()
        wait_for(lambda: root.round_checkpoint.get("status") == "completed")
        manager.post(root.agent_id, notification)
        assert path.read_text() == text
        assert json.loads(inbox.read_text())["messages"] == []
        assert len([event for event in events if event["kind"] == "message_received"]) == 1
    finally:
        manager.close()


def test_log_failure_preserves_message_acceptance_event_and_execution(tmp_path, caplog):
    events = []
    manager = manager_at(tmp_path, Registry(), events=events)
    root = manager.acquire_root({"name": "root"})

    def fail_log(*_args):
        raise OSError("log unavailable")

    root.instance._pretty_log = fail_log
    try:
        receipt = manager.post(root.agent_id, attachment("accepted-despite-log"))
        assert receipt["status"] == "pending"
        assert json.loads((tmp_path / "agents/root/inbox.json").read_text())["messages"][0]["message_id"] == receipt["message_id"]
        assert [event for event in events if event["kind"] == "message_received"]
        assert "agent_input_log_failed" in caplog.text and "log unavailable" in caplog.text
        manager.activate()
        wait_for(lambda: any(event["kind"] == "round_end" for event in events))
        assert root.round_checkpoint["status"] == "completed"
        assert root.instance.calls == [1]
        assert json.loads((tmp_path / "agents/root/inbox.json").read_text())["messages"] == []
    finally:
        manager.close()


def test_functional_saved_task_is_logged_once_and_logging_failure_is_diagnostic(tmp_path, caplog):
    events = []
    manager = manager_at(tmp_path, Registry(), events=events)
    worker = manager.acquire({"name": "worker"}, lifecycle="functional")
    path = tmp_path / "agents" / worker.agent_id / "log.txt"
    bind_file_log(worker.instance, path)
    try:
        result = manager.run(worker, "Investigate weather", attachments=[attachment("child-input")])
        text = path.read_text()
        assert result.status == "completed"
        assert text.count("Task") == 1
        assert "Investigate weather" in text and 'message_id="child-input"' in text
        assert "Message received" not in text
        assert not path.with_name("inbox.json").exists()
        history = manager.describe_sessions(worker.agent_id)["agents"][0]["steps"]
        assert history[0]["task"] == "Investigate weather"

        failing = manager.acquire({"name": "failing-log-worker"}, lifecycle="functional")

        def fail_log(*_args):
            raise OSError("task log unavailable")

        failing.instance._pretty_log = fail_log
        assert manager.run(failing, "Complete despite failed diagnostics").status == "completed"
        assert "task log unavailable" in caplog.text
    finally:
        manager.close()
