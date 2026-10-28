"""Portable root declaration and detached Runner mode configuration.

AgentRegistry resolves ``root`` from local overrides or the built-in catalog.
Runner-specific paths, planning restrictions and Team coordination apply to a copy,
so opening a Runner in another mode never rewrites the shared declaration.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.registry.tools.types import ToolRef

from .builtin.configs import get_general_config


_PLAN_ROOT_INSTRUCTIONS = (
    "You are the user-facing root planner. Clarify requirements, inspect the "
    "codebase, compare implementation options, and make the final design "
    "decision yourself. You may delegate bounded read-only investigation to "
    "the `general` and `explore` agents, but they only provide evidence. Do "
    "not edit repository files, execute code, or run workflows in this mode. "
    "When the plan is ready, write the complete approved artifact with `plan` "
    "and use `exit_plan` to request approval. The only writable artifact is "
    "the Runner-owned plan file shown by the plan tool."
)

# These tools produce no repository mutation.  The policy layer separately
# verifies every execution, so this small surface is both an accurate prompt
# description and a defense in depth boundary for Plan Mode.
_PLAN_ROOT_TOOL_NAMES = frozenset(
    {
        "read",
        "glob",
        "grep",
        "web_search",
        "api_web_search",
        "agent_tool",
        "agents_list",
        "agent_view",
        "tools_list",
        "tool_view",
        "skills_list",
        "skill_view",
        "plugins_list",
        "plugin_view",
        "plan",
        "exit_plan",
        "async_task_stop",
    }
)

_TEAM_ROOT_INSTRUCTIONS = (
    "You are the Team root and own user communication. Use team_create to make "
    "an empty Team, or team_use to start a new board from an existing Team. "
    "Create members with exactly one shared agent_name or Team-local config. "
    "Use agents_list to find eligible shared Agents. For a new role, pass a minimal local config "
    "such as {'description': 'joke teller', 'instructions': 'tell jokes'}; "
    "Team supplies persistent lifecycle and team visibility. "
    "Create tasks with eligible_members='all' or an explicit nonempty list of "
    "existing members. Eligibility is frozen at creation. The Runner dispatches "
    "ready tasks; teammates claim and complete them. Check team_task_list for "
    "failures: an in_progress task with error needs a root team_task_update before "
    "it can run again. Use send_message for directed discussion. Only you can "
    "create or update tasks and manage members. A member may send a structured "
    "team_member_stop_request; inspect it before calling team_member_close. "
    "Ordinary message text never stops a member. Call team_finish after all work "
    "and messages settle. Keep user updates concise."
)

_TEAM_ROOT_TOOLS = (
    "team_task_create",
    "team_task_list",
    "team_task_update",
    "team_create",
    "team_use",
    "send_message",
    "team_member_create",
    "team_member_restart",
    "team_member_close",
    "team_finish",
)


def build_root_declaration() -> AgentConfig:
    """Return the portable mode-independent built-in root declaration.

    In particular, this value must never contain a Runner plan path or the
    fixed Plan/Team workflow. Existing workspace declarations always win.
    """

    return AgentConfig.from_dict(get_general_config())


def build_root_config(
    *,
    mode_id: str,
    plan_file: str | Path,
    managed_agent_names: tuple[str, ...] | list[str] | None = None,
    declaration: AgentConfig | None = None,
) -> AgentConfig:
    """Build the effective root configuration for one Runner mode.

    A fresh value is returned for each call.  This prevents mode-specific
    paths and permissions from contaminating reusable Registry declarations.
    """

    # Detach nested tool parameters and schemas as well as the frozen value;
    # consumers may construct tools from them without touching Registry data.
    declared = AgentConfig.from_dict(declaration or build_root_declaration())
    if declared.name != "root":
        raise ValueError("root declaration 的 name 必须为 root")
    normalized_mode = str(mode_id or "agent").strip() or "agent"
    base_tools = declared.tools
    instructions = declared.instructions
    if normalized_mode == "plan":
        base_tools = tuple(
            ref
            for ref in base_tools
            if ref.name in _PLAN_ROOT_TOOL_NAMES and ref.name != "agent_tool"
        )
        # ``GENERAL_ROOT_TOOLS`` normally does not include workflow artifacts,
        # because ordinary execution never needs them.  Plan Mode adds only
        # the two root-only planning tools and binds the plan path explicitly.
        base_tools = (
            *base_tools,
            ToolRef(name="agent_tool", params={"read_only_only": True}),
            ToolRef(name="plan", params={"file_path": str(plan_file)}),
            ToolRef(name="exit_plan"),
            ToolRef(name="async_task_stop"),
        )
        instructions = _PLAN_ROOT_INSTRUCTIONS
    elif normalized_mode == "team":
        # Team coordination is a fixed system-prompt workflow. The user prompt
        # carries only the current request; tools mutate Runner-owned state.
        base_tools = (
            *(ref for ref in base_tools if ref.name != "agent_tool"),
            *(ToolRef(name=name) for name in _TEAM_ROOT_TOOLS),
        )
        instructions = _TEAM_ROOT_INSTRUCTIONS

    # Keep ordering stable while avoiding duplicate refs when a custom base
    # tool set grows to include a planning tool in a future release.
    tools_by_name: dict[str, ToolRef] = {}
    for ref in base_tools:
        # Plan-specific references intentionally replace any default ref with
        # the same name (notably agent_tool's read_only_only setting).
        tools_by_name[ref.name] = ref
    return replace(
        declared,
        instructions=instructions,
        tools=tuple(tools_by_name.values()),
        # This is only a declaration relationship.  Availability is narrowed
        # again by the mode policy and by the root's persisted Runner state.
        # The Runner resolves this list from the same availability predicate
        # used for listing and dispatch. ``None`` retains the declaration's
        # relationships for a standalone root configuration.
        managed_agent_names=(
            declared.managed_agent_names
            if managed_agent_names is None
            else tuple(managed_agent_names)
        ),
    )


__all__ = ["build_root_config", "build_root_declaration"]
