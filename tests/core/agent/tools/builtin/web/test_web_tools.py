"""Web search and browser automation tool contracts."""

import json
import tempfile
import threading
import unittest
from textwrap import dedent
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from juice_agents.core.agent.tools import ApiWebSearchTool as ApiWebSearchToolExport
from juice_agents.core.agent.tools import BrowserSearchTool as BrowserSearchToolExport
from juice_agents.core.agent.tools.builtin.web.web_search_tools import (
    ApiWebSearchTool,
    WebSearchTool,
    _extract_uddg_target,
    _format_playwright_start_error,
    _parse_version_tuple,
)
from juice_agents.core.agent.tools.builtin.web.browser.inspection_tools import BrowserGetTextTool, BrowserWaitForTool
from juice_agents.core.agent.tools.builtin.web.browser.interaction_tools import BrowserClickTool, BrowserFillTool
from juice_agents.core.agent.tools.builtin.web.browser.navigation_tools import (
    BrowserClosePopupsTool,
    BrowserFindTextTool,
    BrowserGoBackTool,
    BrowserOpenUrlTool,
    BrowserScreenshotTool,
    BrowserSearchTool,
)
from juice_agents.core.agent.tools.builtin.web.browser.session import PlaywrightBrowserSession, get_browser_session
from juice_agents.core.agent.tools.builtin.web.browser.state_tools import BrowserEvaluateTool
from juice_agents.core.agent.tools.builtin.web.browser.tabs_tools import BrowserCloseTabTool, BrowserListTabsTool, BrowserNewTabTool


class _DummyResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False


