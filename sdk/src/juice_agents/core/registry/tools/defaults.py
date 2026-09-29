"""
registry.tools 默认内置工具注册表。
"""

from __future__ import annotations

from collections.abc import Callable

from juice_agents.core.agent.tools.builtin.user_interaction.ask_tools import AskTool
from juice_agents.core.agent.tools.builtin.web.browser import (
    BrowserClearCookiesTool,
    BrowserClickTool,
    BrowserCloseTabTool,
    BrowserClosePopupsTool,
    BrowserConsoleLogsTool,
    BrowserEvaluateTool,
    BrowserExtractTool,
    BrowserFillTool,
    BrowserFindTextTool,
    BrowserGetCookiesTool,
    BrowserGetElementTool,
    BrowserGetHtmlTool,
    BrowserGetStorageTool,
    BrowserGetTextTool,
    BrowserGoBackTool,
    BrowserHoverTool,
    BrowserListTabsTool,
    BrowserLoadStorageStateTool,
    BrowserNewTabTool,
    BrowserOpenUrlTool,
    BrowserPageErrorsTool,
    BrowserPressKeyTool,
    BrowserQueryElementsTool,
    BrowserSaveStorageStateTool,
    BrowserScreenshotTool,
    BrowserScrollTool,
    BrowserSearchTool,
    BrowserSelectOptionTool,
    BrowserSetCheckboxTool,
    BrowserSetCookiesTool,
    BrowserSetStorageTool,
    BrowserStartRecordingTool,
    BrowserStatusTool,
    BrowserStopRecordingTool,
    BrowserSubmitFormTool,
    BrowserSwitchTabTool,
    BrowserTypeTool,
    BrowserWaitForTool,
)
from juice_agents.core.agent.tools.builtin.scheduling.schedules_tools import CronCreateTool, CronDeleteTool, CronListTool
from juice_agents.core.agent.tools.builtin.code_execution.code_tools import PythonTool, ShellTool
from juice_agents.core.agent.tools.builtin.filesystem.files_tools import EditTool, GlobTool, GrepTool, ReadTool, WriteTool
from juice_agents.core.agent.tools.builtin.graphs.graphs_tools import GraphListTool, GraphTool, GraphViewTool
from juice_agents.core.agent.tools.builtin.evolution.graph_manage import GraphManageTool
from juice_agents.core.agent.tools.builtin.images.attachments_tools import AddImageTool
from juice_agents.core.agent.tools.builtin.output.completion_tools import SubmitOutputTool
from juice_agents.core.agent.tools.builtin.planning.planning_tools import ExitPlanTool, PlanTool, TodosTool
from juice_agents.core.agent.tools.builtin.tasks.async_tasks_tools import AsyncTaskStopTool
from juice_agents.core.agent.tools.builtin.agents.agents_tools import AgentViewTool, AgentsListTool
from juice_agents.core.agent.tools.builtin.evolution.agent_manage import AgentManageTool
from juice_agents.core.agent.tools.builtin.plugins.plugins_tools import PluginViewTool, PluginsListTool
from juice_agents.core.agent.tools.builtin.skills.skills_tools import SkillViewTool, SkillsListTool
from juice_agents.core.agent.tools.builtin.evolution.skill_manage import SkillManageTool
from juice_agents.core.agent.tools.builtin.tools.tools_tools import ToolViewTool, ToolsListTool
from juice_agents.core.agent.tools.builtin.evolution.tool_manage import ToolManageTool
from juice_agents.core.agent.tools.runtime.base_tools import Tool
from juice_agents.core.agent.tools.builtin.images.images_tools import GenerateEditImageTool
from juice_agents.core.agent.tools.builtin.web.web_search_tools import ApiWebSearchTool, WebSearchTool
from juice_agents.core.agent.tools.builtin.worktrees.worktrees_tools import (
    EnterWorktreeTool,
    ExitWorktreeTool,
    ListWorktreesTool,
    WorktreeStatusTool,
)
from juice_agents.core.agent.tools.builtin.team.team_tools import (
    SendMessageTool,
    TeamMemberStopRequestTool,
    TeamFinishTool,
    TeamMemberCloseTool,
    TeamMemberCreateTool,
    TeamMemberRestartTool,
    TeamTaskClaimTool,
    TeamTaskCompleteTool,
    TeamTaskCreateTool,
    TeamTaskListTool,
    TeamTaskUpdateTool,
    TeamCreateTool,
    TeamUseTool,
)

