"""Aggregated exports for Playwright browser tools.

This package owns the Playwright session, the policy-aware base class, and the
fine-grained tool groups (navigation / inspection / interaction / state / tabs).
"""

from __future__ import annotations

from .inspection_tools import (
    BROWSER_INSPECTION_TOOLS,
    BrowserConsoleLogsTool,
    BrowserExtractTool,
    BrowserGetElementTool,
    BrowserGetHtmlTool,
    BrowserGetTextTool,
    BrowserPageErrorsTool,
    BrowserQueryElementsTool,
    BrowserStatusTool,
    BrowserWaitForTool,
)
from .interaction_tools import (
    BROWSER_INTERACTION_TOOLS,
    BrowserClickTool,
    BrowserFillTool,
    BrowserHoverTool,
    BrowserPressKeyTool,
    BrowserScrollTool,
    BrowserSelectOptionTool,
    BrowserSetCheckboxTool,
    BrowserSubmitFormTool,
    BrowserTypeTool,
)
from .navigation_tools import (
    BROWSER_NAVIGATION_TOOLS,
    BrowserClosePopupsTool,
    BrowserFindTextTool,
    BrowserGoBackTool,
    BrowserOpenUrlTool,
    BrowserScreenshotTool,
    BrowserSearchTool,
)
from .policy_tools import (
    BrowserPolicy,
    BrowserTool,
    BrowserToolMixin,
    is_local_url,
    load_browser_policy,
    looks_sensitive,
)
from .session import (
    DEFAULT_BROWSER_RECORDINGS_DIR,
    DEFAULT_BROWSER_SCREENSHOTS_DIR,
    DEFAULT_BROWSER_STATE_DIR,
    DEFAULT_USER_AGENT,
    PlaywrightBrowserSession,
    get_browser_session,
)
from .state_tools import (
    BROWSER_STATE_TOOLS,
    BrowserClearCookiesTool,
    BrowserEvaluateTool,
    BrowserGetCookiesTool,
    BrowserGetStorageTool,
    BrowserLoadStorageStateTool,
    BrowserSaveStorageStateTool,
    BrowserSetCookiesTool,
    BrowserSetStorageTool,
    BrowserStartRecordingTool,
    BrowserStopRecordingTool,
)
from .tabs_tools import (
    BROWSER_TAB_TOOLS,
    BrowserCloseTabTool,
    BrowserListTabsTool,
    BrowserNewTabTool,
    BrowserSwitchTabTool,
)

BROWSER_TOOLS = {
    **BROWSER_NAVIGATION_TOOLS,
    **BROWSER_INSPECTION_TOOLS,
    **BROWSER_INTERACTION_TOOLS,
    **BROWSER_STATE_TOOLS,
    **BROWSER_TAB_TOOLS,
}


__all__ = [
    "BROWSER_INSPECTION_TOOLS",
    "BROWSER_INTERACTION_TOOLS",
    "BROWSER_NAVIGATION_TOOLS",
    "BROWSER_STATE_TOOLS",
    "BROWSER_TAB_TOOLS",
    "BROWSER_TOOLS",
    "BrowserClearCookiesTool",
    "BrowserClickTool",
    "BrowserCloseTabTool",
    "BrowserClosePopupsTool",
    "BrowserConsoleLogsTool",
    "BrowserEvaluateTool",
    "BrowserExtractTool",
    "BrowserFillTool",
    "BrowserFindTextTool",
    "BrowserGetCookiesTool",
    "BrowserGetElementTool",
    "BrowserGetHtmlTool",
    "BrowserGetStorageTool",
    "BrowserGetTextTool",
    "BrowserGoBackTool",
    "BrowserHoverTool",
    "BrowserListTabsTool",
    "BrowserLoadStorageStateTool",
    "BrowserNewTabTool",
    "BrowserOpenUrlTool",
    "BrowserPageErrorsTool",
    "BrowserPolicy",
    "BrowserPressKeyTool",
    "BrowserQueryElementsTool",
    "BrowserSaveStorageStateTool",
    "BrowserScreenshotTool",
    "BrowserScrollTool",
    "BrowserSearchTool",
    "BrowserSelectOptionTool",
    "BrowserSetCheckboxTool",
    "BrowserSetCookiesTool",
    "BrowserSetStorageTool",
    "BrowserStartRecordingTool",
    "BrowserStatusTool",
    "BrowserStopRecordingTool",
    "BrowserSubmitFormTool",
    "BrowserSwitchTabTool",
    "BrowserTool",
    "BrowserToolMixin",
    "BrowserTypeTool",
    "BrowserWaitForTool",
    "DEFAULT_BROWSER_RECORDINGS_DIR",
    "DEFAULT_BROWSER_SCREENSHOTS_DIR",
    "DEFAULT_BROWSER_STATE_DIR",
    "DEFAULT_USER_AGENT",
    "PlaywrightBrowserSession",
    "get_browser_session",
    "is_local_url",
    "load_browser_policy",
    "looks_sensitive",
]
