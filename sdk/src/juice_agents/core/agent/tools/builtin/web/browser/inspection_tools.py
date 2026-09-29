"""Read-oriented browser inspection and diagnostics tools."""

from __future__ import annotations

from typing import Any

from .policy_tools import BrowserTool
from ....runtime.base_tools import LIST_OBSERVATION_CHARS, SHORT_OBSERVATION_CHARS


class BrowserStatusTool(BrowserTool):
    max_observation_chars = SHORT_OBSERVATION_CHARS
    name = "browser_status"
    is_read_only = True
    description = "查看内置浏览器 backend、当前 tab 和录屏状态"
    inputs = {}
    outputs = {"backend": {"type": "string", "description": "浏览器 backend"}, "tabs": {"type": "list", "description": "tab 列表"}}

    def forward(self) -> dict[str, Any]:
        return self._session().status()


class BrowserWaitForTool(BrowserTool):
    name = "browser_wait_for"
    is_read_only = True
    description = "等待元素、文本、URL pattern 或 networkidle"
    inputs = {
        "selector": {"type": "string", "description": "可选 locator", "required": False},
        "state": {"type": "string", "description": "visible/attached/hidden/detached，默认 visible", "required": False},
        "text": {"type": "string", "description": "可选等待文本", "required": False},
        "url_pattern": {"type": "string", "description": "可选 URL glob pattern", "required": False},
        "network_idle": {"type": "boolean", "description": "是否等待 networkidle", "required": False},
        "timeout_ms": {"type": "integer", "description": "超时毫秒数", "required": False},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = {"matched": {"type": "boolean", "description": "是否匹配"}, "elapsed_ms": {"type": "integer", "description": "等待耗时"}}

    def forward(self, selector: str | None = None, state: str = "visible", text: str | None = None, url_pattern: str | None = None, network_idle: bool = False, timeout_ms: int | None = None, tab_id: str | None = None) -> dict[str, Any]:
        return self._session().wait_for(selector=selector, state=state, text=text, url_pattern=url_pattern, network_idle=network_idle, timeout_ms=timeout_ms, tab_id=tab_id)


class BrowserGetTextTool(BrowserTool):
    name = "browser_get_text"
    is_read_only = True
    description = "读取当前页面或指定元素的 innerText"
    inputs = {
        "selector": {"type": "string", "description": "可选 locator，不填读取 body", "required": False},
        "max_chars": {"type": "integer", "description": "最大返回字符数，默认 50000", "required": False},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = {"text": {"type": "string", "description": "文本内容"}, "truncated": {"type": "boolean", "description": "是否截断"}}

    def forward(self, selector: str | None = None, max_chars: int = 50000, tab_id: str | None = None) -> dict[str, Any]:
        return self._session().get_text(selector=selector, max_chars=max_chars, tab_id=tab_id)


class BrowserGetHtmlTool(BrowserTool):
    name = "browser_get_html"
    is_read_only = True
    description = "读取当前页面或指定元素的 HTML"
    inputs = BrowserGetTextTool.inputs
    outputs = {"html": {"type": "string", "description": "HTML 内容"}, "truncated": {"type": "boolean", "description": "是否截断"}}

    def forward(self, selector: str | None = None, max_chars: int = 50000, tab_id: str | None = None) -> dict[str, Any]:
        return self._session().get_html(selector=selector, max_chars=max_chars, tab_id=tab_id)


class BrowserGetElementTool(BrowserTool):
    name = "browser_get_element"
    is_read_only = True
    description = "读取单个元素的 tag、属性、可见性和盒模型"
    inputs = {
        "selector": {"type": "string", "description": "Playwright locator"},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = {"attributes": {"type": "object", "description": "元素属性"}, "visible": {"type": "boolean", "description": "是否可见"}}

    def forward(self, selector: str, tab_id: str | None = None) -> dict[str, Any]:
        return self._session().get_element(selector, tab_id=tab_id)


class BrowserQueryElementsTool(BrowserTool):
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "browser_query_elements"
    is_read_only = True
    description = "查询多个元素并返回摘要，默认最多 20 个"
    inputs = {
        "selector": {"type": "string", "description": "Playwright locator"},
        "limit": {"type": "integer", "description": "最大返回元素数，默认 20", "required": False},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = {"items": {"type": "list", "description": "元素摘要列表"}, "count": {"type": "integer", "description": "匹配总数"}}

    def forward(self, selector: str, limit: int = 20, tab_id: str | None = None) -> dict[str, Any]:
        return self._session().query_elements(selector, limit=limit, tab_id=tab_id)


class BrowserExtractTool(BrowserTool):
    name = "browser_extract"
    is_read_only = True
    description = "按 {字段名: selector} schema 从页面抽取结构化文本"
    inputs = {
        "schema": {"type": "object", "description": "字段到 selector 的映射"},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
        "max_chars": {"type": "integer", "description": "每个字段最大字符数，默认 5000", "required": False},
    }
    outputs = {"data": {"type": "object", "description": "抽取结果"}}

    def forward(self, schema: dict[str, str], tab_id: str | None = None, max_chars: int = 5000) -> dict[str, Any]:
        return self._session().extract(schema, tab_id=tab_id, max_chars=max_chars)


class BrowserConsoleLogsTool(BrowserTool):
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "browser_console_logs"
    is_read_only = True
    description = "读取当前 tab 的 console 日志缓冲"
    inputs = {
        "level": {"type": "string", "description": "可选日志类型过滤，例如 error/warning/log", "required": False},
        "limit": {"type": "integer", "description": "最大返回条数，默认 50", "required": False},
        "clear": {"type": "boolean", "description": "读取后是否清空缓冲，默认 false", "required": False},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = {"logs": {"type": "list", "description": "console 日志"}}

    def forward(self, level: str | None = None, limit: int = 50, clear: bool = False, tab_id: str | None = None) -> dict[str, Any]:
        return self._session().console_logs(level=level, limit=limit, clear=clear, tab_id=tab_id)


class BrowserPageErrorsTool(BrowserTool):
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "browser_page_errors"
    is_read_only = True
    description = "读取当前 tab 的 pageerror 缓冲"
    inputs = {
        "limit": {"type": "integer", "description": "最大返回条数，默认 50", "required": False},
        "clear": {"type": "boolean", "description": "读取后是否清空缓冲，默认 false", "required": False},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = {"errors": {"type": "list", "description": "页面错误"}}

    def forward(self, limit: int = 50, clear: bool = False, tab_id: str | None = None) -> dict[str, Any]:
        return self._session().page_errors(limit=limit, clear=clear, tab_id=tab_id)


BROWSER_INSPECTION_TOOLS = {
    "browser_status": BrowserStatusTool(),
    "browser_wait_for": BrowserWaitForTool(),
    "browser_get_text": BrowserGetTextTool(),
    "browser_get_html": BrowserGetHtmlTool(),
    "browser_get_element": BrowserGetElementTool(),
    "browser_query_elements": BrowserQueryElementsTool(),
    "browser_extract": BrowserExtractTool(),
    "browser_console_logs": BrowserConsoleLogsTool(),
    "browser_page_errors": BrowserPageErrorsTool(),
}


__all__ = [
    "BROWSER_INSPECTION_TOOLS",
    "BrowserConsoleLogsTool",
    "BrowserExtractTool",
    "BrowserGetElementTool",
    "BrowserGetHtmlTool",
    "BrowserGetTextTool",
    "BrowserPageErrorsTool",
    "BrowserQueryElementsTool",
    "BrowserStatusTool",
    "BrowserWaitForTool",
]
