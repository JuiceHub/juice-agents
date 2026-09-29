"""Agent 领域内置角色的强类型配置声明。

源码内置配置只使用冻结的 :class:`AgentConfig`。只有 Registry/YAML 边界
才序列化为字典，避免调用方共享并原地修改全局 ``dict``/``list``。
"""

from __future__ import annotations

from typing import Any

from juice_agents.core.registry.agents.types import (
    AgentConfig,
    DEFAULT_MODEL_CONFIG_NAME,
    DEFAULT_MODEL_EFFORT,
)
from juice_agents.core.registry.tools.types import ToolRef


def _tools(*names: str) -> tuple[ToolRef, ...]:
    """把稳定工具名声明转换为强类型引用。"""

    return tuple(ToolRef(name=name) for name in names)


GENERAL_BROWSER_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "browser_search",
        "browser_open_url",
        "browser_go_back",
        "browser_close_popups",
        "browser_find_text",
        "browser_screenshot",
        "browser_status",
        "browser_wait_for",
        "browser_get_text",
        "browser_get_html",
        "browser_get_element",
        "browser_query_elements",
        "browser_extract",
        "browser_console_logs",
        "browser_page_errors",
        "browser_list_tabs",
        "browser_new_tab",
        "browser_switch_tab",
        "browser_close_tab",
        "browser_click",
        "browser_fill",
        "browser_type",
        "browser_press_key",
        "browser_select_option",
        "browser_set_checkbox",
        "browser_submit_form",
        "browser_hover",
        "browser_scroll",
    }
)

_BROWSER_TOOLS = _tools(
    "browser_close_popups",
    "browser_find_text",
    "browser_go_back",
    "browser_open_url",
    "browser_screenshot",
    "browser_search",
    "browser_status",
    "browser_wait_for",
    "browser_get_text",
    "browser_get_html",
    "browser_get_element",
    "browser_query_elements",
    "browser_extract",
    "browser_console_logs",
    "browser_page_errors",
    "browser_list_tabs",
    "browser_new_tab",
    "browser_switch_tab",
    "browser_close_tab",
    "browser_click",
    "browser_fill",
    "browser_type",
    "browser_press_key",
    "browser_select_option",
    "browser_set_checkbox",
    "browser_submit_form",
    "browser_hover",
    "browser_scroll",
)
_GRAPH_ROOT_TOOLS = _tools("graph_list", "graph_view", "graph_manage", "graph_tool")
_GRAPH_SUBAGENT_TOOLS = _tools("graph_list", "graph_view", "graph_tool")
_CONFIG_SHARED_TOOLS = _tools("tools_list", "tool_view", "tool_manage")
_CONFIG_AGENT_TOOLS = _tools("agents_list", "agent_view", "agent_manage")

GENERAL_ROOT_TOOLS = (
    *_tools(
        "shell",
        "python",
        "read",
        "write",
        "edit",
        "add_image",
        "todos",
        "web_search",
        "api_web_search",
        "agent_tool",
        "enter_worktree",
        "exit_worktree",
        "list_worktrees",
        "worktree_status",
    ),
    *_CONFIG_AGENT_TOOLS,
    *_CONFIG_SHARED_TOOLS,
    *_GRAPH_ROOT_TOOLS,
    *_BROWSER_TOOLS,
    *_tools("cron_create", "cron_list", "cron_delete"),
)
GENERAL_SUBAGENT_TOOLS = (
    *_tools(
        "shell",
        "python",
        "read",
        "write",
        "edit",
        "add_image",
        "todos",
        "web_search",
        "api_web_search",
    ),
    *_CONFIG_AGENT_TOOLS,
    *_CONFIG_SHARED_TOOLS,
    *_GRAPH_SUBAGENT_TOOLS,
    *_BROWSER_TOOLS,
)
GENERAL_TEAM_TOOLS = (
    *_tools("shell"),
    *_CONFIG_AGENT_TOOLS,
    *_CONFIG_SHARED_TOOLS,
    *_tools(
        "python",
        "read",
        "write",
        "edit",
        "todos",
        "api_web_search",
        "agent_tool",
        "enter_worktree",
        "exit_worktree",
        "list_worktrees",
        "worktree_status",
    ),
    *_GRAPH_ROOT_TOOLS,
)