_AGENT_CONFIG_DIR_TOOLS = frozenset()
_TOOL_CONFIG_DIR_TOOLS = frozenset({"tools_list", "tool_view", "tool_manage"})
def get_default_tool_factories() -> dict[str, Callable[..., Tool]]:
    """
    返回默认工具 factory 映射。
    """
    from juice_agents.core.agent.tools.builtin.agents.subagents_tools import AgentTool
    return {
        "ask": AskTool,
        "team_task_create": TeamTaskCreateTool,
        "team_task_list": TeamTaskListTool,
        "team_task_claim": TeamTaskClaimTool,
        "team_task_complete": TeamTaskCompleteTool,
        "team_task_update": TeamTaskUpdateTool,
        "team_create": TeamCreateTool,
        "team_use": TeamUseTool,
        "send_message": SendMessageTool,
        "team_member_stop_request": TeamMemberStopRequestTool,
        "team_member_create": TeamMemberCreateTool,
        "team_member_restart": TeamMemberRestartTool,
        "team_member_close": TeamMemberCloseTool,
        "team_finish": TeamFinishTool,
        "shell": ShellTool,
        "python": PythonTool,
        "agent_tool": AgentTool,
        "enter_worktree": EnterWorktreeTool,
        "exit_worktree": ExitWorktreeTool,
        "list_worktrees": ListWorktreesTool,
        "worktree_status": WorktreeStatusTool,
        "graph_tool": GraphTool,
        "graph_list": GraphListTool,
        "graph_view": GraphViewTool,
        "graph_manage": GraphManageTool,
        "read": ReadTool,
        "write": WriteTool,
        "edit": EditTool,
        "glob": GlobTool,
        "grep": GrepTool,
        "submit_output": SubmitOutputTool,
        "agents_list": AgentsListTool,
        "agent_view": AgentViewTool,
        "agent_manage": AgentManageTool,
        "tools_list": ToolsListTool,
        "tool_view": ToolViewTool,
        "tool_manage": ToolManageTool,
        "skills_list": SkillsListTool,
        "skill_view": SkillViewTool,
        "skill_manage": SkillManageTool,
        "plugins_list": PluginsListTool,
        "plugin_view": PluginViewTool,
        "add_image": AddImageTool,
        "todos": TodosTool,
        "plan": PlanTool,
        "exit_plan": ExitPlanTool,
        "async_task_stop": AsyncTaskStopTool,
        "web_search": WebSearchTool,
        "api_web_search": ApiWebSearchTool,
        "browser_search": BrowserSearchTool,
        "browser_open_url": BrowserOpenUrlTool,
        "browser_go_back": BrowserGoBackTool,
        "browser_close_popups": BrowserClosePopupsTool,
        "browser_find_text": BrowserFindTextTool,
        "browser_screenshot": BrowserScreenshotTool,
        "browser_status": BrowserStatusTool,
        "browser_wait_for": BrowserWaitForTool,
        "browser_get_text": BrowserGetTextTool,
        "browser_get_html": BrowserGetHtmlTool,
        "browser_get_element": BrowserGetElementTool,
        "browser_query_elements": BrowserQueryElementsTool,
        "browser_extract": BrowserExtractTool,
        "browser_console_logs": BrowserConsoleLogsTool,
        "browser_page_errors": BrowserPageErrorsTool,
        "browser_list_tabs": BrowserListTabsTool,
        "browser_new_tab": BrowserNewTabTool,
        "browser_switch_tab": BrowserSwitchTabTool,
        "browser_close_tab": BrowserCloseTabTool,
        "browser_click": BrowserClickTool,
        "browser_fill": BrowserFillTool,
        "browser_type": BrowserTypeTool,
        "browser_press_key": BrowserPressKeyTool,
        "browser_select_option": BrowserSelectOptionTool,
        "browser_set_checkbox": BrowserSetCheckboxTool,
        "browser_submit_form": BrowserSubmitFormTool,
        "browser_hover": BrowserHoverTool,
        "browser_scroll": BrowserScrollTool,
        "browser_evaluate": BrowserEvaluateTool,
        "browser_get_cookies": BrowserGetCookiesTool,
        "browser_set_cookies": BrowserSetCookiesTool,
        "browser_clear_cookies": BrowserClearCookiesTool,
        "browser_get_storage": BrowserGetStorageTool,
        "browser_set_storage": BrowserSetStorageTool,
        "browser_save_storage_state": BrowserSaveStorageStateTool,
        "browser_load_storage_state": BrowserLoadStorageStateTool,
        "browser_start_recording": BrowserStartRecordingTool,
        "browser_stop_recording": BrowserStopRecordingTool,
        "generate_edit_image": GenerateEditImageTool,
        "cron_create": CronCreateTool,
        "cron_list": CronListTool,
        "cron_delete": CronDeleteTool,
    }


def get_builtin_tool_context_requirements() -> dict[str, tuple[str, ...]]:
    """返回内置工具创建时必须由运行时显式提供的上下文参数。"""
    return {}


def get_builtin_tool_context_defaults() -> dict[str, tuple[str, ...]]:
    """返回 registry 可根据自身配置目录自动补齐的上下文参数。"""
    defaults: dict[str, tuple[str, ...]] = {}
    for name in _AGENT_CONFIG_DIR_TOOLS:
        defaults.setdefault(name, tuple())
        defaults[name] = tuple(sorted(set(defaults[name]) | {"agent_config_dir"}))
    for name in _TOOL_CONFIG_DIR_TOOLS:
        defaults.setdefault(name, tuple())
        defaults[name] = tuple(sorted(set(defaults[name]) | {"tool_config_dir"}))
    return defaults


__all__ = [
    "get_builtin_tool_context_defaults",
    "get_builtin_tool_context_requirements",
    "get_default_tool_factories",
]
