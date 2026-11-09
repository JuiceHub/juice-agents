"""Functional adapter contracts for autonomous rounds and independent Ask queues."""
from __future__ import annotations

import threading
from types import SimpleNamespace

from adapters.stdio_gateway.runtime import DirectRunnerRuntime
from adapters.stdio_gateway.serialization import serialize_runner_stream_event
from adapters.stdio_gateway.stdio_server import StdioJsonRpcServer


class RecordingRunner:
    def __init__(self):
        self.runner_id = "runner-1"
        self.active_round_id = "round-1"
        self.calls = []
        self.listener = None

    def set_ask_handler(self, handler):
        self.calls.append("ask_bound")

    def subscribe_events(self, listener):
        self.calls.append("listener_bound")
        self.listener = listener
        return lambda: self.calls.append("unsubscribed")

    def activate(self):
        self.calls.append("activated")
        self.listener({"kind": "round_start", "runner_id": self.runner_id, "round_id": self.active_round_id})

    def cancel_round(self, round_id, *, reason="user_cancelled"):
        self.calls.append(("cancel", round_id))
        return round_id == self.active_round_id

    def close(self):
        self.calls.append("closed")


def runtime_shell(runner, events):
    runtime = object.__new__(DirectRunnerRuntime)
    runtime._runner = runner
    runtime._ask_handler = lambda request: {}
    runtime._event_handler = events.append
    runtime._runner_unsubscribe = None
    return runtime


def test_bind_then_load_snapshot_then_explicit_activation_and_unsubscribe_on_close():
    runner = RecordingRunner()
    events = []
    runtime = runtime_shell(runner, events)
    runtime._bind_runner()
    assert runner.calls == ["ask_bound", "listener_bound"]
    assert events == []
    runner.describe_agent_sessions = lambda *, agent_id=None: {"agents": []}
    assert runtime.describe_agent_sessions() == {"agents": []}
    assert runtime.activate_session("runner-1") == {"runner_id": "runner-1", "activated": True}
    assert runner.calls[-1] == "activated"
    assert events[0]["round_id"] == "round-1"
    old_listener = runner.listener
    runtime.stop_session()
    assert runner.calls[-2:] == ["closed", "unsubscribed"]
    runtime._runner = RecordingRunner()
    old_listener({"kind": "round_end"})
    assert len(events) == 1


def test_cancel_is_scoped_to_concrete_runner_and_round():
    runner = RecordingRunner()
    runtime = runtime_shell(runner, [])
    assert runtime.request_cancel_stream(runner_id="old", round_id="round-1") == {"cancelled": False}
    assert runner.calls == []
    assert runtime.request_cancel_stream(runner_id="runner-1", round_id="old") == {"cancelled": False}
    assert runtime.request_cancel_stream(runner_id="runner-1", round_id="round-1") == {"cancelled": True}
    assert runner.calls == [("cancel", "old"), ("cancel", "round-1")]


def test_serialization_preserves_event_identity_and_incremental_action():
    event = {"kind": "tool_end", "timestamp": 123.5, "runner_id": "runner-1", "agent_id": "agent-1", "agent_name": "root", "agent_role": "root", "round_id": "round-1", "event_id": "event-1", "data": {"call_id": "tool-1", "tool_name": "read", "observation": "result"}}
    serialized = serialize_runner_stream_event(event)
    for key, value in event.items():
        assert serialized[key] == value


