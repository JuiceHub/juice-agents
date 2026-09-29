"""stdio_gateway cancel_stream contract tests.

覆盖：
1) `RpcHandlers.handle_cancel_stream` 把请求转发到 runtime。
2) `DirectRunnerRuntime.request_cancel_stream` 在没有 runner / 不支持的 runner 时返回 cancelled=False。
3) `StdioJsonRpcServer._drain_control_commands` 在 streaming 期间能并发分发 cancel_stream，
   并把非控制命令保留在队列尾部，保持 FIFO。
"""

from __future__ import annotations

import json
import io
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from adapters.stdio_gateway.handlers import RpcHandlers
from adapters.stdio_gateway.runtime import DirectRunnerRuntime
from adapters.stdio_gateway.stdio_server import StdioJsonRpcServer

ROOT_DIR = Path(__file__).resolve().parents[3]


class CancelStreamHandlerTests(unittest.TestCase):
    def test_handler_returns_cancelled_false_when_no_runtime(self) -> None:
        handlers = RpcHandlers()
        # _runtime 默认是 None
        self.assertIsNone(handlers._runtime)
        self.assertEqual(handlers.dispatch("cancel_stream", {}), {"cancelled": False})

    def test_handler_forwards_to_runtime_request_cancel_stream(self) -> None:
        handlers = RpcHandlers()
        runtime = MagicMock(spec=DirectRunnerRuntime)
        runtime.request_cancel_stream.return_value = {"cancelled": True}
        handlers._runtime = runtime  # type: ignore[assignment]

        result = handlers.dispatch("cancel_stream", {})

        runtime.request_cancel_stream.assert_called_once_with()
        self.assertEqual(result, {"cancelled": True})


class RuntimeRequestCancelStreamTests(unittest.TestCase):
    def _make_runtime_without_init(self) -> DirectRunnerRuntime:
        # 绕过 __init__ 以隔离测试目标
        runtime = DirectRunnerRuntime.__new__(DirectRunnerRuntime)
        runtime._runner = None  # type: ignore[attr-defined]
        return runtime

    def test_cancel_returns_false_without_active_runner(self) -> None:
        runtime = self._make_runtime_without_init()
        self.assertEqual(runtime.request_cancel_stream(), {"cancelled": False})

    def test_cancel_returns_false_when_runner_lacks_method(self) -> None:
        runtime = self._make_runtime_without_init()
        # 一个没有 request_stop_stream 的对象
        runtime._runner = object()  # type: ignore[attr-defined]
        self.assertEqual(runtime.request_cancel_stream(), {"cancelled": False})

    def test_cancel_invokes_runner_request_stop_stream(self) -> None:
        runtime = self._make_runtime_without_init()
        runner = MagicMock()
        runtime._runner = runner  # type: ignore[attr-defined]
        result = runtime.request_cancel_stream(reason="user_cancelled")
        runner.request_stop_stream.assert_called_once_with(reason="user_cancelled")
        self.assertEqual(result, {"cancelled": True})
        self.assertTrue(runtime._cancel_requested.is_set())  # type: ignore[attr-defined]


