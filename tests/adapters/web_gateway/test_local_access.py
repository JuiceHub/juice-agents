"""Browser access to the unauthenticated gateway stays on local origins."""

import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from adapters.web_gateway.app import create_app


class LocalAccessTests(unittest.TestCase):
    def test_http_rejects_nonlocal_host(self) -> None:
        client = TestClient(create_app(), base_url="http://localhost")
        self.assertEqual(client.get("/api/health").status_code, 200)
        self.assertEqual(client.get("/api/health", headers={"Host": "[::1]:8003"}).status_code, 200)
        self.assertEqual(client.get("/api/health", headers={"Host": "evil.example"}).status_code, 400)
        self.assertEqual(client.get("/api/health", headers={"Host": "localhost.evil.example"}).status_code, 400)

    def test_websockets_reject_foreign_origin_and_host(self) -> None:
        client = TestClient(create_app(), base_url="http://localhost")
        for route in ("/ws", "/ws/browser/live"):
            for headers in (
                {"Origin": "https://evil.example"},
                {"Origin": "null"},
                {"Host": "evil.example", "Origin": "http://localhost:5173"},
            ):
                with self.subTest(route=route, headers=headers):
                    with self.assertRaises(WebSocketDisconnect) as error:
                        with client.websocket_connect(route, headers={"Host": "localhost", **headers}):
                            pass
                    self.assertEqual(error.exception.code, 1008)

    def test_websocket_accepts_local_browser_origin(self) -> None:
        client = TestClient(create_app(), base_url="http://localhost")
        with client.websocket_connect("/ws", headers={"Host": "localhost", "Origin": "http://127.0.0.1:5173"}):
            pass
        with client.websocket_connect("/ws", headers={"Host": "[::1]:8003", "Origin": "http://[::1]:5173"}):
            pass


if __name__ == "__main__":
    unittest.main()
