"""stdio_gateway cancel_stream contract tests.

覆盖：
1) `RpcHandlers.handle_cancel_stream` 把请求转发到 runtime。
2) `DirectRunnerRuntime.request_cancel_stream` 在没有 runner / 不支持的 runner 时返回 cancelled=False。
3) reader 的取消快速路径保持可用，标准输出协议帧不会交错。
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

    def test_cancel_invokes_manager_owned_round_cancellation(self) -> None:
        runtime = self._make_runtime_without_init()
        runner = MagicMock()
        runner.active_round_id = "round-1"
        runner.cancel_round.return_value = True
        runtime._runner = runner
        self.assertEqual(runtime.request_cancel_stream(reason="user_cancelled"), {"cancelled": True})
        runner.cancel_round.assert_called_once_with("round-1", reason="user_cancelled")

    def test_no_active_round_does_not_report_cancellation(self) -> None:
        runtime = self._make_runtime_without_init()
        runtime._runner = MagicMock(active_round_id=None)
        self.assertEqual(runtime.request_cancel_stream(), {"cancelled": False})
        runtime._runner.cancel_round.assert_not_called()


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