class WebSearchToolTests(unittest.TestCase):
    def _write_runtime_config(self, path: str, body: str) -> None:
        Path(path).write_text(dedent(body).strip() + "\n", encoding="utf-8")

    def _write_env(self, path: str, body: str) -> None:
        Path(path).write_text(dedent(body).strip() + "\n", encoding="utf-8")

    def test_tool_package_exports(self):
        self.assertIs(ApiWebSearchToolExport, ApiWebSearchTool)
        self.assertIs(BrowserSearchToolExport, BrowserSearchTool)

    def test_extract_uddg_target(self):
        url = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1"
        self.assertEqual(_extract_uddg_target(url), "https://example.com/a?x=1")

    def test_invalid_engine_raises(self):
        with self.assertRaises(ValueError):
            WebSearchTool(engine="google")

    def test_invalid_max_results_raises(self):
        tool = WebSearchTool()
        with self.assertRaises(ValueError):
            tool.forward(query="test", max_results=0)

    @patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.urlopen")
    def test_duckduckgo_html_parse_and_limit(self, mock_urlopen):
        html = b"""
<html><body>
  <div class="results">
    <div class="result">
      <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2F1">Example 1</a>
      <a class="result__snippet">Snippet 1</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.org">Example 2</a>
      <div class="result__snippet">Snippet <b>2</b></div>
    </div>
  </div>
</body></html>
"""
        mock_urlopen.return_value = _DummyResponse(html)

        tool = WebSearchTool(timeout_s=0.1, max_results_cap=10)
        out = tool.forward(query="hello", max_results=1)
        self.assertNotIn("engine", out)
        self.assertEqual(out["query"], "hello")
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["title"], "Example 1")
        self.assertEqual(out["results"][0]["url"], "https://example.com/1")
        self.assertEqual(out["results"][0]["snippet"], "Snippet 1")

        tool_ddg = WebSearchTool(engine="ddg", max_results_cap=10)
        out2 = tool_ddg.forward(query="hello", max_results=10)
        self.assertNotIn("engine", out2)
        self.assertEqual(len(out2["results"]), 2)
        self.assertEqual(out2["results"][1]["title"], "Example 2")
        self.assertEqual(out2["results"][1]["url"], "https://example.org")
        self.assertEqual(out2["results"][1]["snippet"], "Snippet 2")

    @patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.urlopen")
    def test_duckduckgo_fallback_to_lite(self, mock_urlopen):
        html = b"""
<html><body>
  <div class="results">
    <div class="result">
      <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2F1">Example 1</a>
      <a class="result__snippet">Snippet 1</a>
    </div>
  </div>
</body></html>
"""
        mock_urlopen.side_effect = [URLError("fail"), _DummyResponse(html)]

        tool = WebSearchTool(timeout_s=0.1, max_results_cap=10)
        out = tool.forward(query="hello", max_results=1)
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(mock_urlopen.call_count, 2)

        first_req = mock_urlopen.call_args_list[0].args[0]
        second_req = mock_urlopen.call_args_list[1].args[0]
        self.assertIn("https://html.duckduckgo.com/html/", first_req.full_url)
        self.assertIn("https://lite.duckduckgo.com/lite/", second_req.full_url)

    @patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.urlopen")
    def test_bing_rss_parse_and_limit(self, mock_urlopen):
        rss = b"""
<rss version="2.0">
  <channel>
    <item>
      <title>Example 1</title>
      <link>https://example.com/1</link>
      <description>Snippet 1</description>
    </item>
    <item>
      <title>Example 2</title>
      <link>https://example.org</link>
      <description>Snippet 2</description>
    </item>
  </channel>
</rss>
"""
        mock_urlopen.return_value = _DummyResponse(rss)

        tool = WebSearchTool(engine="bing", timeout_s=0.1, max_results_cap=10)
        out = tool.forward(query="hello", max_results=5)
        self.assertEqual(out["query"], "hello")
        self.assertEqual(len(out["results"]), 2)
        self.assertEqual(out["results"][0]["title"], "Example 1")
        self.assertEqual(out["results"][0]["url"], "https://example.com/1")
        self.assertEqual(out["results"][0]["snippet"], "Snippet 1")
        self.assertEqual(out["results"][1]["title"], "Example 2")
        self.assertEqual(out["results"][1]["url"], "https://example.org")
        self.assertEqual(out["results"][1]["snippet"], "Snippet 2")

    def test_api_web_search_missing_api_key_raises(self):
        with tempfile.TemporaryDirectory() as td:
            config_path = str(Path(td) / "config.yaml")
            self._write_runtime_config(config_path, "tools: {}\n")
            tool = ApiWebSearchTool(config_path=config_path)
            with self.assertRaises(ValueError):
                tool.forward(query="hello")

    def test_api_web_search_default_config_path_points_to_sdk_asset(self):
        tool = ApiWebSearchTool()
        self.assertTrue(Path(tool.config_path).is_file())
        self.assertTrue(
            Path(tool.config_path).as_posix().endswith(
                "juice_agents/_assets/config.example.yaml"
            )
        )

    @patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.urlopen")
    def test_api_web_search_reads_api_key_from_env_tavily(self, mock_urlopen):
        payload = {"results": [{"title": "T", "url": "https://example.com", "content": "S"}]}
        mock_urlopen.return_value = _DummyResponse(json.dumps(payload).encode("utf-8"))

        with tempfile.TemporaryDirectory() as td:
            config_path = str(Path(td) / "config.yaml")
            env_path = str(Path(td) / ".env")
            self._write_runtime_config(
                config_path,
                """
                tools:
                  tavily:
                    api_key_env: TAVILY_API_KEY
                """,
            )
            self._write_env(env_path, "TAVILY_API_KEY=tavily_key")
            with patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.DEFAULT_DOTENV_PATH", Path(env_path)):
                tool = ApiWebSearchTool(config_path=config_path, timeout_s=0.1)
                out = tool.forward(query="hello", max_results=1)
                self.assertEqual(len(out["results"]), 1)

                req = mock_urlopen.call_args_list[0].args[0]
                auth = req.headers.get("Authorization") or req.headers.get("authorization")
                self.assertEqual(auth, "Bearer tavily_key")
                body = json.loads(req.data.decode("utf-8"))
                self.assertEqual(body["query"], "hello")
                self.assertEqual(body["max_results"], 1)

    @patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.urlopen")
    def test_api_web_search_serpapi_parse_and_limit(self, mock_urlopen):
        payload = {
            "search_metadata": {"status": "Success"},
            "organic_results": [
                {"position": 1, "title": "Example 1", "link": "https://example.com/1", "snippet": "Snippet 1"},
                {"position": 2, "title": "Example 2", "link": "https://example.org", "snippet": ["Snippet", "2"]},
            ],
        }
        mock_urlopen.return_value = _DummyResponse(json.dumps(payload).encode("utf-8"))

        with tempfile.TemporaryDirectory() as td:
            config_path = str(Path(td) / "config.yaml")
            env_path = str(Path(td) / ".env")
            self._write_runtime_config(
                config_path,
                """
                tools:
                  serpapi:
                    api_key_env: SERPAPI_API_KEY
                    search_engine: bing
                """,
            )
            self._write_env(env_path, "SERPAPI_API_KEY=serpapi_key")

            with patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.DEFAULT_DOTENV_PATH", Path(env_path)):
                tool = ApiWebSearchTool(
                    engine="serpapi", config_path=config_path, timeout_s=0.1, max_results_cap=10
                )
                out = tool.forward(query="hello world", max_results=1)
                self.assertEqual(out["query"], "hello world")
                self.assertEqual(len(out["results"]), 1)
                self.assertEqual(out["results"][0]["title"], "Example 1")
                self.assertEqual(out["results"][0]["url"], "https://example.com/1")
                self.assertEqual(out["results"][0]["snippet"], "Snippet 1")

                req = mock_urlopen.call_args_list[0].args[0]
                self.assertIn("https://serpapi.com/search.json?", req.full_url)
                self.assertIn("engine=bing", req.full_url)
                self.assertIn("q=hello+world", req.full_url)
                self.assertIn("num=1", req.full_url)

    @patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.urlopen")
    def test_api_web_search_uses_yaml_base_url(self, mock_urlopen):
        payload = {"results": [{"title": "T", "url": "https://example.com", "content": "S"}]}
        mock_urlopen.return_value = _DummyResponse(json.dumps(payload).encode("utf-8"))

        with tempfile.TemporaryDirectory() as td:
            config_path = str(Path(td) / "config.yaml")
            env_path = str(Path(td) / ".env")
            self._write_runtime_config(
                config_path,
                """
                tools:
                  tavily:
                    api_key_env: TAVILY_API_KEY
                    base_url: https://custom.tavily.example/search
                """,
            )
            self._write_env(env_path, "TAVILY_API_KEY=tavily_key")
            with patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.DEFAULT_DOTENV_PATH", Path(env_path)):
                tool = ApiWebSearchTool(config_path=config_path, timeout_s=0.1)
                tool.forward(query="hello", max_results=1)
                req = mock_urlopen.call_args_list[0].args[0]
                self.assertEqual(req.full_url, "https://custom.tavily.example/search")

    @patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.urlopen")
    def test_api_web_search_tavily_parse_and_limit(self, mock_urlopen):
        payload = {
            "results": [
                {"title": "Example 1", "url": "https://example.com/1", "content": "Snippet 1"},
                {"title": "Example 2", "url": "https://example.org", "content": ["Snippet", "2"]},
            ]
        }
        mock_urlopen.return_value = _DummyResponse(json.dumps(payload).encode("utf-8"))

        tool = ApiWebSearchTool(api_key="dummy_key", engine="tavily", timeout_s=0.1, max_results_cap=10)
        out = tool.forward(query="hello world", max_results=1)
        self.assertEqual(out["query"], "hello world")
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["title"], "Example 1")
        self.assertEqual(out["results"][0]["url"], "https://example.com/1")
        self.assertEqual(out["results"][0]["snippet"], "Snippet 1")

        req = mock_urlopen.call_args_list[0].args[0]
        self.assertEqual(req.full_url, "https://api.tavily.com/search")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["query"], "hello world")
        self.assertEqual(body["max_results"], 1)

    @patch("juice_agents.core.agent.tools.builtin.web.web_search_tools.urlopen")
    def test_api_web_search_serpapi_error_field_raises(self, mock_urlopen):
        payload = {"error": "Invalid API key"}
        mock_urlopen.return_value = _DummyResponse(json.dumps(payload).encode("utf-8"))

        tool = ApiWebSearchTool(api_key="dummy_key", engine="serpapi", timeout_s=0.1, max_results_cap=10)
        with self.assertRaises(RuntimeError):
            tool.forward(query="hello", max_results=1)