ROOT_GENERAL_INSTRUCTIONS = (
    "You are the user-facing root agent. Complete simple tasks directly, and use "
    "`agent_tool` for specialized subagents: send codebase search, read-only analysis, "
    "and file discovery to `explore`; send complex isolated execution, multi-step work, "
    "and tasks that may require file edits to `general`. Use graph tools for repeatable, "
    "debuggable workflows: inspect scripts with `graph_list`/`graph_view`, edit local "
    "workflow scripts with `graph_manage`, and run them with `graph_tool`. When a user "
    "message starts with `/deep-research`, parse its question/source mode and call "
    "`graph_tool` with name `deep_research` and payload fields `question`, `source_mode`, "
    "and optional `scope`; never use a `source` payload field. Present the graph result "
    "to the user. Handle user-facing clarification yourself; do not let subagents "
    "interrupt the user directly."
)
GENERAL_SUBAGENT_INSTRUCTIONS = (
    "You are the general subagent. Isolate and complete complex, multi-step tasks that "
    "may require file edits. The user prompt contains only dynamic task details; fixed "
    "workflow, constraints, and output requirements come from the system prompt. Finish "
    "with `submit_output` containing a concise result the root agent can use directly. "
    "You may inspect and run graph workflow scripts, but do not modify graph scripts."
)
EXPLORE_SUBAGENT_INSTRUCTIONS = (
    "You are the explore subagent. Only perform read-only codebase exploration, file "
    "discovery, structure mapping, and evidence gathering. Do not edit files, run "
    "shell/python, or start other subagents; base conclusions only on read/glob/grep "
    "observations. Finish with `submit_output` containing paths, key findings, and any "
    "questions that still need verification."
)
GENERAL_CONFIG = AgentConfig(
    name="general",
    agent_type="default",
    model_config_name=DEFAULT_MODEL_CONFIG_NAME,
    model_effort=DEFAULT_MODEL_EFFORT,
    description="General-purpose prebuilt root agent with the default tool list.",
    instructions=ROOT_GENERAL_INSTRUCTIONS,
    prompt_language="en",
    max_steps=20,
    tools=GENERAL_ROOT_TOOLS,
    managed_agent_names=("general", "explore"),
)
GENERAL_SUBAGENT_CONFIG = AgentConfig(
    name="general",
    agent_type="default",
    model_config_name=DEFAULT_MODEL_CONFIG_NAME,
    model_effort=DEFAULT_MODEL_EFFORT,
    description=(
        "General subagent for isolated execution, multi-step work, and necessary "
        "file edits."
    ),
    instructions=GENERAL_SUBAGENT_INSTRUCTIONS,
    prompt_language="en",
    max_steps=20,
    tools=GENERAL_SUBAGENT_TOOLS,
    allowed_modes=("agent", "plan"),
)
EXPLORE_CONFIG = AgentConfig(
    name="explore",
    agent_type="default",
    model_config_name=DEFAULT_MODEL_CONFIG_NAME,
    model_effort=DEFAULT_MODEL_EFFORT,
    description=(
        "Read-only exploration subagent for codebase search, file discovery, "
        "structure mapping, and evidence gathering."
    ),
    instructions=EXPLORE_SUBAGENT_INSTRUCTIONS,
    prompt_language="en",
    max_steps=12,
    tools=_tools("read", "glob", "grep"),
    enable_skill_tools=False,
    allowed_modes=("agent", "plan"),
)
def build_builtin_agent_config(template_name: str, **overrides: Any) -> AgentConfig:
    """返回一个经过强类型校验的内置声明快照。"""

    normalized_name = str(template_name or "").strip()
    if normalized_name == "general":
        template = GENERAL_CONFIG
    elif normalized_name == "general_subagent":
        template = GENERAL_SUBAGENT_CONFIG
    elif normalized_name == "explore":
        template = EXPLORE_CONFIG
    else:
        raise ValueError(f"未知内置 Agent 模板: {template_name}")
    return template.copy_with(**overrides) if overrides else template


def get_general_config(**overrides: Any) -> AgentConfig:
    return build_builtin_agent_config("general", **overrides)


def get_general_subagent_config(**overrides: Any) -> AgentConfig:
    return build_builtin_agent_config("general_subagent", **overrides)


def get_explore_config(**overrides: Any) -> AgentConfig:
    return build_builtin_agent_config("explore", **overrides)


__all__ = [
    "EXPLORE_CONFIG",
    "GENERAL_BROWSER_TOOL_NAMES",
    "GENERAL_CONFIG",
    "GENERAL_ROOT_TOOLS",
    "GENERAL_SUBAGENT_CONFIG",
    "GENERAL_SUBAGENT_TOOLS",
    "GENERAL_TEAM_TOOLS",
    "build_builtin_agent_config",
    "get_explore_config",
    "get_general_config",
    "get_general_subagent_config",
]
