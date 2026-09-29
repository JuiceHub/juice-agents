"""Browser form and pointer interaction tools backed by Playwright."""

from __future__ import annotations

from typing import Any

from .policy_tools import BrowserTool


class BrowserClickTool(BrowserTool):
    name = "browser_click"
    description = "点击当前浏览器页面中的元素"
    inputs = {
        "selector": {"type": "string", "description": "Playwright locator，例如 text=提交、role=button[name='保存'] 或 CSS"},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
        "button": {"type": "string", "description": "left/right/middle，默认 left", "required": False},
        "click_count": {"type": "integer", "description": "点击次数，默认 1", "required": False},
        "modifiers": {"type": "list", "description": "可选修饰键列表，例如 ['Control']", "required": False},
    }
    outputs = {"status": {"type": "string", "description": "执行状态"}, "url": {"type": "string", "description": "当前 URL"}}

    def forward(self, selector: str, tab_id: str | None = None, button: str = "left", click_count: int = 1, modifiers: list[str] | None = None) -> dict[str, Any]:
        session = self._session()
        self._check_mutation(action="click", selector=selector, url=session.current_url(tab_id))
        return session.click(selector, tab_id=tab_id, button=button, click_count=click_count, modifiers=modifiers)


class BrowserFillTool(BrowserTool):
    name = "browser_fill"
    description = "填充输入框或文本域，默认先清空原值"
    inputs = {
        "selector": {"type": "string", "description": "Playwright locator"},
        "value": {"type": "string", "description": "要填入的值"},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
        "clear_first": {"type": "boolean", "description": "是否先清空，默认 true", "required": False},
    }
    outputs = BrowserClickTool.outputs

    def forward(self, selector: str, value: str, tab_id: str | None = None, clear_first: bool = True) -> dict[str, Any]:
        session = self._session()
        self._check_mutation(action="fill", selector=selector, value=value, url=session.current_url(tab_id))
        return session.fill(selector, value, tab_id=tab_id, clear_first=clear_first)


class BrowserTypeTool(BrowserTool):
    name = "browser_type"
    description = "向元素逐字输入文本，可选按 Enter"
    inputs = {
        "selector": {"type": "string", "description": "Playwright locator"},
        "value": {"type": "string", "description": "要输入的文本"},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
        "delay_ms": {"type": "integer", "description": "每字符延迟毫秒数，默认 0", "required": False},
        "press_enter": {"type": "boolean", "description": "输入后是否按 Enter，默认 false", "required": False},
    }
    outputs = BrowserClickTool.outputs

    def forward(self, selector: str, value: str, tab_id: str | None = None, delay_ms: int = 0, press_enter: bool = False) -> dict[str, Any]:
        session = self._session()
        self._check_mutation(action="type", selector=selector, value=value, url=session.current_url(tab_id))
        return session.type_text(selector, value, tab_id=tab_id, delay_ms=delay_ms, press_enter=press_enter)