class _FakeBrowserSession:
    def __init__(self):
        self._url = "about:blank"

    def reconfigure(self, **kwargs):  # noqa: ANN003
        self.reconfigure_kwargs = kwargs

    def search_detailed(self, query, *, engine, max_results, tab_id=None):  # noqa: ANN001
        del tab_id
        return {
            "query": query,
            "engine": engine,
            "status": "ok",
            "message": "",
            "results": [
                {
                    "title": f"{engine} result",
                    "url": "https://example.com",
                    "snippet": f"{query}-{max_results}",
                }
            ],
        }

    def search(self, query, *, engine, max_results):
        return self.search_detailed(query, engine=engine, max_results=max_results)["results"]

    def open_url(self, url):
        self._url = url
        return {"url": url, "title": "Example"}

    def go_back(self):
        self._url = "https://example.org/back"
        return {"url": self._url, "title": "Back"}

    def close_popups(self):
        return {"status": "ok", "url": self._url}

    def find_text(self, text, nth_result=1):
        return {"matches": 3, "focused_index": nth_result, "preview": text, "url": self._url}

    def screenshot(self, *, save_path=None, full_page=True):
        return {"path": save_path or ".juice/browser_screenshots/mock.png", "url": self._url, "full_page": full_page}


class _FakeLayout:
    def __init__(self):
        self.browser_screenshots_dir = Path("/tmp/project/.juice/runners/run123/browser_screenshots")


class _FakeRunnerContext:
    def __init__(self, actor_name: str = "root"):
        self.layout = _FakeLayout()
        self.runner_id = "run123"
        self.actor_name = actor_name


class _FakeOwnerAgent:
    def __init__(self, actor_name: str = "root"):
        self.runner_context = _FakeRunnerContext(actor_name)


