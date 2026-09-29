"""Stable public facade for Juice Agent tools.

Concrete implementations live in ``builtin`` and shared runtime mechanics live
in ``runtime``. This facade keeps public imports compact and lazily
imports ``AgentTool`` to avoid the Registry import cycle.
"""

from importlib import import_module

from .builtin.scheduling.schedules_tools import CRON_TOOLS, CronCreateTool, CronDeleteTool, CronListTool
from .builtin.code_execution.code_tools import PythonTool, ShellTool
from .runtime import (
    ResolvedToolAction,
    Tool,
    ToolExecutionCancelled,
    ToolExecutionContext,
    ToolExecutionMode,
    ToolExecutionPolicy,
    ToolExecutionRecord,
    ToolExecutionResult,
)
from .builtin.web.browser import BROWSER_NAVIGATION_TOOLS, BROWSER_TOOLS
from .builtin.web.browser import (
    BrowserClearCookiesTool, BrowserClickTool, BrowserClosePopupsTool, BrowserCloseTabTool,
    BrowserConsoleLogsTool, BrowserEvaluateTool, BrowserExtractTool, BrowserFillTool,
    BrowserFindTextTool, BrowserGetCookiesTool, BrowserGetElementTool, BrowserGetHtmlTool,
    BrowserGetStorageTool, BrowserGetTextTool, BrowserGoBackTool, BrowserHoverTool,
    BrowserListTabsTool, BrowserLoadStorageStateTool, BrowserNewTabTool, BrowserOpenUrlTool,
    BrowserPageErrorsTool, BrowserPressKeyTool, BrowserQueryElementsTool,
    BrowserSaveStorageStateTool, BrowserScreenshotTool, BrowserScrollTool, BrowserSearchTool,
    BrowserSelectOptionTool, BrowserSetCheckboxTool, BrowserSetCookiesTool,
    BrowserSetStorageTool, BrowserStartRecordingTool, BrowserStatusTool,
    BrowserStopRecordingTool, BrowserSubmitFormTool, BrowserSwitchTabTool, BrowserTypeTool,
    BrowserWaitForTool,
)
from .builtin.images.images_tools import GenerateEditImageTool
from .builtin.mcp.mcp_tools import MCPServerConfig, MCPTools
from .builtin.web.web_search_tools import ApiWebSearchTool, WEB_TOOLS, WebSearchTool
from .builtin.agents.agents_tools import AgentViewTool, AgentsListTool
from .builtin.plugins.plugins_tools import PluginViewTool, PluginsListTool
from .builtin.skills.skills_tools import SkillViewTool, SkillsListTool
from .builtin.tools.tools_tools import ToolViewTool, ToolsListTool
from .builtin.evolution.agent_manage import AgentManageTool
from .builtin.evolution.graph_manage import GraphManageTool
from .builtin.evolution.skill_manage import SkillManageTool
from .builtin.evolution.tool_manage import ToolManageTool
from .builtin.tasks.async_tasks_tools import AsyncTaskStopTool
from .builtin.graphs.graphs_tools import GraphListTool, GraphTool, GraphViewTool
from .builtin.filesystem.files_tools import EditTool, FILE_TOOLS, GlobTool, GrepTool, ReadTool, WriteTool

DEFAULT_TOOLS = {
    "shell": ShellTool(),
    "python": PythonTool(),
    "async_task_stop": AsyncTaskStopTool(),
}

# 每个域的 list/view 随域目录发布，manage 集中在 builtin/evolution/，所以 bundle
# 只能在这一层聚合 —— 若定义在域目录就会产生 域 → evolution 的反向依赖。
AGENT_TOOLS = [AgentsListTool, AgentViewTool, AgentManageTool]
TOOL_TOOLS = [ToolsListTool, ToolViewTool, ToolManageTool]
SKILL_TOOLS = [SkillsListTool, SkillViewTool, SkillManageTool]
PLUGIN_TOOLS = [PluginsListTool, PluginViewTool]
GRAPH_TOOLS = [GraphListTool, GraphViewTool, GraphManageTool, GraphTool]

_LAZY_EXPORTS = {"AgentTool"}


def __getattr__(name: str):
    """Defer subagent orchestration import until a caller actually needs it."""

    if name in _LAZY_EXPORTS:
        value = getattr(import_module(".builtin.agents.subagents_tools", __package__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Tool", "ToolExecutionMode", "ToolExecutionPolicy",
    "ToolExecutionContext", "ToolExecutionCancelled", "ResolvedToolAction",
    "ToolExecutionResult", "ToolExecutionRecord", "DEFAULT_TOOLS", "ShellTool",
    "PythonTool", "AsyncTaskStopTool",
    "CronCreateTool", "CronListTool", "CronDeleteTool", "CRON_TOOLS", "AgentTool",
    "GraphTool", "GraphListTool", "GraphViewTool", "GraphManageTool", "GRAPH_TOOLS",
    "ReadTool", "WriteTool", "EditTool", "GlobTool", "GrepTool", "FILE_TOOLS",
    "MCPTools", "MCPServerConfig", "SkillsListTool", "SkillViewTool", "SkillManageTool",
    "PluginsListTool", "PluginViewTool", "PLUGIN_TOOLS", "AgentsListTool", "AgentViewTool",
    "AgentManageTool", "ToolsListTool", "ToolViewTool", "ToolManageTool", "AGENT_TOOLS",
    "SKILL_TOOLS", "TOOL_TOOLS", "GenerateEditImageTool", "WebSearchTool", "ApiWebSearchTool",
    "BrowserSearchTool", "BrowserOpenUrlTool", "BrowserGoBackTool", "BrowserClosePopupsTool",
    "BrowserFindTextTool", "BrowserScreenshotTool", "BrowserStatusTool", "BrowserWaitForTool",
    "BrowserGetTextTool", "BrowserGetHtmlTool", "BrowserGetElementTool", "BrowserQueryElementsTool",
    "BrowserExtractTool", "BrowserConsoleLogsTool", "BrowserPageErrorsTool", "BrowserListTabsTool",
    "BrowserNewTabTool", "BrowserSwitchTabTool", "BrowserCloseTabTool", "BrowserClickTool",
    "BrowserFillTool", "BrowserTypeTool", "BrowserPressKeyTool", "BrowserSelectOptionTool",
    "BrowserSetCheckboxTool", "BrowserSubmitFormTool", "BrowserHoverTool", "BrowserScrollTool",
    "BrowserEvaluateTool", "BrowserGetCookiesTool", "BrowserSetCookiesTool", "BrowserClearCookiesTool",
    "BrowserGetStorageTool", "BrowserSetStorageTool", "BrowserSaveStorageStateTool",
    "BrowserLoadStorageStateTool", "BrowserStartRecordingTool", "BrowserStopRecordingTool",
    "BROWSER_NAVIGATION_TOOLS", "BROWSER_TOOLS", "WEB_TOOLS",
]
