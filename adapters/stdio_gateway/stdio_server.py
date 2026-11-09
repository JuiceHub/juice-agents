"""JSON-RPC 2.0 server that exposes the stdio gateway over stdin/stdout."""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
from typing import Any, Optional

from juice_agents.core.runner.types.ask import AskRequest, normalize_ask_response

from .handlers import RpcHandlers
from .runtime import ask_execution_cancelled

logger = logging.getLogger(__name__)

# These commands set durable cancellation intent and a stop signal. They can
# run in the reader while the main command loop processes snapshot requests.
_READER_IMMEDIATE_METHODS = frozenset({"cancel_stream", "interrupt_agent"})


class StdioJsonRpcServer:
    """
    JSON-RPC 2.0 server that reads requests from stdin and writes responses to stdout.

    Protocol:
    - Each request is a single line of JSON.
    - Each response is a single line of JSON.
    - Streaming methods emit notifications without an `id` field.

    Concurrency model:
    - stdin has one reader; ordinary commands have one main-loop consumer.
    - Foreground streams wait in workers so later input reaches the inbox.
    - Ask answers have independent queues keyed by request_id. They can never
      be consumed by the command loop or by another concurrent Ask callback.
    - Runner subscriptions publish autonomous events with no RPC request ID.
    """

    def __init__(self) -> None:
        self.handlers = RpcHandlers(ask_handler=self.handle_ask_request, event_handler=self._publish_runner_event)
        self._command_queue: "queue.Queue[Optional[dict[str, Any]]]" = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._pending_asks: dict[str, queue.Queue[dict[str, Any] | None]] = {}
        self._pending_asks_lock = threading.Lock()
        self._pending_ask_round_ids: dict[str, set[str]] = {}
        self._cancelled_round_ids: set[str] = set()
        self._input_closed = threading.Event()
        self._stream_workers: set[threading.Thread] = set()
        self._stdout_lock = threading.Lock()  # 双工写出时保护 stdout 行原子性
        self._cancel_state_lock = threading.Lock()
        self._cancelled_stream_request_ids: set[str] = set()
        self._stream_request_ids: set[str] = set()

    # ------------------------------------------------------------------ reader

    def _enqueue_command(self, request: Optional[dict[str, Any]]) -> None:
        """Append to the thread-safe FIFO consumed by the main command loop."""
        self._command_queue.put(request)

    def _start_reader(self) -> None:
        """Spawn the stdin reader thread (idempotent)."""
        if self._reader_thread is not None and self._reader_thread.is_alive():
            return
        thread = threading.Thread(target=self._read_stdin_loop, name="stdio-reader", daemon=True)
        self._reader_thread = thread
        thread.start()

    def _read_stdin_loop(self) -> None:
        """Forward stdin requests, dispatching cancel_stream without queue delay."""
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.error("Invalid JSON: %s", exc)
                    self._write_error(None, -32700, "Parse error")
                    continue
                if str(request.get("method") or "") == "answer_ask":
                    self._route_ask_answer(request)
                    continue
                if str(request.get("method") or "") in _READER_IMMEDIATE_METHODS:
                    try:
                        self._handle_request(request)
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.exception("Immediate control command failed")
                        self._write_error(request.get("id"), -32603, f"Internal error: {exc}")
                    continue
                if str(request.get("method") or "") == "stream_message":
                    with self._cancel_state_lock:
                        self._stream_request_ids.add(self._normalize_request_id(request.get("id")))
                self._enqueue_command(request)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("stdio reader thread exited unexpectedly: %s", exc)
        finally:
            self._input_closed.set()
            with self._pending_asks_lock:
                for answers in self._pending_asks.values():
                    answers.put(None)
            self._enqueue_command(None)

    # ---------------------------------------------------------------- main loop

    def run(self) -> None:
        """Process line-delimited JSON-RPC requests until stdin closes."""
        logger.info("stdio JSON-RPC server started")
        self._start_reader()

        try:
            while True:
                request = self._command_queue.get()
                if request is None:
                    break
                try:
                    if request.get("method") == "stream_message":
                        # Waiting for a round never owns the command consumer.
                        # Later input can therefore enter the same durable inbox.
                        worker = threading.Thread(
                            target=self._handle_request, args=(request,),
                            name=f"stdio-stream-{request.get('id')}", daemon=True,
                        )
                        self._stream_workers = {item for item in self._stream_workers if item.is_alive()}
                        self._stream_workers.add(worker)
                        worker.start()
                    else:
                        self._handle_request(request)
                except Exception as exc:
                    logger.exception("Unexpected error handling request")
                    self._write_error(request.get("id"), -32603, f"Internal error: {exc}")
        except KeyboardInterrupt:
            logger.info("Server interrupted")
        except Exception as exc:
            logger.exception("Fatal error in server loop: %s", exc)
        finally:
            try:
                self.handlers.dispatch("stop_session", {"source": "gateway_shutdown"})
            except Exception:
                logger.exception("Failed to stop active session during gateway shutdown")

    def _handle_request(self, request: dict[str, Any]) -> None:
        """Dispatch one JSON-RPC request and write the corresponding response."""
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if not method:
            self._write_error(request_id, -32600, "Invalid Request: missing method")
            return

        try:
            if method == "cancel_stream" and params.get("request_id") and not params.get("round_id"):
                target = self._normalize_request_id(params["request_id"])
                with self._cancel_state_lock:
                    accepted = target in self._stream_request_ids
                    if accepted:
                        self._cancelled_stream_request_ids.add(target)
                self._write_response(request_id, {"cancelled": accepted})
                return
            dispatch_params = params
            if method == "stream_message":
                with self._cancel_state_lock:
                    self._stream_request_ids.add(self._normalize_request_id(request_id))
                dispatch_params = dict(params)
                dispatch_params["_cancel_check"] = (
                    lambda rid=self._normalize_request_id(request_id):
                    self._stream_request_is_cancelled(rid)
                )
            if method in {"stop_session", "start_session", "resume_session"}:
                self._wake_pending_asks(reason="runner stopped or replaced")
            result = self.handlers.dispatch(method, dispatch_params)
            if method == "cancel_stream" and result.get("cancelled"):
                self._record_cancel_target(params)

            self._write_response(request_id, result)
        except Exception as exc:
            logger.exception("Error executing method %s: %s", method, exc)
            self._write_error(request_id, -32603, str(exc))
        finally:
            if method == "stream_message":
                with self._cancel_state_lock:
                    target = self._normalize_request_id(request_id)
                    self._stream_request_ids.discard(target)
                    self._cancelled_stream_request_ids.discard(target)

    @staticmethod
    def _normalize_request_id(request_id: Any) -> str:
        return str(request_id if request_id is not None else "").strip()

    def _record_cancel_target(self, params: dict[str, Any]) -> str:
        """Wake only approvals belonging to an accepted round cancellation."""
        target = self._normalize_request_id(params.get("request_id"))
        runner = getattr(getattr(self.handlers, "_runtime", None), "_runner", None)
        round_id = str(params.get("round_id") or getattr(runner, "active_round_id", "") or "")
        if params.get("round_id") and round_id != str(getattr(runner, "active_round_id", "")):
            return target
        if params.get("runner_id") and str(params["runner_id"]) != str(getattr(runner, "runner_id", "")):
            return target
        with self._cancel_state_lock:
            if target:
                self._cancelled_stream_request_ids.add(target)
        if round_id:
            self._wake_pending_asks(round_id=round_id, reason="cancelled by user")
        return target

    def _wake_pending_asks(self, *, round_id: str | None = None, reason: str) -> None:
        """Root cancellation covers synchronous descendants, never background work."""
        with self._pending_asks_lock:
            if round_id is not None:
                self._cancelled_round_ids.add(round_id)
            for request_id, answers in self._pending_asks.items():
                if round_id is None or round_id in self._pending_ask_round_ids.get(request_id, set()):
                    answers.put({"_cancelled": True, "reason": reason})

    def _stream_request_is_cancelled(self, request_id: str) -> bool:
        with self._cancel_state_lock:
            return request_id in self._cancelled_stream_request_ids

    # ------------------------------------------------------------------- writes

    def _write_response(self, request_id: Any, result: Any) -> None:
        """Write a JSON-RPC success response to stdout."""
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }
        self._write_json(response)

    def _write_error(self, request_id: Any, code: int, message: str) -> None:
        """Write a JSON-RPC error response to stdout."""
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
        self._write_json(response)

    # --------------------------------------------------------------------- ask

    def _publish_runner_event(self, event: dict[str, Any]) -> None:
        """Publish scheduled rounds even when no foreground RPC is waiting."""
        from .serialization import serialize_runner_stream_event
        if event.get("kind") == "round_end":
            with self._pending_asks_lock:
                self._cancelled_round_ids.discard(str(event.get("round_id") or ""))
        self._write_notification(serialize_runner_stream_event(event), request_id=None)

    def _route_ask_answer(self, command: dict[str, Any]) -> None:
        """Deliver answers to their own queue, never to the main command consumer."""
        request_id = str((command.get("params") or {}).get("request_id") or "")
        with self._pending_asks_lock:
            answers = self._pending_asks.get(request_id)
            if answers is not None:
                answers.put(command)
                return
        self._write_error(command.get("id"), -32602, f"unknown ask request: {request_id}")

    def handle_ask_request(self, request: AskRequest) -> dict[str, Any]:
        """Wait on a dedicated Ask queue while the main loop accepts new commands.

        Registration precedes the notification and reader startup, so even an
        immediate answer cannot race with the main command queue. A wrong Ask
        identity receives an error without consuming the pending answer slot.
        """
        request_id = str(request.get("request_id") or "").strip()
        answers: queue.Queue[dict[str, Any] | None] = queue.Queue()
        round_ids = {
            str(request.get(key) or "") for key in ("round_id", "cancellation_round_id")
            if request.get(key)
        }
        with self._pending_asks_lock:
            if round_ids & self._cancelled_round_ids:
                return dict(normalize_ask_response({"status": "cancelled", "error": "cancelled by user"}, request=request))
            self._pending_asks[request_id] = answers
            self._pending_ask_round_ids[request_id] = round_ids
        try:
            self._write_notification(dict(request), method="ask_request", request_id=None)
            self._start_reader()
            while True:
                runner = getattr(getattr(self.handlers, "_runtime", None), "_runner", None)
                if ask_execution_cancelled(runner, request):
                    return dict(normalize_ask_response({"status": "cancelled", "error": "cancelled by user"}, request=request))
                try:
                    command = answers.get(timeout=0.1)
                except queue.Empty:
                    if self._input_closed.is_set():
                        return dict(normalize_ask_response({"status": "error", "error": "ask input stream closed"}, request=request))
                    continue
                if command is None:
                    return dict(normalize_ask_response({"status": "error", "error": "ask input stream closed"}, request=request))
                if command.get("_cancelled"):
                    return dict(normalize_ask_response({"status": "cancelled", "error": "cancelled by user"}, request=request))
                response = normalize_ask_response((command.get("params") or {}).get("response") or {}, request=request)
                self._write_response(command.get("id"), {"accepted": True})
                return dict(response)
        finally:
            with self._pending_asks_lock:
                self._pending_asks.pop(request_id, None)
                self._pending_ask_round_ids.pop(request_id, None)

    def _write_notification(
        self,
        data: Any,
        *,
        method: str = "stream_step",
        request_id: Any = None,
    ) -> None:
        """Publish one notification; autonomous execution has no RPC identity."""
        params = {"request" if method == "ask_request" else "event": data}
        if request_id is not None:
            params["request_id"] = request_id
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write_json(notification)

    def _write_json(self, obj: Any) -> None:
        """Emit one compact JSON line and flush immediately for streaming UX."""
        line = json.dumps(obj, ensure_ascii=False)
        # Subscriptions, Ask and RPC workers write concurrently. Hold the lock
        # over the complete JSON line and flush so protocol frames never mix.
        with self._stdout_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
