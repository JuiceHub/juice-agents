"""Browser navigation and screenshot tools.

These tools are simple wrappers over the shared :class:`PlaywrightBrowserSession`
helpers. They are owner-aware so navigation, inspection, and interaction tools
inside the same Runner/Agent all operate on the same browser session.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .policy_tools import BrowserTool
from ....runtime.base_tools import LIST_OBSERVATION_CHARS
from .session import (
    DEFAULT_BROWSER_SCREENSHOTS_DIR,
    DEFAULT_USER_AGENT,
)


class BrowserSearchTool(BrowserTool):
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "browser_search"
    is_read_only = True
    description = "使用浏览器执行网页搜索，返回标题/链接/摘要；支持 duckduckgo/ddg/bing/baidu"
    inputs = {
        "query": {"type": "string", "description": "搜索关键词/问题"},
        "engine": {
            "type": "string",
            "description": "可选搜索引擎：duckduckgo/ddg/bing/baidu，默认使用工具配置",
            "required": False,
        },
        "max_results": {
            "type": "integer",
            "description": "最大返回结果数，默认 5（会被 cap 限制）",
            "required": False,
        },
        "tab_id": {"type": "string", "description": "可选：目标 tab id，默认 active tab", "required": False},
    }
    outputs = {
        "results": {"type": "list", "description": "搜索结果列表，每项包含 title/url/snippet"},
        "query": {"type": "string", "description": "原始 query"},
        "requested_engine": {"type": "string", "description": "发生可见降级时记录用户最初请求的搜索引擎"},
        "engine": {"type": "string", "description": "实际使用的搜索引擎"},
        "status": {"type": "string", "description": "ok、fallback 或 blocked"},
        "message": {"type": "string", "description": "降级或受阻时的状态说明"},
    }

    DEFAULT_MAX_RESULTS = 5
    DEFAULT_MAX_RESULTS_CAP = 20

    def __init__(
        self,
        *,
        engine: str = "duckduckgo",
        max_results_cap: int = DEFAULT_MAX_RESULTS_CAP,
        headless: bool = True,
        timeout_ms: int = 15000,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        super().__init__()
        self.engine = str(engine or "duckduckgo").strip().lower()
        self.max_results_cap = int(max_results_cap)
        self.headless = bool(headless)
        self.timeout_ms = int(timeout_ms)
        self.user_agent = str(user_agent or DEFAULT_USER_AGENT)

    def _browser_session(self) -> Any:
        session = self._session()
        session.reconfigure(headless=self.headless, timeout_ms=self.timeout_ms, user_agent=self.user_agent)
        return session

    def forward(
        self,
        query: str,
        max_results: int | None = None,
        tab_id: str | None = None,
        engine: str | None = None,
    ) -> Dict[str, Any]:
        from ..web_search_tools import _resolve_max_results, _validate_query

        q = _validate_query(query)
        effective_engine = str(engine or self.engine).strip().lower()
        mr = _resolve_max_results(
            max_results=max_results,
            default_value=self.DEFAULT_MAX_RESULTS,
            cap_value=self.max_results_cap,
            logger_name="BrowserSearchTool",
        )
        session = self._browser_session()
        if tab_id is None:
            return session.search_detailed(q, engine=effective_engine, max_results=mr)
        else:
            return session.search_detailed(q, engine=effective_engine, max_results=mr, tab_id=tab_id)


class BrowserOpenUrlTool(BrowserTool):
    name = "browser_open_url"
    is_read_only = True
    description = "在浏览器中打开网页 URL"
    inputs = {
        "url": {"type": "string", "description": "目标网页 URL"},
        "tab_id": {"type": "string", "description": "可选：目标 tab id，默认 active tab", "required": False},
    }
    outputs = {
        "url": {"type": "string", "description": "当前页面 URL"},
        "title": {"type": "string", "description": "当前页面标题"},
    }

    def __init__(self, *, headless: bool = True, timeout_ms: int = 15000) -> None:
        super().__init__()
        self.headless = bool(headless)
        self.timeout_ms = int(timeout_ms)

    def _browser_session(self) -> Any:
        session = self._session()
        session.reconfigure(headless=self.headless, timeout_ms=self.timeout_ms)
        return session

    def forward(self, url: str, tab_id: str | None = None) -> Dict[str, Any]:
        session = self._browser_session()
        return session.open_url(url) if tab_id is None else session.open_url(url, tab_id=tab_id)


class BrowserGoBackTool(BrowserOpenUrlTool):
    name = "browser_go_back"
    is_read_only = True
    description = "浏览器返回上一页"
    inputs = {"tab_id": {"type": "string", "description": "可选：目标 tab id，默认 active tab", "required": False}}
    outputs = {
        "url": {"type": "string", "description": "当前页面 URL"},
        "title": {"type": "string", "description": "当前页面标题"},
    }

    def forward(self, tab_id: str | None = None) -> Dict[str, Any]:
        session = self._browser_session()
        return session.go_back() if tab_id is None else session.go_back(tab_id=tab_id)


class BrowserClosePopupsTool(BrowserOpenUrlTool):
    name = "browser_close_popups"
    is_read_only = True
    description = "尝试通过 Escape 关闭弹窗或浮层"
    inputs = {"tab_id": {"type": "string", "description": "可选：目标 tab id，默认 active tab", "required": False}}
    outputs = {
        "status": {"type": "string", "description": "执行状态"},
        "url": {"type": "string", "description": "当前页面 URL"},
    }

    def forward(self, tab_id: str | None = None) -> Dict[str, Any]:
        session = self._browser_session()
        return session.close_popups() if tab_id is None else session.close_popups(tab_id=tab_id)


class BrowserFindTextTool(BrowserOpenUrlTool):
    name = "browser_find_text"
    is_read_only = True
    description = "在当前页面查找文本并聚焦到第 N 个匹配"
    inputs = {
        "text": {"type": "string", "description": "要查找的文本"},
        "nth_result": {
            "type": "integer",
            "description": "第几个匹配（从 1 开始，默认 1）",
            "required": False,
        },
        "tab_id": {"type": "string", "description": "可选：目标 tab id，默认 active tab", "required": False},
    }
    outputs = {
        "matches": {"type": "integer", "description": "匹配总数"},
        "focused_index": {"type": "integer", "description": "当前聚焦序号"},
        "preview": {"type": "string", "description": "聚焦文本预览"},
        "url": {"type": "string", "description": "当前页面 URL"},
    }

    def forward(self, text: str, nth_result: int = 1, tab_id: str | None = None) -> Dict[str, Any]:
        session = self._browser_session()
        if tab_id is None:
            return session.find_text(text=text, nth_result=nth_result)
        return session.find_text(text=text, nth_result=nth_result, tab_id=tab_id)


class BrowserScreenshotTool(BrowserOpenUrlTool):
    name = "browser_screenshot"
    is_read_only = True
    description = "对当前页面截图并返回本地图片路径"
    inputs = {
        "save_path": {
            "type": "string",
            "description": "可选：保存路径（默认写入当前 runner 的 browser_screenshots）",
            "required": False,
        },
        "full_page": {
            "type": "boolean",
            "description": "可选：是否截取整页，默认 true",
            "required": False,
        },
        "tab_id": {"type": "string", "description": "可选：目标 tab id，默认 active tab", "required": False},
    }
    outputs = {
        "path": {"type": "string", "description": "截图本地路径"},
        "url": {"type": "string", "description": "截图时页面 URL"},
        "full_page": {"type": "boolean", "description": "是否整页截图"},
    }

    def __init__(self, *, headless: bool = True, timeout_ms: int = 15000) -> None:
        super().__init__(headless=headless, timeout_ms=timeout_ms)
        self._default_dir: Path = Path(DEFAULT_BROWSER_SCREENSHOTS_DIR)

    def bind_owner_agent(self, agent: Any) -> None:
        super().bind_owner_agent(agent)
        context = getattr(agent, "runner_context", None)
        layout = getattr(context, "layout", None)
        screenshots_dir = getattr(layout, "browser_screenshots_dir", None)
        if screenshots_dir is not None:
            self._default_dir = Path(screenshots_dir)

    def _default_save_path(self) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(self._default_dir / f"screenshot_{ts}.png")

    def forward(self, save_path: str | None = None, full_page: bool = True, tab_id: str | None = None) -> Dict[str, Any]:
        effective_save_path = save_path or self._default_save_path()
        session = self._browser_session()
        if tab_id is None:
            return session.screenshot(save_path=effective_save_path, full_page=full_page)
        return session.screenshot(save_path=effective_save_path, full_page=full_page, tab_id=tab_id)


BROWSER_NAVIGATION_TOOLS = {
    "browser_search": BrowserSearchTool(),
    "browser_open_url": BrowserOpenUrlTool(),
    "browser_go_back": BrowserGoBackTool(),
    "browser_close_popups": BrowserClosePopupsTool(),
    "browser_find_text": BrowserFindTextTool(),
    "browser_screenshot": BrowserScreenshotTool(),
}


__all__ = [
    "BROWSER_NAVIGATION_TOOLS",
    "BrowserClosePopupsTool",
    "BrowserFindTextTool",
    "BrowserGoBackTool",
    "BrowserOpenUrlTool",
    "BrowserScreenshotTool",
    "BrowserSearchTool",
]