class BrowserToolsTests(unittest.TestCase):
    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_browser_search_tool_forward(self, mock_get_session):
        mock_get_session.return_value = _FakeBrowserSession()
        tool = BrowserSearchTool(engine="bing", max_results_cap=10)
        out = tool.forward(query="playwright", max_results=1)
        self.assertEqual(out["query"], "playwright")
        self.assertEqual(out["engine"], "bing")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["title"], "bing result")

    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_browser_search_tool_allows_call_level_engine(self, mock_get_session):
        mock_get_session.return_value = _FakeBrowserSession()
        tool = BrowserSearchTool(engine="bing", max_results_cap=10)
        out = tool.forward(query="马斯克", engine="baidu", max_results=1)

        self.assertEqual(out["engine"], "baidu")
        self.assertEqual(out["results"][0]["title"], "baidu result")

    def test_browser_search_schema_describes_visible_engine_fallback(self):
        self.assertIn("requested_engine", BrowserSearchTool.outputs)
        self.assertIn("fallback", BrowserSearchTool.outputs["status"]["description"])

    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_browser_open_url_tool_forward(self, mock_get_session):
        mock_get_session.return_value = _FakeBrowserSession()
        tool = BrowserOpenUrlTool()
        out = tool.forward(url="https://example.com")
        self.assertEqual(out["url"], "https://example.com")
        self.assertEqual(out["title"], "Example")

    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_browser_go_back_tool_forward(self, mock_get_session):
        mock_get_session.return_value = _FakeBrowserSession()
        tool = BrowserGoBackTool()
        out = tool.forward()
        self.assertIn("back", out["url"])

    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_browser_close_popups_tool_forward(self, mock_get_session):
        mock_get_session.return_value = _FakeBrowserSession()
        tool = BrowserClosePopupsTool()
        out = tool.forward()
        self.assertEqual(out["status"], "ok")

    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_browser_find_text_tool_forward(self, mock_get_session):
        mock_get_session.return_value = _FakeBrowserSession()
        tool = BrowserFindTextTool()
        out = tool.forward(text="hello", nth_result=2)
        self.assertEqual(out["matches"], 3)
        self.assertEqual(out["focused_index"], 2)
        self.assertEqual(out["preview"], "hello")

    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_browser_screenshot_tool_forward(self, mock_get_session):
        mock_get_session.return_value = _FakeBrowserSession()
        tool = BrowserScreenshotTool()
        out = tool.forward(save_path="/tmp/s.png", full_page=False)
        self.assertEqual(out["path"], "/tmp/s.png")
        self.assertFalse(out["full_page"])

    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_browser_screenshot_tool_defaults_to_runner_screenshot_dir(self, mock_get_session):
        mock_get_session.return_value = _FakeBrowserSession()
        tool = BrowserScreenshotTool()
        tool.bind_owner_agent(_FakeOwnerAgent())

        out = tool.forward(full_page=True)

        self.assertTrue(out["path"].startswith("/tmp/project/.juice/runners/run123/browser_screenshots/"))
        self.assertTrue(out["path"].endswith(".png"))
        self.assertEqual(mock_get_session.call_args.args[0], "run123:browser")

    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_navigation_tools_use_runner_scoped_browser_session(self, mock_get_session):
        mock_get_session.return_value = _FakeBrowserSession()
        tool = BrowserOpenUrlTool()
        tool.bind_owner_agent(_FakeOwnerAgent())

        tool.forward(url="https://example.com")

        self.assertEqual(mock_get_session.call_args.args[0], "run123:browser")

    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_root_and_subagent_tools_share_runner_scoped_browser_session(self, mock_get_session):
        mock_get_session.return_value = _FakeBrowserSession()
        root_tool = BrowserOpenUrlTool()
        root_tool.bind_owner_agent(_FakeOwnerAgent("root"))
        subagent_tool = BrowserOpenUrlTool()
        subagent_tool.bind_owner_agent(_FakeOwnerAgent("general"))

        root_tool.forward(url="https://example.com")
        subagent_tool.forward(url="https://example.org")

        self.assertEqual([call.args[0] for call in mock_get_session.call_args_list], ["run123:browser", "run123:browser"])

    @patch("juice_agents.core.agent.tools.builtin.web.browser.policy_tools.get_browser_session")
    def test_navigation_and_interaction_tools_share_runner_session(self, mock_get_session):
        class _SharedBrowserSession:
            def __init__(self):
                self.url = "about:blank"
                self.calls = []

            def reconfigure(self, **kwargs):  # noqa: ANN003
                self.calls.append(("reconfigure", kwargs))

            def current_url(self, tab_id=None):  # noqa: ANN001
                del tab_id
                return self.url

            def open_url(self, url):
                self.url = url
                self.calls.append(("open_url", url))
                return {"url": url, "title": "Example"}

            def fill(self, selector, value, **kwargs):  # noqa: ANN001
                self.calls.append(("fill", selector, value, kwargs))
                return {"status": "ok", "url": self.url}

        session = _SharedBrowserSession()
        mock_get_session.return_value = session
        owner = _FakeOwnerAgent()
        open_url = BrowserOpenUrlTool()
        open_url.bind_owner_agent(owner)
        fill = BrowserFillTool()
        fill.bind_owner_agent(owner)

        open_url.forward(url="https://example.com")
        fill.forward(selector="#q", value="hello")

        self.assertEqual([call.args[0] for call in mock_get_session.call_args_list], ["run123:browser", "run123:browser"])
        self.assertIn(("open_url", "https://example.com"), session.calls)
        self.assertTrue(any(call[0] == "fill" for call in session.calls))

    def test_get_browser_session_reconfigure_timeout(self):
        import juice_agents.core.agent.tools.builtin.web.browser.session as session_module

        old_sessions = dict(session_module._BROWSER_SESSIONS)
        try:
            session_module._BROWSER_SESSIONS.clear()
            s1 = get_browser_session(headless=True, timeout_ms=15000)
            self.assertEqual(s1.timeout_ms, 15000)
            s2 = get_browser_session(headless=True, timeout_ms=25000)
            self.assertIs(s1, s2)
            self.assertEqual(s2.timeout_ms, 25000)
        finally:
            session_module._BROWSER_SESSIONS.clear()
            session_module._BROWSER_SESSIONS.update(old_sessions)

    def test_search_detailed_runs_on_browser_owner_thread(self):
        class _FakePage:
            def __init__(self):
                self.goto_calls = []

            def goto(self, url, wait_until=None):  # noqa: ANN001
                self.goto_calls.append((url, wait_until))

        session = PlaywrightBrowserSession()
        page = _FakePage()
        observed = {"ran_on_owner": False}

        def run_on_owner(callback):
            observed["ran_on_owner"] = True
            return callback()

        session.run_on_browser_thread = run_on_owner  # type: ignore[method-assign]
        session._resolve_tab = lambda tab_id=None: ("tab_1", page)  # type: ignore[method-assign]
        session._extract_bing_results = lambda browser_page, *, max_results: [  # type: ignore[method-assign]
            {"title": "Owner thread", "url": "https://example.com", "snippet": str(max_results)}
        ]

        out = session.search_detailed("playwright", engine="bing", max_results=1)

        self.assertTrue(observed["ran_on_owner"])
        self.assertEqual(out["results"][0]["title"], "Owner thread")
        self.assertEqual(page.goto_calls[0][1], "domcontentloaded")

    def test_duckduckgo_empty_page_falls_back_to_visible_bing_results(self):
        """The live browser and the structured result must describe the same search page."""

        class _FakePage:
            def __init__(self):
                self.goto_calls = []

            def goto(self, url, wait_until=None):  # noqa: ANN001
                self.goto_calls.append((url, wait_until))

        session = PlaywrightBrowserSession()
        page = _FakePage()
        session.run_on_browser_thread = lambda callback: callback()  # type: ignore[method-assign]
        session._resolve_tab = lambda tab_id=None: ("tab_1", page)  # type: ignore[method-assign]
        session._extract_ddg_results = lambda browser_page, *, max_results: []  # type: ignore[method-assign]
        session._extract_bing_results = lambda browser_page, *, max_results: [  # type: ignore[method-assign]
            {"title": "Visible result", "url": "https://example.com", "snippet": str(max_results)}
        ]

        out = session.search_detailed("juice agents", engine="duckduckgo", max_results=1)

        self.assertEqual(len(page.goto_calls), 2)
        self.assertIn("duckduckgo.com/?q=juice+agents", page.goto_calls[0][0])
        self.assertIn("bing.com/search?q=juice+agents", page.goto_calls[1][0])
        self.assertEqual(out["requested_engine"], "duckduckgo")
        self.assertEqual(out["engine"], "bing")
        self.assertEqual(out["status"], "fallback")
        self.assertIn("DuckDuckGo", out["message"])
        self.assertEqual(out["results"][0]["title"], "Visible result")

    def test_duckduckgo_visible_fallback_continues_to_baidu_when_bing_is_blocked(self):
        """A second visible engine keeps Canvas useful when Bing shows a challenge page."""

        class _FakePage:
            def __init__(self):
                self.goto_calls = []

            def goto(self, url, wait_until=None):  # noqa: ANN001
                self.goto_calls.append((url, wait_until))

        session = PlaywrightBrowserSession()
        page = _FakePage()
        session.run_on_browser_thread = lambda callback: callback()  # type: ignore[method-assign]
        session._resolve_tab = lambda tab_id=None: ("tab_1", page)  # type: ignore[method-assign]
        session._extract_ddg_results = lambda browser_page, *, max_results: []  # type: ignore[method-assign]
        session._extract_bing_results = lambda browser_page, *, max_results: []  # type: ignore[method-assign]
        session._is_security_verification_page = lambda browser_page: False  # type: ignore[method-assign]
        session._extract_baidu_results = lambda browser_page, *, max_results: [  # type: ignore[method-assign]
            {"title": "可见结果", "url": "https://example.cn", "snippet": str(max_results)}
        ]

        out = session.search_detailed("薛之谦", engine="duckduckgo", max_results=1)

        self.assertEqual(len(page.goto_calls), 3)
        self.assertIn("bing.com/search", page.goto_calls[1][0])
        self.assertIn("baidu.com/s?wd=", page.goto_calls[2][0])
        self.assertEqual(out["requested_engine"], "duckduckgo")
        self.assertEqual(out["engine"], "baidu")
        self.assertEqual(out["status"], "fallback")
        self.assertEqual(out["results"][0]["title"], "可见结果")

    def test_screencast_recovers_from_playwright_thread_mismatch(self):
        class _FakePage:
            url = "about:blank"

            def title(self):
                return "Example"

        class _BadContext:
            def new_cdp_session(self, page):  # noqa: ANN001
                del page
                raise RuntimeError("Cannot switch to a different thread")

            def close(self):
                return None

        class _FakeCdp:
            def __init__(self):
                self.sent = []
                self.handlers = {}

            def send(self, name, payload=None):  # noqa: ANN001
                self.sent.append((name, payload))

            def on(self, name, handler):  # noqa: ANN001
                self.handlers[name] = handler

        class _GoodContext:
            def __init__(self):
                self.cdp = _FakeCdp()

            def new_cdp_session(self, page):  # noqa: ANN001
                del page
                return self.cdp

        session = PlaywrightBrowserSession()
        calls = {"ensure_started": 0}
        good_context = _GoodContext()
        session._active_tab_id = "tab_1"
        session._tabs = {"tab_1": _FakePage()}

        def ensure_started():
            calls["ensure_started"] += 1
            session._active_tab_id = "tab_1"
            session._tabs = {"tab_1": _FakePage()}
            session._context = _BadContext() if calls["ensure_started"] == 1 else good_context

        session._ensure_started = ensure_started  # type: ignore[method-assign]

        token = session._start_screencast(lambda payload: None)

        self.assertEqual(token, "screencast_1")
        self.assertEqual(calls["ensure_started"], 2)
        self.assertIn(("Page.startScreencast", {"format": "jpeg", "quality": 72, "everyNthFrame": 1}), good_context.cdp.sent)

    def test_register_page_is_idempotent_for_same_playwright_page(self):
        class _FakePage:
            url = "https://example.com"

            def __init__(self):
                self.default_timeouts = []
                self.handlers = {}

            def set_default_timeout(self, timeout_ms):  # noqa: ANN001
                self.default_timeouts.append(timeout_ms)

            def on(self, name, handler):  # noqa: ANN001
                self.handlers[name] = handler

            def title(self):
                return "Example"

        session = PlaywrightBrowserSession(timeout_ms=15000)
        first_page = _FakePage()
        second_page = _FakePage()

        first_tab_id = session._register_page(first_page, make_active=True)
        session._register_page(second_page, make_active=True)
        duplicate_tab_id = session._register_page(first_page, make_active=True)

        self.assertEqual(first_tab_id, duplicate_tab_id)
        self.assertEqual(list(session._tabs), ["tab_1", "tab_2"])
        self.assertIs(session._tabs[first_tab_id], first_page)
        self.assertEqual(session._active_tab_id, first_tab_id)
        self.assertEqual(first_page.default_timeouts, [15000])
        self.assertIn(first_tab_id, session._console_logs)
        self.assertIn(first_tab_id, session._page_errors)

    def test_new_tab_reuses_initial_blank_page_in_fresh_session(self):
        class _FakePage:
            url = "about:blank"

            def __init__(self):
                self.default_timeouts = []
                self.handlers = {}

            def set_default_timeout(self, timeout_ms):  # noqa: ANN001
                self.default_timeouts.append(timeout_ms)

            def on(self, name, handler):  # noqa: ANN001
                self.handlers[name] = handler

            def title(self):
                return ""

        class _FakeContext:
            def __init__(self):
                self.created_pages = []

            def new_page(self):
                page = _FakePage()
                self.created_pages.append(page)
                return page

        session = PlaywrightBrowserSession(timeout_ms=15000)
        context = _FakeContext()

        def ensure_started():
            if session._context is not None and session._tabs:
                return
            session._context = context
            session._register_page(context.new_page(), make_active=True)

        session._ensure_started = ensure_started  # type: ignore[method-assign]

        out = session.new_tab()

        self.assertEqual(out["tab_id"], "tab_1")
        self.assertEqual(len(context.created_pages), 1)
        self.assertEqual(len(session.list_tabs()["tabs"]), 1)

    def test_tab_snapshot_and_status_do_not_start_playwright(self):
        session = PlaywrightBrowserSession(timeout_ms=15000)

        def fail_start():
            raise AssertionError("read-only tab snapshot should not start Playwright")

        session._ensure_started = fail_start  # type: ignore[method-assign]

        self.assertEqual(session.tab_snapshot()["tabs"], [])
        self.assertEqual(session.list_tabs()["tabs"], [])
        self.assertEqual(session.status()["active_tab_id"], "")

    def test_open_url_retries_after_timeout(self):
        import juice_agents.core.agent.tools.builtin.web.browser.session as session_module

        class _FakePage:
            def __init__(self):
                self.url = "about:blank"
                self.calls = 0

            def goto(self, url, wait_until=None, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("Timeout 15000ms exceeded.")
                self.url = url

            def title(self):
                return "Example"

            def set_default_timeout(self, timeout_ms):  # noqa: ARG002
                return None

        old_sessions = dict(session_module._BROWSER_SESSIONS)
        try:
            session_module._BROWSER_SESSIONS.clear()
            session = PlaywrightBrowserSession(timeout_ms=1000)
            fake_page = _FakePage()
            session._page = fake_page
            session_module._BROWSER_SESSIONS["default"] = session

            tool = BrowserOpenUrlTool()
            out = tool.forward(url="https://example.com")
            self.assertEqual(out["title"], "Example")
            self.assertEqual(fake_page.calls, 2)
        finally:
            session_module._BROWSER_SESSIONS.clear()
            session_module._BROWSER_SESSIONS.update(old_sessions)

    @patch("juice_agents.core.agent.tools.builtin.web.web_search_tools._bing_search_rss")
    def test_session_search_fallback_to_bing_rss_when_empty(self, mock_bing_rss):
        class _FakePage:
            def goto(self, url, wait_until=None, timeout=None):  # noqa: ARG002
                return None

            def query_selector_all(self, selector):  # noqa: ARG002
                return []

            def set_default_timeout(self, timeout_ms):  # noqa: ARG002
                return None

        mock_bing_rss.return_value = [
            {"title": "T", "url": "https://example.com", "snippet": "S"}
        ]
        session = PlaywrightBrowserSession(timeout_ms=15000)
        session._page = _FakePage()
        out = session.search("playwright", engine="bing", max_results=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "T")
        self.assertEqual(mock_bing_rss.call_count, 1)

    @patch("juice_agents.core.agent.tools.builtin.web.web_search_tools._duckduckgo_search_html")
    def test_session_search_fallback_to_ddg_html_when_empty(self, mock_ddg_html):
        class _FakePage:
            def goto(self, url, wait_until=None, timeout=None):  # noqa: ARG002
                return None

            def query_selector_all(self, selector):  # noqa: ARG002
                return []

            def set_default_timeout(self, timeout_ms):  # noqa: ARG002
                return None

        mock_ddg_html.return_value = [
            {"title": "D", "url": "https://example.org", "snippet": "DDG"}
        ]
        session = PlaywrightBrowserSession(timeout_ms=15000)
        session._page = _FakePage()
        out = session.search("playwright", engine="duckduckgo", max_results=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "D")
        self.assertEqual(mock_ddg_html.call_count, 1)

    def test_session_search_baidu_security_verification_returns_blocked(self):
        class _FakePage:
            url = "about:blank"

            def goto(self, url, wait_until=None):  # noqa: ARG002
                self.url = "https://wappass.baidu.com/static/captcha/tuxing_v2.html"

            def title(self):
                return "百度安全验证"

            def set_default_timeout(self, timeout_ms):  # noqa: ARG002
                return None

        session = PlaywrightBrowserSession(timeout_ms=15000)
        session._page = _FakePage()
        out = session.search_detailed("马斯克", engine="baidu", max_results=1)

        self.assertEqual(out["engine"], "baidu")
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["results"], [])
        self.assertIn("安全验证", out["message"])


