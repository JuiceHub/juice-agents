"""Opt-in browser state, JavaScript, and recording tools.

These tools are implemented but intentionally omitted from prebuilt agents by
default. Runtime policy requires `browser.risky_tools.enabled=true` and, by
default, per-call user confirmation.
"""

from __future__ import annotations

from typing import Any

from .policy_tools import BrowserTool


class _RiskyBrowserTool(BrowserTool):
    is_risky_tool = True
    is_read_only = False

    def _allow(self, action: str, tab_id: str | None = None):
        session = self._session()
        url = ""
        try:
            url = session.current_url(tab_id)
        except Exception:
            url = ""
        return session, self._check_risky(action=action, url=url)


class BrowserEvaluateTool(_RiskyBrowserTool):
    name = "browser_evaluate"
    description = "执行 JavaScript expression 并返回 JSON-friendly 结果；高风险，默认关闭"
    inputs = {
        "expression": {"type": "string", "description": "要在页面执行的 JavaScript expression/function"},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
        "timeout_ms": {"type": "integer", "description": "超时毫秒数，默认取 policy js_timeout_ms", "required": False},
        "max_chars": {"type": "integer", "description": "最大返回字符数，默认取 policy max_eval_result_chars", "required": False},
    }
    outputs = {"result": {"type": "any", "description": "执行结果"}, "truncated": {"type": "boolean", "description": "是否截断"}}

    def forward(self, expression: str, tab_id: str | None = None, timeout_ms: int | None = None, max_chars: int | None = None) -> dict[str, Any]:
        session, policy = self._allow("browser_evaluate", tab_id)
        return session.evaluate(expression, tab_id=tab_id, timeout_ms=timeout_ms or policy.js_timeout_ms, max_chars=max_chars or policy.max_eval_result_chars)


class BrowserGetCookiesTool(_RiskyBrowserTool):
    name = "browser_get_cookies"
    description = "读取浏览器 context cookies；高风险，默认关闭"
    inputs = {"urls": {"type": "list", "description": "可选 URL 过滤列表", "required": False}}
    outputs = {"cookies": {"type": "list", "description": "cookie 列表"}}

    def forward(self, urls: list[str] | None = None) -> dict[str, Any]:
        session, _ = self._allow("browser_get_cookies")
        return session.get_cookies(urls=urls)


class BrowserSetCookiesTool(_RiskyBrowserTool):
    name = "browser_set_cookies"
    description = "写入浏览器 context cookies；高风险，默认关闭"
    inputs = {"cookies": {"type": "list", "description": "Playwright cookie object 列表"}}
    outputs = {"status": {"type": "string", "description": "执行状态"}, "count": {"type": "integer", "description": "写入数量"}}

    def forward(self, cookies: list[dict[str, Any]]) -> dict[str, Any]:
        session, _ = self._allow("browser_set_cookies")
        return session.set_cookies(cookies)


class BrowserClearCookiesTool(_RiskyBrowserTool):
    name = "browser_clear_cookies"
    description = "清空浏览器 context cookies；高风险，默认关闭"
    inputs = {}
    outputs = {"status": {"type": "string", "description": "执行状态"}}

    def forward(self) -> dict[str, Any]:
        session, _ = self._allow("browser_clear_cookies")
        return session.clear_cookies()


