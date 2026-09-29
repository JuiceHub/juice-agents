import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from adapters.web_gateway.app import (
    BrowserLivePreviewConnection,
    WebSocketRuntimeConnection,
    _apply_browser_live_command,
    _browser_live_preview,
    _browser_live_status,
    _browser_live_session,
    _browser_session_keys,
    create_app,
)
from adapters.stdio_gateway.sessions import list_sessions


class WebGatewayTests(unittest.TestCase):
    def test_team_list_is_cold_and_does_not_create_runner_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = TestClient(create_app(), base_url="http://localhost")
            listed = client.get("/api/teams/configs", params={"base_dir": tmpdir})

            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json(), {"teams": []})
            self.assertFalse((Path(tmpdir) / ".juice").exists())

    def test_websocket_team_queries_are_cold_and_do_not_create_runtime(self) -> None:
        """The Web slash command can inspect Team declarations before chat starts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = WebSocketRuntimeConnection(websocket=MagicMock())

            listed = connection._handle_sync_command(
                "list_team_configs", {"base_dir": tmpdir}
            )
            with self.assertRaises(FileNotFoundError):
                connection._handle_sync_command(
                    "get_team_config", {"base_dir": tmpdir, "team_name": "default"}
                )

            self.assertEqual(listed, {"teams": []})
            self.assertIsNone(connection.runtime)
            self.assertFalse((Path(tmpdir) / ".juice").exists())

    def test_team_routes_store_only_global_agent_name_references(self) -> None:
        from juice_agents.core.config.context import ConfigurationContext
        from juice_agents.core.registry import AgentRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = AgentRegistry(
                config_context=ConfigurationContext.from_workspace(tmpdir)
            )
            for name in ("lead", "dev", "qa"):
                registry.save_config(
                    {
                        "name": name,
                        "agent_type": "react",
                        "max_steps": 3,
                        "tools": [],
                    }
                )
            client = TestClient(create_app(), base_url="http://localhost")
            created = client.post(
                "/api/teams/configs",
                json={
                    "base_dir": tmpdir,
                    "team_name": "alpha",
                    "member_names": ["lead", "dev"],
                },
            )
            updated = client.put(
                "/api/teams/configs/alpha/manifest",
                json={
                    "base_dir": tmpdir,
                    "description": "updated",
                    "member_names": ["qa", "lead", "dev"],
                },
            )
            fetched = client.get(
                "/api/teams/configs/alpha", params={"base_dir": tmpdir}
            )
            private_member_route = client.post(
                "/api/teams/configs/alpha/teammates",
                json={"base_dir": tmpdir, "member": {"name": "qa"}},
            )

            team_dir = Path(tmpdir) / ".juice" / "teams" / "alpha"
            self.assertEqual(created.status_code, 200)
            self.assertEqual(updated.status_code, 200)
            self.assertIn("manifest_path", created.json())
            self.assertEqual(fetched.json()["description"], "updated")
            self.assertEqual(fetched.json()["member_names"], ["qa", "lead", "dev"])
            self.assertTrue((team_dir / "manifest.yaml").exists())
            self.assertFalse((team_dir / "agents").exists())
            self.assertEqual(private_member_route.status_code, 404)

    def test_websocket_dispatches_plugin_read_and_config_commands(self) -> None:
        connection = WebSocketRuntimeConnection(websocket=MagicMock())
        runtime = MagicMock()
        runtime.list_plugins.return_value = {"success": True, "plugins": [{"name": "demo"}]}
        runtime.view_plugin.return_value = {"success": True, "plugin": {"name": "demo"}}
        runtime.plugins_config_status.return_value = {"success": True, "enabled": True, "plugins": []}
        runtime.set_plugins_config.return_value = {"success": True, "enabled": False, "plugins": []}
        connection.runtime = runtime

        listed = connection._handle_sync_command("list_plugins", {})
        viewed = connection._handle_sync_command(
            "view_plugin",
            {"name": "demo", "file_path": ".juice-plugin/plugin.json"},
        )
        status = connection._handle_sync_command("plugins_config_status", {})
        updated = connection._handle_sync_command(
            "set_plugins_config",
            {"enabled": False, "disabled": ["demo"]},
        )

        runtime.list_plugins.assert_called_once_with()
        runtime.view_plugin.assert_called_once_with("demo", file_path=".juice-plugin/plugin.json")
        runtime.plugins_config_status.assert_called_once_with()
        runtime.set_plugins_config.assert_called_once_with(enabled=False, disabled=["demo"])
        self.assertEqual(listed["plugins"][0]["name"], "demo")
        self.assertEqual(viewed["plugin"]["name"], "demo")
        self.assertTrue(status["enabled"])
        self.assertFalse(updated["enabled"])

    def test_health_and_bootstrap_return_local_gateway_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = TestClient(create_app(), base_url="http://localhost")

            health = client.get("/api/health")
            bootstrap = client.get("/api/bootstrap", params={"base_dir": tmpdir})

        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(bootstrap.status_code, 200)
        self.assertEqual(bootstrap.json()["base_dir"], str(Path(tmpdir).resolve()))
        self.assertIn("sessions", bootstrap.json())
        self.assertIn("models", bootstrap.json())
        self.assertTrue(bootstrap.json()["models"][0]["supported_efforts"])

    def test_file_preview_is_workspace_scoped_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "src" / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            client = TestClient(create_app(), base_url="http://localhost")

            tree = client.get("/api/files/tree", params={"base_dir": tmpdir})
            content = client.get(
                "/api/files/read",
                params={"base_dir": tmpdir, "path": "src/app.py"},
            )
            image = client.get(
                "/api/files/read",
                params={"base_dir": tmpdir, "path": "src/plot.png"},
            )
            image_asset = client.get(
                "/api/files/asset",
                params={"base_dir": tmpdir, "path": "src/plot.png"},
            )
            non_image_asset = client.get(
                "/api/files/asset",
                params={"base_dir": tmpdir, "path": "src/app.py"},
            )
            escaped = client.get(
                "/api/files/read",
                params={"base_dir": tmpdir, "path": "../outside.py"},
            )
            escaped_asset = client.get(
                "/api/files/asset",
                params={"base_dir": tmpdir, "path": "../outside.png"},
            )

        self.assertEqual(tree.status_code, 200)
        self.assertEqual(content.json()["content"], "print('ok')\n")
        self.assertEqual(content.json()["language"], "python")
        self.assertEqual(content.json()["kind"], "text")
        self.assertEqual(image.json()["kind"], "image")
        self.assertEqual(image.json()["language"], "image")
        self.assertIn("/api/files/asset", image.json()["image_url"])
        self.assertEqual(image_asset.status_code, 200)
        self.assertEqual(non_image_asset.status_code, 404)
        self.assertEqual(escaped.status_code, 403)
        self.assertEqual(escaped_asset.status_code, 403)

    def test_file_tree_keeps_symlink_paths_workspace_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as external_tmpdir:
            root = Path(tmpdir)
            external_root = Path(external_tmpdir)
            external_file = external_root / "AGENTS.md"
            external_file.write_text("# external\n", encoding="utf-8")
            external_dir = external_root / "external-dir"
            external_dir.mkdir()
            (external_dir / "secret.txt").write_text("outside\n", encoding="utf-8")
            try:
                (root / "AGENTS.md").symlink_to(external_file)
                (root / "linked-dir").symlink_to(external_dir, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks are not available: {exc}")
            client = TestClient(create_app(), base_url="http://localhost")

            response = client.get("/api/files/tree", params={"base_dir": tmpdir})

        self.assertEqual(response.status_code, 200)
        children = {child["name"]: child for child in response.json()["tree"]["children"]}
        self.assertEqual(children["AGENTS.md"]["path"], "AGENTS.md")
        self.assertEqual(children["linked-dir"]["path"], "linked-dir")
        self.assertNotIn("children", children["linked-dir"])

    def test_live_browser_preview_returns_empty_or_latest_screenshot_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client = TestClient(create_app(), base_url="http://localhost")

            with patch("adapters.web_gateway.app.get_existing_browser_session", return_value=None):
                empty = client.get("/api/browser/live/preview", params={"base_dir": tmpdir})
            screenshot_dir = root / ".juice" / "runners" / "abc" / "browser_screenshots"
            screenshot_dir.mkdir(parents=True)
            (screenshot_dir / "page.png").write_bytes(b"png")
            image_dir = root / ".juice" / "runners" / "other" / "observation_images"
            image_dir.mkdir(parents=True)
            (image_dir / "generated.png").write_bytes(b"png")
            with patch("adapters.web_gateway.app.get_existing_browser_session", return_value=None):
                scoped_empty = client.get("/api/browser/live/preview", params={"base_dir": tmpdir, "runner_id": "other"})
                live_preview = client.get("/api/browser/live/preview", params={"base_dir": tmpdir, "runner_id": "abc"})

        self.assertFalse(empty.json()["connected"])
        self.assertFalse(scoped_empty.json()["connected"])
        self.assertTrue(live_preview.json()["connected"])
        self.assertIn("/api/browser/asset", live_preview.json()["image_url"])
        self.assertEqual(live_preview.json()["session_key"], "artifact")

    def test_legacy_browser_http_control_routes_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = TestClient(create_app(), base_url="http://localhost")
            self.assertEqual(client.get("/api/browser/preview", params={"base_dir": tmpdir}).status_code, 404)
            for path in [
                "/api/browser/live/navigate",
                "/api/browser/live/back",
                "/api/browser/live/forward",
                "/api/browser/live/refresh",
            ]:
                self.assertEqual(client.post(path, json={"base_dir": tmpdir}).status_code, 404)

    def test_live_browser_session_prefers_runner_session_before_default(self) -> None:
        fake = object()
        seen_keys: list[str] = []

        def get_existing(key: str):
            seen_keys.append(key)
            return fake if key == "abc:browser" else None

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "adapters.web_gateway.app.get_existing_browser_session",
            side_effect=get_existing,
        ):
            key, session = _browser_live_session(Path(tmpdir), "abc")

        self.assertEqual(key, "abc:browser")
        self.assertIs(session, fake)
        self.assertEqual(seen_keys[0], "abc:browser")

    def test_browser_session_keys_use_only_unified_general_root_name(self) -> None:
        keys = _browser_session_keys(Path("/tmp/workspace"), "runner-1")

        self.assertIn("runner-1:general", keys)
        self.assertNotIn("runner-1:general_react", keys)
        self.assertNotIn("runner-1:general_codeact", keys)

    def test_live_browser_preview_does_not_fall_back_to_default_for_runner(self) -> None:
        fake = object()

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "adapters.web_gateway.app.get_existing_browser_session",
            side_effect=lambda key: fake if key == "default" else None,
        ):
            preview = _browser_live_preview(Path(tmpdir), "abc")

        self.assertFalse(preview["connected"])
        self.assertEqual(preview["session_key"], "")

    def test_live_browser_session_creates_runner_scoped_browser_session(self) -> None:
        fake = object()
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "adapters.web_gateway.app.get_existing_browser_session",
            return_value=None,
        ), patch(
            "adapters.web_gateway.app.get_browser_session",
            return_value=fake,
        ) as get_session:
            key, session = _browser_live_session(Path(tmpdir), "abc")

        self.assertEqual(key, "abc:browser")
        self.assertIs(session, fake)
        self.assertEqual(get_session.call_args.args[0], "abc:browser")

    def test_live_browser_command_maps_pointer_keyboard_and_paste(self) -> None:
        class FakeMouse:
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            def move(self, x, y):
                self.calls.append(("move", x, y))

            def down(self, *, button):
                self.calls.append(("down", button))

            def up(self, *, button):
                self.calls.append(("up", button))

            def dblclick(self, x, y, *, button):
                self.calls.append(("dblclick", x, y, button))

            def wheel(self, dx, dy):
                self.calls.append(("wheel", dx, dy))

        class FakeKeyboard:
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            def press(self, key):
                self.calls.append(("press", key))

            def type(self, text):
                self.calls.append(("type", text))

            def insert_text(self, text):
                self.calls.append(("insert_text", text))

        class FakePage:
            def __init__(self) -> None:
                self.mouse = FakeMouse()
                self.keyboard = FakeKeyboard()
                self.calls: list[tuple] = []

            def go_forward(self, *, wait_until):
                self.calls.append(("go_forward", wait_until))

            def reload(self, *, wait_until):
                self.calls.append(("reload", wait_until))

        class FakeSession:
            def __init__(self) -> None:
                self.page_obj = FakePage()
                self._active_tab_id = "tab_1"
                self._tabs = {"tab_1": self.page_obj}
                self.calls: list[tuple] = []

            def run_on_browser_thread(self, callback):
                return callback()

            def page(self):
                return self.page_obj

            def open_url(self, url):
                self.calls.append(("open_url", url))

            def go_back(self):
                self.calls.append(("go_back",))

            def switch_tab(self, tab_id):
                self.calls.append(("switch_tab", tab_id))

            def close_tab(self, tab_id):
                self.calls.append(("close_tab", tab_id))

            def tab_snapshot(self):
                return {
                    "active_tab_id": self._active_tab_id,
                    "tabs": [{"tab_id": "tab_1", "url": "https://example.com", "title": "Example", "active": True}],
                }

        session = FakeSession()
        _apply_browser_live_command(session, {"type": "navigate", "url": "https://example.com"})
        _apply_browser_live_command(session, {"type": "back"})
        _apply_browser_live_command(session, {"type": "forward"})
        _apply_browser_live_command(session, {"type": "refresh"})
        _apply_browser_live_command(session, {"type": "switch_tab", "tab_id": "tab_1"})
        _apply_browser_live_command(session, {"type": "close_tab", "tab_id": "tab_1"})
        _apply_browser_live_command(session, {"type": "mouse", "event": "down", "x": 1, "y": 2, "button": "right"})
        _apply_browser_live_command(session, {"type": "mouse", "event": "up", "x": 1, "y": 2, "button": "right"})
        _apply_browser_live_command(session, {"type": "key", "key": "A", "modifiers": ["Control"]})
        _apply_browser_live_command(session, {"type": "paste", "text": "hello"})

        self.assertIn(("open_url", "https://example.com"), session.calls)
        self.assertIn(("go_back",), session.calls)
        self.assertIn(("switch_tab", "tab_1"), session.calls)
        self.assertIn(("close_tab", "tab_1"), session.calls)
        self.assertIn(("go_forward", "domcontentloaded"), session.page_obj.calls)
        self.assertIn(("reload", "domcontentloaded"), session.page_obj.calls)
        self.assertIn(("down", "right"), session.page_obj.mouse.calls)
        self.assertIn(("up", "right"), session.page_obj.mouse.calls)
        self.assertIn(("press", "Control+A"), session.page_obj.keyboard.calls)
        self.assertIn(("insert_text", "hello"), session.page_obj.keyboard.calls)

    def test_live_browser_restarts_screencast_when_active_page_can_change(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.stopped: list[str] = []
                self.started = 0

            def stop_screencast(self, token):
                self.stopped.append(token)

            def start_screencast(self, _callback):
                self.started += 1
                return "stream-new"

        connection = BrowserLivePreviewConnection(object(), root=Path("."), runner_id="run-1")
        delivered: list[dict] = []
        connection._put = delivered.append  # type: ignore[method-assign]
        connection.commands.put({"type": "switch_tab", "tab_id": "tab_2"})
        session = FakeSession()

        with patch("adapters.web_gateway.app._apply_browser_live_command") as apply_command, patch(
            "adapters.web_gateway.app._browser_live_status",
            return_value={"type": "status", "active_tab_id": "tab_2", "tabs": []},
        ):
            token = connection._drain_browser_commands("run-1:browser", session, "stream-old")

        self.assertEqual(session.stopped, ["stream-old"])
        self.assertEqual(session.started, 1)
        self.assertEqual(token, "stream-new")
        apply_command.assert_called_once_with(session, {"type": "switch_tab", "tab_id": "tab_2"})
        self.assertEqual(delivered[-1]["active_tab_id"], "tab_2")

    def test_live_browser_close_tab_surfaces_session_validation_errors(self) -> None:
        class FakeSession:
            _active_tab_id = "tab_1"
            _tabs = {"tab_1": object()}

            def run_on_browser_thread(self, callback):
                return callback()

            def tab_snapshot(self):
                return {
                    "active_tab_id": "tab_1",
                    "tabs": [{"tab_id": "tab_1", "url": "about:blank", "title": "", "active": True}],
                }

            def close_tab(self, _tab_id):
                raise ValueError("不能关闭最后一个浏览器标签页")

        with self.assertRaisesRegex(ValueError, "不能关闭最后一个"):
            _apply_browser_live_command(FakeSession(), {"type": "close_tab", "tab_id": "tab_1"})

    def test_live_browser_restores_screencast_after_tab_change_is_rejected(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.stopped: list[str] = []
                self.started = 0

            def stop_screencast(self, token):
                self.stopped.append(token)

            def start_screencast(self, _callback):
                self.started += 1
                return "stream-restored"

        connection = BrowserLivePreviewConnection(object(), root=Path("."), runner_id="run-1")
        delivered: list[dict] = []
        connection._put = delivered.append  # type: ignore[method-assign]
        connection.commands.put({"type": "close_tab", "tab_id": "tab_1"})
        session = FakeSession()

        with patch(
            "adapters.web_gateway.app._apply_browser_live_command",
            side_effect=ValueError("不能关闭最后一个浏览器标签页"),
        ), patch(
            "adapters.web_gateway.app._browser_live_status",
            return_value={"type": "status", "active_tab_id": "tab_1", "tabs": []},
        ):
            token = connection._drain_browser_commands("run-1:browser", session, "stream-old")

        self.assertEqual(session.stopped, ["stream-old"])
        self.assertEqual(session.started, 1)
        self.assertEqual(token, "stream-restored")
        self.assertEqual(delivered[-1], {"type": "error", "message": "不能关闭最后一个浏览器标签页"})

    def test_live_browser_commands_do_not_create_page_without_active_tab(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self._active_tab_id = ""
                self._tabs = {}
                self.calls: list[tuple] = []

            def run_on_browser_thread(self, callback):
                return callback()

            def tab_snapshot(self):
                return {"active_tab_id": "", "tabs": []}

            def page(self):
                raise AssertionError("page() should not be called without an active tab")

            def open_url(self, url):
                self.calls.append(("open_url", url))

            def go_back(self):
                raise AssertionError("go_back() should not run without an active tab")

            def switch_tab(self, tab_id):
                raise AssertionError(f"switch_tab({tab_id}) should not run without a tab")

        session = FakeSession()
        for command in [
            {"type": "back"},
            {"type": "forward"},
            {"type": "refresh"},
            {"type": "switch_tab", "tab_id": "tab_1"},
            {"type": "mouse", "event": "down", "x": 1, "y": 2},
            {"type": "key", "key": "A"},
            {"type": "paste", "text": "hello"},
            {"type": "wheel", "x": 1, "y": 2, "delta_x": 0, "delta_y": 1},
        ]:
            _apply_browser_live_command(session, command)
        _apply_browser_live_command(session, {"type": "navigate", "url": "https://example.com"})

        self.assertEqual(session.calls, [("open_url", "https://example.com")])

    def test_live_browser_status_reads_playwright_state_on_owner_thread(self) -> None:
        class FakePage:
            url = "https://example.com"

            def title(self):
                return "Example"

            def evaluate(self, script):
                return script == "() => window.history.length > 1"

        class FakeSession:
            headless = True
            viewport_width = 1280
            viewport_height = 900

            def __init__(self) -> None:
                self._active_tab_id = "tab_1"
                self._tabs = {"tab_1": FakePage()}
                self.owner_thread_used = False

            def run_on_browser_thread(self, callback):
                self.owner_thread_used = True
                return callback()

            def _ensure_started(self):
                raise AssertionError("live status must not start Playwright")

            def status(self):
                raise AssertionError("live status should use owner-thread snapshot, not public status()")

        session = FakeSession()
        status = _browser_live_status("abc:browser", session)

        self.assertTrue(session.owner_thread_used)
        self.assertEqual(status["session_key"], "abc:browser")
        self.assertEqual(status["active_tab_id"], "tab_1")
        self.assertEqual(status["tabs"][0]["title"], "Example")
        self.assertTrue(status["can_go_back"])

    def test_live_browser_status_is_idle_without_starting_empty_session(self) -> None:
        class FakeSession:
            headless = True
            viewport_width = 1280
            viewport_height = 900

            def __init__(self) -> None:
                self._active_tab_id = ""
                self._tabs = {}
                self.owner_thread_used = False

            def run_on_browser_thread(self, callback):
                self.owner_thread_used = True
                return callback()

            def _ensure_started(self):
                raise AssertionError("empty status must not start Playwright")

            def tab_snapshot(self):
                return {"active_tab_id": "", "tabs": []}

            def status(self):
                raise AssertionError("live status should use tab_snapshot, not public status()")

        session = FakeSession()
        status = _browser_live_status("abc:browser", session)

        self.assertTrue(session.owner_thread_used)
        self.assertEqual(status["tabs"], [])
        self.assertEqual(status["active_tab_id"], "")
        self.assertEqual(status["stream_state"], "idle")

    def test_live_browser_worker_starts_screencast_only_when_status_has_active_tab(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.started = 0

            def start_screencast(self, callback):  # noqa: ANN001
                self.started += 1
                callback({"type": "frame"})
                return "screen_1"

        connection = BrowserLivePreviewConnection(None, root=Path("."), runner_id="abc")
        session = FakeSession()

        idle_token = connection._start_screencast_if_ready(session, "", {"active_tab_id": ""})
        live_token = connection._start_screencast_if_ready(session, "", {"active_tab_id": "tab_1"})

        self.assertEqual(idle_token, "")
        self.assertEqual(live_token, "screen_1")
        self.assertEqual(session.started, 1)

    def test_open_external_browser_requests_visible_playwright_session(self) -> None:
        class FakeSession:
            headless = False

            def __init__(self) -> None:
                self.opened = ""
                self.visible_called = False

            def open_url(self, url):
                self.opened = url

            def ensure_visible_window(self):
                self.visible_called = True
                return {"status": "visible"}

            def status(self):
                return {
                    "active_tab_id": "tab_1",
                    "tabs": [{"tab_id": "tab_1", "url": self.opened, "title": "Example", "active": True}],
                }

        fake = FakeSession()
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "adapters.web_gateway.app.get_existing_browser_session",
            return_value=None,
        ), patch(
            "adapters.web_gateway.app.get_browser_session",
            return_value=fake,
        ), patch(
            "adapters.web_gateway.app._has_visible_browser_display",
            return_value=True,
        ):
            client = TestClient(create_app(), base_url="http://localhost")
            result = client.post(
                "/api/browser/live/open-external",
                json={"base_dir": tmpdir, "runner_id": "abc", "url": "https://example.com"},
            )

        self.assertEqual(result.status_code, 200)
        self.assertTrue(fake.visible_called)
        self.assertEqual(result.json()["stream_state"], "external")
        self.assertTrue(result.json()["external_window"])

    def test_open_external_browser_reports_missing_display(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "adapters.web_gateway.app._has_visible_browser_display",
            return_value=False,
        ):
            client = TestClient(create_app(), base_url="http://localhost")
            result = client.post(
                "/api/browser/live/open-external",
                json={"base_dir": tmpdir, "runner_id": "abc", "url": "https://example.com"},
            )

        self.assertEqual(result.status_code, 409)
        self.assertIn("DISPLAY/WAYLAND_DISPLAY", result.json()["detail"])

    def test_new_browser_tab_creates_real_playwright_tab_in_runner_session(self) -> None:
        class FakeSession:
            headless = True
            viewport_width = 1280
            viewport_height = 900

            def __init__(self) -> None:
                self.created: list[tuple[str | None, bool]] = []

            def new_tab(self, url, make_active=True):
                self.created.append((url, make_active))
                return {"tab_id": "tab_2", "url": url or "about:blank", "title": "New"}

            def status(self):
                return {
                    "active_tab_id": "tab_2",
                    "tabs": [{"tab_id": "tab_2", "url": "https://example.com", "title": "Example", "active": True}],
                }

        fake = FakeSession()
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "adapters.web_gateway.app.get_existing_browser_session",
            return_value=None,
        ), patch(
            "adapters.web_gateway.app.get_browser_session",
            return_value=fake,
        ):
            client = TestClient(create_app(), base_url="http://localhost")
            missing_runner = client.post("/api/browser/live/new-tab", json={"base_dir": tmpdir})
            result = client.post(
                "/api/browser/live/new-tab",
                json={"base_dir": tmpdir, "runner_id": "abc", "url": "https://example.com", "make_active": True},
            )

        self.assertEqual(missing_runner.status_code, 400)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(fake.created, [("https://example.com", True)])
        self.assertEqual(result.json()["session_key"], "abc:browser")
        self.assertEqual(result.json()["active_tab_id"], "tab_2")

    def test_web_archive_hides_runner_from_web_lists_but_not_cli_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_manifest(root, "visible")
            _write_manifest(root, "archived")
            client = TestClient(create_app(), base_url="http://localhost")

            with client.websocket_connect("/ws", headers={"Host": "localhost"}) as websocket:
                websocket.send_json({"id": "1", "type": "archive_runner", "payload": {"base_dir": tmpdir, "runner_id": "archived"}})
                archive_result = websocket.receive_json()
                websocket.send_json({"id": "2", "type": "list_sessions", "payload": {"base_dir": tmpdir}})
                visible_result = websocket.receive_json()
                websocket.send_json({"id": "3", "type": "list_archived_sessions", "payload": {"base_dir": tmpdir}})
                archived_result = websocket.receive_json()
                websocket.send_json({"id": "4", "type": "restore_runner", "payload": {"base_dir": tmpdir, "runner_id": "archived"}})
                restore_result = websocket.receive_json()
                websocket.send_json({"id": "5", "type": "list_sessions", "payload": {"base_dir": tmpdir}})
                restored_visible_result = websocket.receive_json()
            cli_sessions = sorted(summary.runner_id for summary in list_sessions(tmpdir))

        self.assertEqual(archive_result["payload"]["archived"], True)
        self.assertEqual([item["runner_id"] for item in visible_result["payload"]], ["visible"])
        self.assertEqual([item["runner_id"] for item in archived_result["payload"]], ["archived"])
        self.assertEqual(restore_result["payload"]["restored"], True)
        self.assertEqual(
            sorted(item["runner_id"] for item in restored_visible_result["payload"]),
            ["archived", "visible"],
        )
        self.assertEqual(cli_sessions, ["archived", "visible"])

def _write_manifest(root: Path, runner_id: str) -> None:
    runner_root = root / ".juice" / "runners" / runner_id
    runner_root.mkdir(parents=True)
    (runner_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "runner_id": runner_id,
                "permission_mode": "default",
                "agent_mode": "agent",
                "root_agent_name": "root",
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