class _FakeEnhancedBrowserSession:
    def __init__(self):
        self.calls = []
        self.closed = []

    def current_url(self, tab_id=None):  # noqa: ANN001
        del tab_id
        return "http://localhost/form"

    def click(self, selector, **kwargs):  # noqa: ANN001
        self.calls.append(("click", selector, kwargs))
        return {"status": "ok", "url": self.current_url(), "selector": selector}

    def fill(self, selector, value, **kwargs):  # noqa: ANN001
        self.calls.append(("fill", selector, value, kwargs))
        return {"status": "ok", "url": self.current_url(), "selector": selector}

    def wait_for(self, **kwargs):
        self.calls.append(("wait_for", kwargs))
        return {"matched": True, "elapsed_ms": 3, "url": self.current_url()}

    def get_text(self, **kwargs):
        self.calls.append(("get_text", kwargs))
        return {"text": "hello", "truncated": False, "url": self.current_url()}

    def list_tabs(self):
        return {"active_tab_id": "tab_1", "tabs": [{"tab_id": "tab_1", "url": self.current_url()}]}

    def new_tab(self, url=None, make_active=True):  # noqa: ANN001
        self.calls.append(("new_tab", url, make_active))
        return {"tab_id": "tab_2", "url": url or "about:blank", "title": ""}

    def close_tab(self, tab_id=None):  # noqa: ANN001
        self.closed.append(tab_id)
        return {"closed_tab_id": tab_id or "tab_2", "active_tab_id": "tab_1"}


