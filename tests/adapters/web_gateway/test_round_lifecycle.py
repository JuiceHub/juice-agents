"""WebSocket round delivery is independent from foreground request lifetimes."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from adapters.web_gateway.app import WebSocketRuntimeConnection


def test_new_stream_reaches_runner_while_previous_request_waits(monkeypatch):
    connection = WebSocketRuntimeConnection(SimpleNamespace())
    started = asyncio.Barrier(2)
    results = []
    async def worker(command_id, payload):
        # A transport lifetime mutex would prevent the second request from
        # reaching the inbox while the first waits for its round to finish.
        await asyncio.wait_for(started.wait(), 2)
        return {"done": True, "result": {"round_id": command_id, "status": "cancelled" if command_id == "first" else "completed"}}
    async def send_result(command_id, result):
        results.append((command_id, result))
    monkeypatch.setattr(connection, "_run_stream_request", worker)
    monkeypatch.setattr(connection, "_send_result", send_result)
    async def run():
        await asyncio.gather(connection._stream_message("first", {}), connection._stream_message("second", {}))
    asyncio.run(run())
    assert {key: result["result"]["round_id"] for key, result in results} == {"first": "first", "second": "second"}
    assert dict(results)["first"]["result"]["status"] == "cancelled"
    assert dict(results)["second"]["result"]["status"] == "completed"


def test_autonomous_events_use_same_schema_without_request_id(monkeypatch):
    connection = WebSocketRuntimeConnection(SimpleNamespace())
    notifications = []
    monkeypatch.setattr(connection, "_put_threadsafe", notifications.append)
    connection._publish_runner_event({"kind": "round_start", "event_id": "event-1", "runner_id": "runner-1", "agent_id": "agent-1", "round_id": "round-1"})
    assert "id" not in notifications[0]
    assert notifications[0]["payload"]["event_id"] == "event-1"
    assert notifications[0]["payload"]["round_id"] == "round-1"


def test_stream_worker_returns_result_without_event_or_snapshot_duplication(monkeypatch):
    from unittest.mock import MagicMock, AsyncMock
    connection = WebSocketRuntimeConnection(SimpleNamespace())
    result = {"done": True, "result": {"round_id": "round-1", "status": "completed", "output": "done", "reason": "submitted", "error": None}}
    runtime = MagicMock()
    runtime.astream_message = AsyncMock(return_value=result)
    connection.runtime = runtime
    emitted = []
    monkeypatch.setattr(connection, "_put_threadsafe", emitted.append)
    assert asyncio.run(connection._run_stream_request("request-1", {"message": "hello"})) == result
    runtime.describe_agent_sessions.assert_not_called()
    assert emitted == []


def test_rejected_cancellation_keeps_pending_approval(monkeypatch):
    from concurrent.futures import Future
    connection = WebSocketRuntimeConnection(SimpleNamespace())
    future = Future()
    connection.pending_asks["ask-1"] = future
    monkeypatch.setattr(connection, "_handle_sync_command", lambda *args: {"cancelled": False})
    async def send_result(*args):
        return None
    monkeypatch.setattr(connection, "_send_result", send_result)
    asyncio.run(connection._cancel_stream("cancel-1", {"round_id": "round-1"}))
    assert not future.done()
    assert connection.pending_asks["ask-1"] is future


def test_round_cancel_wakes_sync_descendants_but_preserves_background_approval():
    from concurrent.futures import Future
    connection = WebSocketRuntimeConnection(SimpleNamespace())
    root, child, background = Future(), Future(), Future()
    connection.pending_asks = {"root": root, "child": child, "background": background}
    connection.pending_ask_round_ids = {"root": {"root-round"}, "child": {"child-round", "root-round"}, "background": {"background-round"}}
    connection._cancel_pending_asks("cancelled by user", status="cancelled", round_id="root-round")
    assert root.result()["status"] == child.result()["status"] == "cancelled"
    assert not background.done()
    answer = connection._handle_ask_request({"request_id": "late-child", "question": "continue?", "round_id": "child-round", "cancellation_round_id": "root-round"})
    assert answer["status"] == "cancelled"


def test_pending_request_cancel_uses_real_execution_token_without_terminal_event(monkeypatch):
    connection = WebSocketRuntimeConnection(SimpleNamespace())
    connection._stream_request_ids.add("queued")
    results = []
    async def send_result(command_id, result):
        results.append((command_id, result))
    monkeypatch.setattr(connection, "_send_result", send_result)
    emitted = []
    monkeypatch.setattr(connection, "_put_threadsafe", emitted.append)
    asyncio.run(connection._cancel_stream("cancel", {"request_id": "queued"}))
    asyncio.run(connection._cancel_stream("unknown", {"request_id": "missing"}))
    assert results == [("cancel", {"cancelled": True}), ("unknown", {"cancelled": False})]
    assert connection._stream_request_is_cancelled("queued")
    assert emitted == []


def test_answer_and_cancellation_can_complete_an_approval_only_once(monkeypatch):
    from concurrent.futures import Future
    for cancel_first in (False, True):
        connection = WebSocketRuntimeConnection(SimpleNamespace())
        future = Future()
        connection.pending_asks["ask-1"] = future
        connection.pending_ask_round_ids["ask-1"] = {"round-1"}
        responses, errors = [], []
        async def send_result(command_id, result):
            responses.append(result)
        async def send_error(command_id, error):
            errors.append(error)
        monkeypatch.setattr(connection, "_send_result", send_result)
        monkeypatch.setattr(connection, "_send_error", send_error)
        def cancel():
            connection._cancel_pending_asks("cancelled", status="cancelled", round_id="round-1")
        if cancel_first:
            cancel()
        asyncio.run(connection._answer_ask("answer", {"request_id": "ask-1", "response": {"status": "answered"}}))
        if not cancel_first:
            cancel()
        assert future.result()["status"] == ("cancelled" if cancel_first else "answered")
        assert bool(errors) == cancel_first
        assert responses == ([] if cancel_first else [{"accepted": True}])
