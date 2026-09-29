"""Web adapter regression tests for SDK-owned request continuation."""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import MagicMock

from adapters.web_gateway.app import WebSocketRuntimeConnection


class WebAdapterBoundaryTests(unittest.TestCase):
    def test_connection_has_no_notification_watcher(self) -> None:
        connection = WebSocketRuntimeConnection(websocket=MagicMock())

        self.assertFalse(hasattr(connection, "_watcher_task"))
        self.assertFalse(hasattr(connection, "_watcher_loop"))
        self.assertFalse(hasattr(connection, "_auto_resume"))

    def test_web_adapter_never_starts_an_empty_auto_resume_stream(self) -> None:
        source = inspect.getsource(WebSocketRuntimeConnection)

        self.assertNotIn("wait_pending_notifications", source)
        self.assertNotIn('{"message": ""}', source)


if __name__ == "__main__":
    unittest.main()