class BrowserGetStorageTool(_RiskyBrowserTool):
    name = "browser_get_storage"
    description = "读取 localStorage/sessionStorage；高风险，默认关闭"
    inputs = {
        "keys": {"type": "list", "description": "可选 key 列表", "required": False},
        "storage_type": {"type": "string", "description": "localStorage 或 sessionStorage", "required": False},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = {"items": {"type": "object", "description": "storage 内容"}}

    def forward(self, keys: list[str] | None = None, storage_type: str = "localStorage", tab_id: str | None = None) -> dict[str, Any]:
        session, _ = self._allow("browser_get_storage", tab_id)
        return session.get_storage(keys=keys, storage_type=storage_type, tab_id=tab_id)


class BrowserSetStorageTool(_RiskyBrowserTool):
    name = "browser_set_storage"
    description = "写入 localStorage/sessionStorage；高风险，默认关闭"
    inputs = {
        "items": {"type": "object", "description": "要写入的 key/value"},
        "storage_type": {"type": "string", "description": "localStorage 或 sessionStorage", "required": False},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = {"status": {"type": "string", "description": "执行状态"}, "count": {"type": "integer", "description": "写入数量"}}

    def forward(self, items: dict[str, Any], storage_type: str = "localStorage", tab_id: str | None = None) -> dict[str, Any]:
        session, _ = self._allow("browser_set_storage", tab_id)
        return session.set_storage(items, storage_type=storage_type, tab_id=tab_id)


class BrowserSaveStorageStateTool(_RiskyBrowserTool):
    name = "browser_save_storage_state"
    description = "保存 Playwright storage_state 到 runner 隔离目录；高风险，默认关闭"
    inputs = {"path": {"type": "string", "description": "文件名或 runner browser_state 下路径，默认 storage_state.json", "required": False}}
    outputs = {"path": {"type": "string", "description": "保存路径"}, "status": {"type": "string", "description": "执行状态"}}

    def forward(self, path: str = "storage_state.json") -> dict[str, Any]:
        session, _ = self._allow("browser_save_storage_state")
        return session.save_storage_state(path)


class BrowserLoadStorageStateTool(_RiskyBrowserTool):
    name = "browser_load_storage_state"
    description = "加载 Playwright storage_state 并重建 context；高风险，默认关闭"
    inputs = {"path": {"type": "string", "description": "runner browser_state 下的 storage_state 文件"}}
    outputs = {"active_tab_id": {"type": "string", "description": "重建后的 active tab"}, "status": {"type": "string", "description": "执行状态"}}

    def forward(self, path: str) -> dict[str, Any]:
        session, _ = self._allow("browser_load_storage_state")
        return session.load_storage_state(path)


class BrowserStartRecordingTool(_RiskyBrowserTool):
    name = "browser_start_recording"
    description = "启动 Playwright 视频录制；高风险，默认关闭"
    inputs = {"directory": {"type": "string", "description": "可选输出目录", "required": False}}
    outputs = {"recording_id": {"type": "string", "description": "录制 ID"}, "path": {"type": "string", "description": "输出目录"}}

    def forward(self, directory: str | None = None) -> dict[str, Any]:
        session, _ = self._allow("browser_start_recording")
        return session.start_recording(directory=directory)


class BrowserStopRecordingTool(_RiskyBrowserTool):
    name = "browser_stop_recording"
    description = "停止录屏，默认返回 webm；format=gif 时 ffmpeg 可用才转换"
    inputs = {
        "format": {"type": "string", "description": "webm 或 gif，默认 webm", "required": False},
        "gif_path": {"type": "string", "description": "可选 GIF 输出路径", "required": False},
    }
    outputs = {"path": {"type": "string", "description": "输出文件"}, "format": {"type": "string", "description": "输出格式"}}

    def forward(self, format: str = "webm", gif_path: str | None = None) -> dict[str, Any]:
        session, _ = self._allow("browser_stop_recording")
        return session.stop_recording(format=format, gif_path=gif_path)


BROWSER_STATE_TOOLS = {
    "browser_evaluate": BrowserEvaluateTool(),
    "browser_get_cookies": BrowserGetCookiesTool(),
    "browser_set_cookies": BrowserSetCookiesTool(),
    "browser_clear_cookies": BrowserClearCookiesTool(),
    "browser_get_storage": BrowserGetStorageTool(),
    "browser_set_storage": BrowserSetStorageTool(),
    "browser_save_storage_state": BrowserSaveStorageStateTool(),
    "browser_load_storage_state": BrowserLoadStorageStateTool(),
    "browser_start_recording": BrowserStartRecordingTool(),
    "browser_stop_recording": BrowserStopRecordingTool(),
}


__all__ = [
    "BROWSER_STATE_TOOLS",
    "BrowserClearCookiesTool",
    "BrowserEvaluateTool",
    "BrowserGetCookiesTool",
    "BrowserGetStorageTool",
    "BrowserLoadStorageStateTool",
    "BrowserSaveStorageStateTool",
    "BrowserSetCookiesTool",
    "BrowserSetStorageTool",
    "BrowserStartRecordingTool",
    "BrowserStopRecordingTool",
]