def test_ask_answers_never_enter_main_command_queue(monkeypatch):
    server = StdioJsonRpcServer()
    monkeypatch.setattr(server, "_start_reader", lambda: None)
    notifications = []
    ready = threading.Event()
    responses = []
    def notify(data, **kwargs):
        notifications.append((data, kwargs))
        ready.set()
    monkeypatch.setattr(server, "_write_notification", notify)
    monkeypatch.setattr(server, "_write_response", lambda *args: responses.append(args))
    errors = []
    monkeypatch.setattr(server, "_write_error", lambda *args: errors.append(args))
    result = []
    worker = threading.Thread(target=lambda: result.append(server.handle_ask_request({"request_id": "ask-1", "question": "continue?"})), daemon=True)
    worker.start()
    assert ready.wait(1)
    # The command consumer is free to read ordinary input throughout Ask.
    server._enqueue_command({"id": 10, "method": "list_async_tasks"})
    assert server._command_queue.get(timeout=1)["id"] == 10
    server._route_ask_answer({"id": 11, "params": {"request_id": "wrong", "response": {}}})
    assert errors[0][0] == 11
    assert worker.is_alive()
    server._route_ask_answer({"id": 12, "params": {"request_id": "ask-1", "response": {"status": "answered", "custom_response": "yes"}}})
    worker.join(1)
    assert not worker.is_alive()
    assert result[0]["custom_response"] == "yes"
    assert responses == [(12, {"accepted": True})]
    assert server._command_queue.empty()
    assert notifications[0][1]["request_id"] is None


def test_stale_round_cancel_does_not_wake_new_ask():
    server = StdioJsonRpcServer()
    server.handlers._runtime = SimpleNamespace(_runner=SimpleNamespace(runner_id="runner-1", active_round_id="new"))
    server._record_cancel_target({"runner_id": "runner-1", "round_id": "old"})
    assert not server._cancelled_round_ids


def test_request_returns_result_without_republishing_events_or_reading_history():
    runner = RecordingRunner()
    result = {"round_id": "round-1", "status": "completed", "output": "done", "reason": "submitted", "error": None}
    async def run_round(message, *, cancel_check=None):
        assert message == "hello"
        runner.listener({"kind": "round_end", "event_id": "event-1", "data": {"result": result}})
        return SimpleNamespace(to_dict=lambda: result)
    runner.arun_round = run_round
    events = []
    runtime = runtime_shell(runner, events)
    runtime.agent_mode = "agent"
    runtime._bind_runner()
    assert runtime.stream_message("hello") == {"done": True, "result": result}
    assert [event["event_id"] for event in events] == ["event-1"]
    assert "agent_sessions_report" not in events[0]


def test_activation_rejects_old_session_and_history_filter_is_forwarded():
    import pytest
    runner = RecordingRunner()
    runtime = runtime_shell(runner, [])
    runner.describe_agent_sessions = lambda *, agent_id=None: {"selected_agent_id": agent_id}
    assert runtime.describe_agent_sessions("child-1") == {"selected_agent_id": "child-1"}
    with pytest.raises(ValueError, match="no longer active"):
        runtime.activate_session("old-runner")
    assert runner.calls == []


def test_stdio_stream_response_has_no_duplicate_notification(monkeypatch):
    server = StdioJsonRpcServer()
    result = {"done": True, "result": {"round_id": "round-1", "status": "failed", "output": "partial", "reason": "max_steps_exceeded", "error": None}}
    monkeypatch.setattr(server.handlers, "dispatch", lambda method, params: result)
    emitted = []
    monkeypatch.setattr(server, "_write_json", emitted.append)
    server._handle_request({"id": 1, "method": "stream_message", "params": {"message": "hello"}})
    assert emitted == [{"jsonrpc": "2.0", "id": 1, "result": result}]


def test_rejected_cancellation_does_not_wake_approval(monkeypatch):
    server = StdioJsonRpcServer()
    monkeypatch.setattr(server.handlers, "dispatch", lambda method, params: {"cancelled": False})
    monkeypatch.setattr(server, "_write_json", lambda item: None)
    server._handle_request({"id": 2, "method": "cancel_stream", "params": {}})
    assert not server._cancelled_round_ids


def test_round_cancel_wakes_sync_descendants_but_preserves_background_approval():
    import queue
    server = StdioJsonRpcServer()
    root_queue, child_queue, background_queue = queue.Queue(), queue.Queue(), queue.Queue()
    server._pending_asks = {"root": root_queue, "child": child_queue, "background": background_queue}
    server._pending_ask_round_ids = {"root": {"root-round"}, "child": {"child-round", "root-round"}, "background": {"background-round"}}
    server._wake_pending_asks(round_id="root-round", reason="cancelled by user")
    assert root_queue.get_nowait()["_cancelled"]
    assert child_queue.get_nowait()["_cancelled"]
    assert background_queue.empty()
    # Cancellation persisted in the transport closes the registration race.
    answer = server.handle_ask_request({"request_id": "late-child", "question": "continue?", "round_id": "child-round", "cancellation_round_id": "root-round"})
    assert answer["status"] == "cancelled"


