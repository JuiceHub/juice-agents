"""The local web gateway must reject externally reachable bind addresses."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from adapters.web_gateway.entry import _loopback_host, main


class WebGatewayEntryTests(unittest.TestCase):
    def test_only_loopback_hosts_are_accepted(self) -> None:
        for host in ("127.0.0.1", "127.2.3.4", "::1", "localhost"):
            with self.subTest(host=host):
                self.assertEqual(_loopback_host(host), host)

        for host in ("0.0.0.0", "::", "192.168.1.2", "example.com"):
            with self.subTest(host=host):
                with self.assertRaises(argparse.ArgumentTypeError):
                    _loopback_host(host)

    def test_main_rejects_public_host_before_starting_server(self) -> None:
        with (
            patch.object(sys, "argv", ["juice-web", "--host", "0.0.0.0"]),
            patch("adapters.web_gateway.entry.uvicorn.run") as run,
        ):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()

    def test_main_starts_server_on_loopback(self) -> None:
        with (
            patch.object(sys, "argv", ["juice-web", "--host", "::1", "--port", "8004"]),
            patch("adapters.web_gateway.entry.create_app") as create_app,
            patch("adapters.web_gateway.entry.uvicorn.run") as run,
        ):
            main()
        run.assert_called_once_with(create_app.return_value, host="::1", port=8004, log_level="info")

    def test_launcher_rejects_public_host_before_starting_frontend(self) -> None:
        root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            [str(root / "juice-web")],
            cwd=root,
            env={**os.environ, "JUICE_WEB_HOST": "0.0.0.0"},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loopback", result.stderr)
        self.assertNotIn("Installing web dependencies", result.stdout)


if __name__ == "__main__":
    unittest.main()
