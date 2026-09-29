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

logger = logging.getLogger(__name__)

# 控制类命令：streaming 期间允许并发分发，仅做信号设置，不与生成器竞争。
_CONTROL_COMMAND_METHODS = frozenset({"cancel_stream", "stop_session", "send_agent_message", "interrupt_agent"})
# reader 线程可直接处理的控制命令必须只做轻量协作标记，不能重配 runner。
_READER_IMMEDIATE_METHODS = frozenset({"cancel_stream", "interrupt_agent"})
# 这些检查命令只在主线程的 stream yield / Ask 等待边界优先执行。即使方法本身
# 主要读取状态，也不能放进 reader 快速路径与正在运行的 Runner 生成器并发。
_STREAM_SAFE_INSPECTION_METHODS = frozenset({
    "list_async_tasks",
    "read_async_task_output",
    "permission_status",
})


class StdioJsonRpcServer:
    """
    JSON-RPC 2.0 server that reads requests from stdin and writes responses to stdout.

    Protocol:
    - Each request is a single line of JSON.
    - Each response is a single line of JSON.
    - Streaming methods emit notifications without an `id` field.

    Concurrency model:
    - 一个独立的 reader 线程持续读 stdin，把每行 JSON 投入 `_command_queue`。
    - 主线程从队列取命令派发；streaming 生成器迭代期间在每个 yield 之间调用
      `_drain_priority_commands` 处理控制、检查和安全 mode 命令，保证它们在
      step 边界生效而不与 Runner 生成器并发。
    - `handle_ask_request` 同步等待 answer_ask 时也执行边界安全命令；普通命令与
      涉及 Plan 的 mode 请求暂存，Ask 结束后按原 FIFO 顺序恢复。
    """

    def __init__(self) -> None:
        self.handlers = RpcHandlers(ask_handler=self.handle_ask_request)
        self._command_queue: "queue.Queue[Optional[dict[str, Any]]]" = queue.Queue()
        # drain 需要暂时取出普通命令再按原序放回。reader 的所有入队都
        # 经过同一把锁，避免重排期间新命令插入并跑到更早的 FIFO 命令前面。
        self._command_enqueue_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._stdout_lock = threading.Lock()  # 双工写出时保护 stdout 行原子性
        # cancel_stream 由 reader 线程直接派发（不经过队列），通过此 Event 通知
        # 正在阻塞等待 answer_ask 的 handle_ask_request 退出。
        self._ask_cancel_event = threading.Event()
        self._cancel_state_lock = threading.Lock()
        self._cancelled_stream_request_ids: set[str] = set()
        # Main-loop request whose generator is currently being consumed. Ask
        # callbacks execute synchronously inside that generator and reuse it.
        self._active_stream_request_id: Any = None
        # 同一个 Plan 转换可能跨过多个 stream step；每个请求只记录一次延迟日志，
        # 避免长 stream 产生无意义的高频 debug 输出。
        self._logged_deferred_plan_requests: set[str] = set()

    # ------------------------------------------------------------------ reader

    def _enqueue_command(self, request: Optional[dict[str, Any]]) -> None:
        """Append one command while preserving FIFO against priority queue rewrites."""

        with self._command_enqueue_lock:
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
                if str(request.get("method") or "") in _READER_IMMEDIATE_METHODS:
                    try:
                        self._handle_request(request)
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.exception("Immediate control command failed")
                        self._write_error(request.get("id"), -32603, f"Internal error: {exc}")
                    continue
                self._enqueue_command(request)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("stdio reader thread exited unexpectedly: %s", exc)
        finally:
            # Sentinel: 主循环据此退出。
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

        if method in {"switch_permission_mode", "switch_agent_mode"}:
            self._logged_deferred_plan_requests.discard(
                self._normalize_request_id(request_id)
            )

        if method == "cancel_stream":
            self._record_cancel_target(params)

        if method == "stream_message":
            stream_request_id = self._normalize_request_id(request_id)
            with self._cancel_state_lock:
                if stream_request_id in self._cancelled_stream_request_ids:
                    self._cancelled_stream_request_ids.discard(stream_request_id)
                    self._write_response(
                        request_id,
                        {"done": True, "stopped": True, "stop_reason": "user_cancelled"},
                    )
                    logger.info("skip cancelled queued stdio stream: request_id=%s", stream_request_id)
                    return
                # Reset the legacy wake event once per new request, never at Ask entry.
                self._ask_cancel_event.clear()

        try:
            dispatch_params = params
            if method == "stream_message":
                dispatch_params = dict(params)
                dispatch_params["_cancel_check"] = (
                    lambda rid=self._normalize_request_id(request_id):
                    self._stream_request_is_cancelled(rid)
                )
            result = self.handlers.dispatch(method, dispatch_params)

            # Streaming RPC methods yield multiple notifications and end with one response.
            if hasattr(result, "__iter__") and not isinstance(result, (str, dict, list)):
                previous_request_id = self._active_stream_request_id
                self._active_stream_request_id = request_id
                try:
                    for item in result:
                        self._write_notification(item, request_id=request_id)
                        # 控制、只读检查和安全 mode 切换都只在主线程 step 边界执行。
                        self._drain_priority_commands()
                finally:
                    self._active_stream_request_id = previous_request_id
                    with self._cancel_state_lock:
                        self._cancelled_stream_request_ids.discard(
                            self._normalize_request_id(request_id)
                        )
                stopped = self._was_stream_cancelled()
                self._write_response(
                    request_id,
                    {"done": True, "stopped": stopped, "stop_reason": self._stream_stop_reason()},
                )
            else:
                self._write_response(request_id, result)
        except Exception as exc:
            logger.exception("Error executing method %s: %s", method, exc)
            self._write_error(request_id, -32603, str(exc))

    def _mode_switch_is_step_safe(self, request: dict[str, Any]) -> bool:
        """Mode/config changes replace Manager composition after the stream ends."""

        method = str(request.get("method") or "")
        if method not in {"switch_permission_mode", "switch_agent_mode"}:
            return False
        params = request.get("params") or {}
        target_mode = str(params.get("agent_mode") or "").strip().lower() if isinstance(params, dict) else ""
        runtime = getattr(self.handlers, "_runtime", None)
        current_mode = str(getattr(runtime, "agent_mode", "") or "").strip().lower()
        request_key = self._normalize_request_id(request.get("id"))
        if request_key and request_key not in self._logged_deferred_plan_requests:
            self._logged_deferred_plan_requests.add(request_key)
            logger.debug(
                "defer agent_mode transition until stream round ends: "
                "request_id=%s current=%s target=%s",
                request_key,
                current_mode,
                target_mode,
            )
        return False

    def _is_stream_priority_command(self, request: dict[str, Any]) -> bool:
        """Classify commands that may overtake normal FIFO work at a safe boundary."""

        method = str(request.get("method") or "")
        return (
            method in _CONTROL_COMMAND_METHODS
            or method in _STREAM_SAFE_INSPECTION_METHODS
            or self._mode_switch_is_step_safe(request)
        )

    def _drain_priority_commands(self) -> None:
        """Dispatch boundary-safe commands and preserve all ordinary commands in FIFO.

        Queue rewriting happens under ``_command_enqueue_lock`` so a concurrent reader
        or watcher cannot insert a newer command ahead of buffered older commands.
        Handlers are dispatched only after releasing the lock because a handler may
        synchronously enter Ask and need the reader to enqueue ``answer_ask``.
        """

        priority: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []
        saw_sentinel = False
        with self._command_enqueue_lock:
            while True:
                try:
                    request = self._command_queue.get_nowait()
                except queue.Empty:
                    break
                if request is None:
                    saw_sentinel = True
                    continue
                if self._is_stream_priority_command(request):
                    priority.append(request)
                else:
                    deferred.append(request)
            for request in deferred:
                self._command_queue.put(request)
            # Sentinel 必须始终排在保留的普通命令之后，否则 stdin 关闭时主循环会
            # 提前退出并遗失 sentinel 之前已经收到的请求。
            if saw_sentinel:
                self._command_queue.put(None)

        for request in priority:
            logger.debug(
                "dispatch stdio priority command at stream boundary: method=%s request_id=%s",
                request.get("method"),
                request.get("id"),
            )
            self._handle_request(request)

    def _drain_control_commands(self) -> None:
        """Compatibility wrapper for existing callers and control-command tests."""

        self._drain_priority_commands()

    def _restore_deferred_commands(self, deferred: list[dict[str, Any]]) -> None:
        """Put Ask-deferred commands back ahead of commands that arrived later."""

        if not deferred:
            return
        trailing: list[dict[str, Any]] = []
        saw_sentinel = False
        with self._command_enqueue_lock:
            while True:
                try:
                    request = self._command_queue.get_nowait()
                except queue.Empty:
                    break
                if request is None:
                    saw_sentinel = True
                else:
                    trailing.append(request)
            for request in [*deferred, *trailing]:
                self._command_queue.put(request)
            if saw_sentinel:
                self._command_queue.put(None)

    def _was_stream_cancelled(self) -> bool:
        """Best-effort 读出 runtime 上一次 stream 是否被用户取消。"""
        runtime = getattr(self.handlers, "_runtime", None)
        runner = getattr(runtime, "_runner", None) if runtime is not None else None
        getter = getattr(runner, "last_stream_was_cancelled", None) if runner is not None else None
        if not callable(getter):
            return False
        try:
            return bool(getter())
        except Exception:
            return False

    def _stream_stop_reason(self) -> str:
        """Best-effort 读出 runtime 上一次 stream 的取消原因。"""

        runtime = getattr(self.handlers, "_runtime", None)
        runner = getattr(runtime, "_runner", None) if runtime is not None else None
        reason = getattr(runner, "stop_reason", None) if runner is not None else None
        return str(reason or "")

    @staticmethod
    def _normalize_request_id(request_id: Any) -> str:
        return str(request_id if request_id is not None else "").strip()

    def _record_cancel_target(self, params: dict[str, Any]) -> str:
        """Persist request-scoped cancellation before Ask/stream registration can race."""
        target = self._normalize_request_id(params.get("request_id"))
        with self._cancel_state_lock:
            if target:
                self._cancelled_stream_request_ids.add(target)
            active = self._normalize_request_id(self._active_stream_request_id)
            if not target or target == active:
                self._ask_cancel_event.set()
        return target

    def _active_stream_is_cancelled(self) -> bool:
        active = self._normalize_request_id(self._active_stream_request_id)
        with self._cancel_state_lock:
            return bool(active and active in self._cancelled_stream_request_ids)

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

    def handle_ask_request(self, request: AskRequest) -> dict[str, Any]:
        """
        Emit an ask_request notification and synchronously wait for answer_ask.

        新模型下 stdin 由 reader 线程持续投递到队列；此处阻塞地从队列取下一条
        命令：遇到 answer_ask 即返回响应；遇到 cancel_stream 以 cancelled 退出；
        遇到 stop_session 立即派发后继续等待；其它命令在 ask 等待期间不被支持，回错误。

        ask 既可能在 server.run() 已启动 reader 后被调用（正常 CLI 路径），
        也可能在测试中被直接调用（无 server.run）；这里幂等启动 reader 保证
        两条路径都能消费 stdin。
        """

        self._start_reader()
        request_id = str(request.get("request_id") or "").strip()
        if self._active_stream_is_cancelled() or self._ask_cancel_event.is_set():
            logger.info("skip Ask registration for cancelled stream: request_id=%s", request_id)
            return dict(
                normalize_ask_response(
                    {
                        "status": "cancelled",
                        "request_id": request_id,
                        "error": "cancelled by user",
                    },
                    request=request,
                )
            )
        self._write_notification(
            dict(request),
            method="ask_request",
            request_id=self._active_stream_request_id,
        )
        deferred_commands: list[dict[str, Any]] = []

        def finish(response: dict[str, Any]) -> dict[str, Any]:
            self._restore_deferred_commands(deferred_commands)
            return response

        while True:
            # cancel_stream 由 reader 线程直接派发不经队列，通过 Event 通知。
            if self._ask_cancel_event.is_set():
                logger.info("ask cancelled by cancel_stream: request_id=%s", request_id)
                return finish(dict(
                    normalize_ask_response(
                        {
                            "status": "cancelled",
                            "request_id": request_id,
                            "error": "cancelled by user",
                        },
                        request=request,
                    )
                ))
            try:
                answer_request = self._command_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if answer_request is None:
                # stdin 已关闭，确保 sentinel 让主循环退出。
                self._enqueue_command(None)
                return finish(dict(
                    normalize_ask_response(
                        {
                            "status": "error",
                            "request_id": request_id,
                            "error": "ask input stream closed",
                        },
                        request=request,
                    )
                ))

            answer_id = answer_request.get("id")
            method = str(answer_request.get("method") or "")

            if self._is_stream_priority_command(answer_request):
                # Ask 等待也是安全边界：控制、检查与 default/accept 切换可优先执行。
                try:
                    self._handle_request(answer_request)
                except Exception:
                    logger.exception("priority command failed during ask wait: %s", method)
                if method == "cancel_stream":
                    # cancel 语义统一：用户取消了整个 stream，ask 也不再等待。
                    logger.info("ask cancelled by cancel_stream: request_id=%s", request_id)
                    return finish(dict(
                        normalize_ask_response(
                            {
                                "status": "cancelled",
                                "request_id": request_id,
                                "error": "cancelled by user",
                            },
                            request=request,
                        )
                    ))
                continue

            if method != "answer_ask":
                # 普通 FIFO 和涉及 Plan 的 mode 请求不能在 Ask 内执行，也不能报错
                # 丢弃；暂存到 Ask 完成，再恢复到后来到达的命令之前。
                deferred_commands.append(answer_request)
                continue
            params = answer_request.get("params") or {}
            if str(params.get("request_id") or "").strip() != request_id:
                incoming = str(params.get("request_id") or "").strip()
                logger.warning(
                    "drop stale answer_ask: incoming=%s pending=%s",
                    incoming, request_id,
                )
                self._write_error(
                    answer_id, -32602,
                    f"request_id mismatch (incoming={incoming!r}, pending={request_id!r})",
                )
                continue

            response = normalize_ask_response(params.get("response") or {}, request=request)
            self._write_response(answer_id, {"accepted": True})
            return finish(dict(response))

    def _write_notification(
        self,
        data: Any,
        *,
        method: str = "stream_step",
        request_id: Any = None,
    ) -> None:
        """Write a request-scoped notification for stream and Ask events."""
        params = (
            {"request_id": request_id, "request": data}
            if method == "ask_request"
            else {"request_id": request_id, "event": data}
        )
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write_json(notification)

    def _write_json(self, obj: Any) -> None:
        """Emit one compact JSON line and flush immediately for streaming UX."""
        line = json.dumps(obj, ensure_ascii=False)
        # 双工写：reader 线程不写 stdout，主线程是唯一写入者；锁仅做防御性保护，
        # 防止未来有其它后台线程并发写入交错。
        with self._stdout_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