class _FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def insert_text(self, value):
        if self.page.active_element is not None:
            self.page.active_element.value += value


class _FakeEditableElement:
    def __init__(
        self,
        *,
        tag="input",
        attrs=None,
        visible=True,
        enabled=True,
        editable=True,
        box=None,
        text="",
        value="",
    ):
        self.tag = tag
        self.attrs = dict(attrs or {})
        self.visible = visible
        self.enabled = enabled
        self.editable = editable
        self.box = box if box is not None else {"x": 10, "y": 10, "width": 120, "height": 24}
        self.text = text
        self.value = value


class _FakeEditableLocator:
    def __init__(self, page, elements):
        self.page = page
        self.elements = list(elements)

    @property
    def first(self):
        return _FakeEditableLocator(self.page, self.elements[:1])

    def nth(self, index):
        return _FakeEditableLocator(self.page, self.elements[index : index + 1])

    def count(self):
        return len(self.elements)

    def _one(self):
        if not self.elements:
            raise RuntimeError("no element")
        return self.elements[0]

    def is_visible(self):
        return self._one().visible

    def is_enabled(self):
        return self._one().enabled

    def is_editable(self):
        return self._one().editable

    def bounding_box(self):
        return self._one().box if self._one().visible else None

    def evaluate(self, expression):  # noqa: ANN001
        element = self._one()
        if "attributes" in expression:
            return element.attrs
        if "tagName" in expression:
            return element.tag
        return None

    def fill(self, value):
        self._one().value = str(value)

    def click(self):
        self.page.active_element = self._one()

    def press(self, key):
        element = self._one()
        if key in {"Control+A", "Backspace"}:
            element.value = ""

    def input_value(self, timeout=None):  # noqa: ARG002
        return self._one().value

    def inner_text(self, timeout=None):  # noqa: ARG002
        return self._one().text


