"""Browser tab management tools."""

from __future__ import annotations

from typing import Any

from .policy_tools import BrowserTool
from ....runtime.base_tools import LIST_OBSERVATION_CHARS


class BrowserListTabsTool(BrowserTool):
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "browser_list_tabs"
    is_read_only = True
    description = "列出当前浏览器会话中的 tabs"
    inputs = {}
    outputs = {"tabs": {"type": "list", "description": "tab 列表"}, "active_tab_id": {"type": "string", "description": "当前 active tab"}}

    def forward(self) -> dict[str, Any]:
        return self._session().list_tabs()


class BrowserNewTabTool(BrowserTool):
    name = "browser_new_tab"
    description = "新建浏览器 tab，可选打开 URL"
    inputs = {
        "url": {"type": "string", "description": "可选 URL", "required": False},
        "make_active": {"type": "boolean", "description": "是否设为 active，默认 true", "required": False},
    }
    outputs = {"tab_id": {"type": "string", "description": "新 tab id"}, "url": {"type": "string", "description": "当前 URL"}}

    def forward(self, url: str | None = None, make_active: bool = True) -> dict[str, Any]:
        if url:
            self._check_host(url)
        return self._session().new_tab(url, make_active=make_active)


class BrowserSwitchTabTool(BrowserTool):
    name = "browser_switch_tab"
    is_read_only = True
    description = "切换 active tab"
    inputs = {"tab_id": {"type": "string", "description": "目标 tab id"}}
    outputs = {"tab_id": {"type": "string", "description": "当前 tab id"}, "url": {"type": "string", "description": "当前 URL"}}

    def forward(self, tab_id: str) -> dict[str, Any]:
        return self._session().switch_tab(tab_id)


class BrowserCloseTabTool(BrowserTool):
    name = "browser_close_tab"
    description = "关闭指定 tab，不能关闭最后一个 tab"
    inputs = {"tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False}}
    outputs = {"closed_tab_id": {"type": "string", "description": "已关闭 tab"}, "active_tab_id": {"type": "string", "description": "新的 active tab"}}

    def forward(self, tab_id: str | None = None) -> dict[str, Any]:
        session = self._session()
        self._check_mutation(action="close_tab", url=session.current_url(tab_id))
        return session.close_tab(tab_id)


BROWSER_TAB_TOOLS = {
    "browser_list_tabs": BrowserListTabsTool(),
    "browser_new_tab": BrowserNewTabTool(),
    "browser_switch_tab": BrowserSwitchTabTool(),
    "browser_close_tab": BrowserCloseTabTool(),
}


__all__ = [
    "BROWSER_TAB_TOOLS",
    "BrowserCloseTabTool",
    "BrowserListTabsTool",
    "BrowserNewTabTool",
    "BrowserSwitchTabTool",
]