class DrainControlCommandsTests(unittest.TestCase):
    def _make_server_with_capture(self) -> tuple[StdioJsonRpcServer, list[dict[str, Any]]]:
        server = StdioJsonRpcServer()
        captured: list[dict[str, Any]] = []

        def capture(req: dict[str, Any]) -> None:
            captured.append(req)

        server._handle_request = capture  # type: ignore[assignment]
        return server, captured

    def test_drain_dispatches_only_control_commands(self) -> None:
        server, captured = self._make_server_with_capture()
        server._command_queue.put({"id": "1", "method": "cancel_stream", "params": {}})
        server._command_queue.put({"id": "2", "method": "cancel_stream", "params": {}})

        server._drain_control_commands()

        methods = [r["method"] for r in captured]
        self.assertEqual(methods, ["cancel_stream", "cancel_stream"])
        self.assertTrue(server._command_queue.empty())

    def test_drain_preserves_non_control_commands_at_queue_head(self) -> None:
        server, captured = self._make_server_with_capture()
        server._command_queue.put({"id": "1", "method": "cancel_stream", "params": {}})
        server._command_queue.put({"id": "2", "method": "describe_session", "params": {}})
        server._command_queue.put({"id": "3", "method": "cancel_stream", "params": {}})
        server._command_queue.put({"id": "4", "method": "list_models", "params": {}})

        server._drain_control_commands()

        # 控制命令应被立即派发；非控制命令保持顺序、保留在队列。
        dispatched_methods = [r["method"] for r in captured]
        self.assertEqual(dispatched_methods, ["cancel_stream", "cancel_stream"])

        remaining: list[str] = []
        while not server._command_queue.empty():
            req = server._command_queue.get_nowait()
            self.assertIsNotNone(req)
            assert req is not None
            remaining.append(req["method"])
        self.assertEqual(remaining, ["describe_session", "list_models"])

    def test_stream_stop_reason_reads_runner_reason(self) -> None:
        server = StdioJsonRpcServer()
        runner = MagicMock()
        runner.stop_reason = "user_cancelled"
        runtime = MagicMock()
        runtime._runner = runner
        server.handlers._runtime = runtime  # type: ignore[assignment]

        self.assertEqual(server._stream_stop_reason(), "user_cancelled")

    def test_stream_notification_carries_originating_rpc_request_id(self) -> None:
        server = StdioJsonRpcServer()
        event = {"kind": "action_step", "step_num": 1}
        server.handlers.dispatch = MagicMock(return_value=iter([event]))  # type: ignore[assignment]
        emitted: list[dict[str, Any]] = []
        server._write_json = lambda value: emitted.append(value)  # type: ignore[assignment]

        server._handle_request({
            "jsonrpc": "2.0",
            "id": 41,
            "method": "stream_message",
            "params": {"message": "hello"},
        })

        self.assertEqual(emitted[0]["method"], "stream_step")
        self.assertEqual(emitted[0]["params"], {"request_id": 41, "event": event})

    def test_cancelled_queued_request_is_skipped_before_dispatch(self) -> None:
        server = StdioJsonRpcServer()
        server.handlers.dispatch = MagicMock(return_value=iter([]))  # type: ignore[assignment]
        server._write_json = MagicMock()  # type: ignore[assignment]
        server._record_cancel_target({"request_id": "new"})

        server._handle_request({
            "jsonrpc": "2.0",
            "id": "new",
            "method": "stream_message",
            "params": {"message": "should not execute"},
        })

        server.handlers.dispatch.assert_not_called()


class ReaderThreadIntegrationTests(unittest.TestCase):
    def test_reader_thread_dispatches_cancel_stream_immediately(self) -> None:
        # cancel_stream 是唯一 reader 线程快速路径：直接派发，不进入普通队列。
        server = StdioJsonRpcServer()
        captured: list[dict[str, Any]] = []
        server._handle_request = lambda req: captured.append(req)  # type: ignore[assignment]
        original_stdin = sys.stdin
        sys.stdin = io.StringIO(
            json.dumps({"id": "1", "method": "cancel_stream", "params": {}}) + "\n"
            + json.dumps({"id": "2", "method": "list_models", "params": {}}) + "\n"
        )
        try:
            server._start_reader()
            assert server._reader_thread is not None
            server._reader_thread.join(timeout=2.0)
        finally:
            sys.stdin = original_stdin

        self.assertEqual([item["method"] for item in captured], ["cancel_stream"])
        items: list[Any] = []
        while not server._command_queue.empty():
            items.append(server._command_queue.get_nowait())
        self.assertIn(None, items)
        non_sentinel = [x for x in items if x is not None]
        self.assertEqual(len(non_sentinel), 1)
        self.assertEqual(non_sentinel[0]["method"], "list_models")

    def test_stdout_writes_are_lock_protected_for_reader_responses(self) -> None:
        source = (ROOT_DIR / "adapters/stdio_gateway/stdio_server.py").read_text(encoding="utf-8")
        self.assertIn("self._stdout_lock = threading.Lock()", source)
        self.assertIn("with self._stdout_lock:", source)


if __name__ == "__main__":
    unittest.main()