class _FakeFillPage:
    url = "http://localhost/form"

    def __init__(self, *, target, candidates=None, title="Example", body_text=""):
        self.target = target
        self.candidates = list(candidates or [])
        self._title = title
        self.body_text = body_text
        self.active_element = None
        self.keyboard = _FakeKeyboard(self)

    def set_default_timeout(self, timeout_ms):  # noqa: ARG002
        return None

    def on(self, event, callback):  # noqa: ARG002
        return None

    def title(self):
        return self._title

    def locator(self, selector):
        if selector == "#kw":
            return _FakeEditableLocator(self, [self.target])
        if selector == "body":
            return _FakeEditableLocator(self, [_FakeEditableElement(tag="body", text=self.body_text)])
        if "input:not([type='hidden'])" in selector:
            return _FakeEditableLocator(self, self.candidates)
        return _FakeEditableLocator(self, [])


class EnhancedBrowserToolTests(unittest.TestCase):
    def test_interaction_tools_delegate_to_session_with_localhost_policy(self):
        session = _FakeEnhancedBrowserSession()
        click = BrowserClickTool()
        click._session = lambda: session  # type: ignore[method-assign]
        fill = BrowserFillTool()
        fill._session = lambda: session  # type: ignore[method-assign]

        click_out = click.forward("text=Save", click_count=2)
        fill_out = fill.forward("#name", "Ada")

        self.assertEqual(click_out["status"], "ok")
        self.assertEqual(fill_out["status"], "ok")
        self.assertEqual(session.calls[0][0], "click")
        self.assertEqual(session.calls[1][0], "fill")

    def test_inspection_tools_delegate_to_session(self):
        session = _FakeEnhancedBrowserSession()
        wait = BrowserWaitForTool()
        wait._session = lambda: session  # type: ignore[method-assign]
        get_text = BrowserGetTextTool()
        get_text._session = lambda: session  # type: ignore[method-assign]

        self.assertTrue(wait.forward(selector="#ready")["matched"])
        self.assertEqual(get_text.forward(selector="body")["text"], "hello")

    def test_tab_tools_delegate_to_session(self):
        session = _FakeEnhancedBrowserSession()
        list_tabs = BrowserListTabsTool()
        list_tabs._session = lambda: session  # type: ignore[method-assign]
        new_tab = BrowserNewTabTool()
        new_tab._session = lambda: session  # type: ignore[method-assign]
        close_tab = BrowserCloseTabTool()
        close_tab._session = lambda: session  # type: ignore[method-assign]

        self.assertEqual(list_tabs.forward()["active_tab_id"], "tab_1")
        self.assertEqual(new_tab.forward("http://localhost/next")["tab_id"], "tab_2")
        self.assertEqual(close_tab.forward("tab_2")["closed_tab_id"], "tab_2")

    def test_session_fill_hidden_selector_returns_diagnostics_without_waiting(self):
        hidden = _FakeEditableElement(
            attrs={"id": "kw", "name": "wd"},
            visible=False,
            editable=True,
            box=None,
        )
        session = PlaywrightBrowserSession(timeout_ms=15000)
        session._page = _FakeFillPage(target=hidden)

        out = session.fill("#kw", "马斯克")

        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "element_not_visible")
        self.assertEqual(out["matched_count"], 1)
        self.assertFalse(out["visible"])
        self.assertEqual(out["candidates"], [])

    def test_session_fill_hidden_selector_uses_single_visible_candidate(self):
        hidden = _FakeEditableElement(attrs={"id": "kw"}, visible=False, editable=True, box=None)
        visible = _FakeEditableElement(attrs={"name": "q", "placeholder": "Search"}, visible=True, value="")
        session = PlaywrightBrowserSession(timeout_ms=15000)
        session._page = _FakeFillPage(target=hidden, candidates=[visible])

        out = session.fill("#kw", "Ada")

        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["fallback_used"])
        self.assertEqual(out["filled_selector"], 'input[name="q"]')
        self.assertEqual(visible.value, "Ada")

    def test_session_fill_security_verification_returns_blocked(self):
        target = _FakeEditableElement(attrs={"id": "kw"}, visible=True)
        page = _FakeFillPage(target=target, title="百度安全验证", body_text="请完成下方验证后继续操作")
        page.url = "https://wappass.baidu.com/static/captcha/tuxing_v2.html"
        session = PlaywrightBrowserSession(timeout_ms=15000)
        session._page = page

        out = session.fill("#kw", "马斯克")

        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["reason"], "security_verification")
        self.assertIn("安全验证", out["message"])

    def test_risky_tool_is_disabled_by_default(self):
        tool = BrowserEvaluateTool()
        tool._session = lambda: _FakeEnhancedBrowserSession()  # type: ignore[method-assign]
        with self.assertRaises(PermissionError):
            tool.forward("() => document.title")

    def test_storage_state_path_is_restricted_to_state_dir(self):
        with tempfile.TemporaryDirectory() as td:
            session = PlaywrightBrowserSession(state_dir=Path(td) / "state")
            safe = session._safe_state_path("demo.json")
            self.assertTrue(str(safe).startswith(str((Path(td) / "state").resolve())))
            with self.assertRaises(ValueError):
                session._safe_state_path(Path(td) / "outside.json")

    def test_browser_session_runs_callbacks_on_owner_thread(self):
        session = PlaywrightBrowserSession()
        caller_thread_id = threading.get_ident()
        owner_thread_id = session.run_on_browser_thread(lambda: threading.get_ident())

        self.assertNotEqual(owner_thread_id, caller_thread_id)
        self.assertEqual(session.run_on_browser_thread(lambda: threading.get_ident()), owner_thread_id)