def test_real_python_events_and_committed_result_are_single_json_contract(tmp_path, monkeypatch):
    import json
    from juice_agents.core.registry.agents import registry as registry_module
    class Model:
        def generate(self, messages, **kwargs):
            return {"content": '<thought>done</thought><actions>[{"name":"submit_output","args":{"output":"finished"}}]</actions>'}
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **kwargs: Model())
    events = []
    runtime = DirectRunnerRuntime(base_dir=tmp_path, event_handler=events.append)
    try:
        session = runtime.start_session()
        assert runtime.describe_agent_sessions()["agents"] == []
        assert events == []
        runtime.activate_session(session.runner_id)
        response = runtime.stream_message("finish")
        serialized = [serialize_runner_stream_event(event) for event in events]
        assert len({event["event_id"] for event in serialized}) == len(serialized)
        assert all(isinstance(event["timestamp"], (int, float)) for event in serialized)
        assert all(event["runner_id"] == session.runner_id for event in serialized)
        assert all("agent_sessions_report" not in event for event in serialized)
        ending = [event for event in serialized if event["kind"] == "round_end"]
        assert len(ending) == 1 and ending[0]["data"]["result"] == response["result"]
        assert response["result"]["status"] == "completed"
        steps = [event for event in serialized if event["kind"] == "step_end"]
        assert len(steps) == 1
        step = steps[0]["data"]["step"]
        assert step["round_id"] == response["result"]["round_id"]
        assert step["step_id"] and step["tool_calls"][0]["call_id"]
        json.dumps({"events": serialized, "response": response})
    finally:
        runtime.stop_session()


def test_queued_request_cancel_still_commits_real_round_result(tmp_path, monkeypatch):
    import pytest
    from juice_agents.core.registry.agents import registry as registry_module
    class Model:
        def generate(self, *args, **kwargs):
            pytest.fail("A cancelled queued request must not begin model generation")
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **kwargs: Model())
    server = StdioJsonRpcServer()
    emitted = []
    monkeypatch.setattr(server, "_write_json", emitted.append)
    server.handlers.dispatch("start_session", {"base_dir": str(tmp_path)})
    try:
        # The reader registers the request before putting it on the main queue.
        server._stream_request_ids.add("queued")
        server._handle_request({"id": "cancel", "method": "cancel_stream", "params": {"request_id": "queued"}})
        assert emitted[-1]["result"] == {"cancelled": True}
        server._handle_request({"id": "queued", "method": "stream_message", "params": {"message": "never start"}})
        response = next(item["result"] for item in emitted if item.get("id") == "queued")
        endings = [item["params"]["event"] for item in emitted if item.get("method") == "stream_step" and item["params"]["event"]["kind"] == "round_end"]
        assert len(endings) == 1
        assert response["result"] == endings[0]["data"]["result"]
        assert response["result"]["status"] == "cancelled"
        assert "request_id" not in next(item["params"] for item in emitted if item.get("method") == "stream_step")
        assert not server._stream_request_ids and not server._cancelled_stream_request_ids
    finally:
        server.handlers.dispatch("stop_session", {})


def test_close_delivers_committed_terminal_event_before_unsubscribing():
    runner = RecordingRunner()
    events = []
    runtime = runtime_shell(runner, events)
    runtime._bind_runner()
    def close():
        runner.listener({"kind": "round_end", "round_id": "round-1", "data": {"result": {"status": "cancelled"}}})
    runner.close = close
    runtime.stop_session()
    assert events[0]["kind"] == "round_end"
    assert runtime._runner is None
    assert runner.calls[-1] == "unsubscribed"