class BrowserPressKeyTool(BrowserTool):
    name = "browser_press_key"
    description = "在页面或指定元素上按键，例如 Enter、Tab、Escape"
    inputs = {
        "key": {"type": "string", "description": "按键名称"},
        "selector": {"type": "string", "description": "可选元素 locator，不填则在页面上按键", "required": False},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = BrowserClickTool.outputs

    def forward(self, key: str, selector: str | None = None, tab_id: str | None = None) -> dict[str, Any]:
        session = self._session()
        self._check_mutation(action=f"press_key:{key}", selector=selector or "", url=session.current_url(tab_id))
        return session.press_key(key, selector=selector, tab_id=tab_id)


class BrowserSelectOptionTool(BrowserTool):
    name = "browser_select_option"
    description = "选择 select 下拉框选项"
    inputs = {
        "selector": {"type": "string", "description": "select 元素 locator"},
        "values": {"type": "any", "description": "单个值或值列表"},
        "by": {"type": "string", "description": "value/label/index，默认 value", "required": False},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = {"selected": {"type": "list", "description": "实际选中的值"}, "url": {"type": "string", "description": "当前 URL"}}

    def forward(self, selector: str, values: Any, by: str = "value", tab_id: str | None = None) -> dict[str, Any]:
        session = self._session()
        self._check_mutation(action="select_option", selector=selector, value=str(values), url=session.current_url(tab_id))
        return session.select_option(selector, values, by=by, tab_id=tab_id)


class BrowserSetCheckboxTool(BrowserTool):
    name = "browser_set_checkbox"
    description = "设置 checkbox/radio 的选中状态"
    inputs = {
        "selector": {"type": "string", "description": "checkbox/radio 元素 locator"},
        "checked": {"type": "boolean", "description": "是否选中"},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = {"checked": {"type": "boolean", "description": "最终状态"}, "url": {"type": "string", "description": "当前 URL"}}

    def forward(self, selector: str, checked: bool, tab_id: str | None = None) -> dict[str, Any]:
        session = self._session()
        self._check_mutation(action="set_checkbox", selector=selector, url=session.current_url(tab_id))
        return session.set_checkbox(selector, checked, tab_id=tab_id)


class BrowserSubmitFormTool(BrowserTool):
    name = "browser_submit_form"
    description = "提交指定元素所在表单或指定 form"
    inputs = {
        "selector": {"type": "string", "description": "form 或表单内元素 locator"},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = BrowserClickTool.outputs

    def forward(self, selector: str, tab_id: str | None = None) -> dict[str, Any]:
        session = self._session()
        self._check_mutation(action="submit_form", selector=selector, url=session.current_url(tab_id))
        return session.submit_form(selector, tab_id=tab_id)


class BrowserHoverTool(BrowserTool):
    name = "browser_hover"
    description = "悬停到指定元素"
    inputs = {
        "selector": {"type": "string", "description": "Playwright locator"},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = BrowserClickTool.outputs

    def forward(self, selector: str, tab_id: str | None = None) -> dict[str, Any]:
        session = self._session()
        self._check_mutation(action="hover", selector=selector, url=session.current_url(tab_id))
        return session.hover(selector, tab_id=tab_id)


class BrowserScrollTool(BrowserTool):
    name = "browser_scroll"
    description = "滚动页面或把指定元素滚入视图"
    inputs = {
        "selector": {"type": "string", "description": "可选元素 locator", "required": False},
        "x": {"type": "integer", "description": "横向滚动量，默认 0", "required": False},
        "y": {"type": "integer", "description": "纵向滚动量，默认 800", "required": False},
        "to": {"type": "string", "description": "top 或 bottom", "required": False},
        "tab_id": {"type": "string", "description": "可选 tab id，默认 active tab", "required": False},
    }
    outputs = BrowserClickTool.outputs

    def forward(self, selector: str | None = None, x: int = 0, y: int = 800, to: str | None = None, tab_id: str | None = None) -> dict[str, Any]:
        session = self._session()
        self._check_mutation(action="scroll", selector=selector or "", url=session.current_url(tab_id))
        return session.scroll(selector=selector, x=x, y=y, to=to, tab_id=tab_id)


BROWSER_INTERACTION_TOOLS = {
    "browser_click": BrowserClickTool(),
    "browser_fill": BrowserFillTool(),
    "browser_type": BrowserTypeTool(),
    "browser_press_key": BrowserPressKeyTool(),
    "browser_select_option": BrowserSelectOptionTool(),
    "browser_set_checkbox": BrowserSetCheckboxTool(),
    "browser_submit_form": BrowserSubmitFormTool(),
    "browser_hover": BrowserHoverTool(),
    "browser_scroll": BrowserScrollTool(),
}


__all__ = [
    "BROWSER_INTERACTION_TOOLS",
    "BrowserClickTool",
    "BrowserFillTool",
    "BrowserHoverTool",
    "BrowserPressKeyTool",
    "BrowserScrollTool",
    "BrowserSelectOptionTool",
    "BrowserSetCheckboxTool",
    "BrowserSubmitFormTool",
    "BrowserTypeTool",
]