class PlaywrightErrorFormattingTests(unittest.TestCase):
    def test_parse_version_tuple(self):
        self.assertEqual(_parse_version_tuple("2.27"), (2, 27))
        self.assertEqual(_parse_version_tuple("2.27.1"), (2, 27, 1))
        self.assertEqual(_parse_version_tuple(""), ())
        self.assertEqual(_parse_version_tuple("2.x"), (2,))

    def test_format_playwright_error_contains_glibc_hint(self):
        exc = AttributeError("'PlaywrightContextManager' object has no attribute '_playwright'")
        msg = _format_playwright_start_error(exc, libc_info=("glibc", "2.18"))
        self.assertIn("Playwright 启动失败", msg)
        self.assertIn("glibc=2.18", msg)
        self.assertIn(">= 2.27", msg)
        self.assertIn("AttributeError", msg)

    def test_format_playwright_error_without_glibc_hint(self):
        exc = RuntimeError("Connection closed")
        msg = _format_playwright_start_error(exc, libc_info=("glibc", "2.31"))
        self.assertIn("Playwright 启动失败", msg)
        self.assertNotIn("glibc=2.31", msg)

    def test_format_playwright_error_contains_asyncio_owner_thread_hint(self):
        exc = RuntimeError("It looks like you are using Playwright Sync API inside the asyncio loop.")
        msg = _format_playwright_start_error(exc, libc_info=("glibc", "2.31"))
        self.assertIn("asyncio 事件循环线程", msg)
        self.assertIn("owner 线程队列", msg)


if __name__ == "__main__":
    unittest.main()
