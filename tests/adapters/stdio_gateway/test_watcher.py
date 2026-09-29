"""Adapter boundary regression tests after SDK-owned async continuation."""

from __future__ import annotations

import inspect
import unittest

from adapters.stdio_gateway.runtime import DirectRunnerRuntime
from adapters.stdio_gateway.stdio_server import StdioJsonRpcServer


class StdioAdapterBoundaryTests(unittest.TestCase):
    def test_stdio_server_has_no_background_notification_watcher(self) -> None:
        server = StdioJsonRpcServer()

        self.assertFalse(hasattr(server, "_watcher_thread"))
        self.assertFalse(hasattr(server, "_resume_enqueued"))
        self.assertFalse(hasattr(server, "_watcher_loop"))

    def test_runtime_does_not_expose_adapter_notification_waiting(self) -> None:
        self.assertFalse(hasattr(DirectRunnerRuntime, "has_pending_notifications"))
        self.assertFalse(hasattr(DirectRunnerRuntime, "wait_pending_notifications"))

    def test_server_never_sends_empty_auto_resume_requests(self) -> None:
        source = inspect.getsource(StdioJsonRpcServer)

        self.assertNotIn("__auto_resume__", source)
        self.assertNotIn('{"message": ""}', source)


if __name__ == "__main__":
    unittest.main()
