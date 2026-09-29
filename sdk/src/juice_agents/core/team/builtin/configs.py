"""Built-in declarations for the lightweight Team mode.

Team is a relationship between globally registered workers.  The Runner's
``root`` remains the coordinator, so this module deliberately creates no
``teamlead`` Agent and no private Team configuration copies.
"""

from __future__ import annotations

from pathlib import Path

from juice_agents.core.agent.builtin.configs import GENERAL_TEAM_TOOLS, get_general_config
from juice_agents.core.registry.agents.types import AgentConfig, normalize_agent_type_policy
from juice_agents.core.registry.tools.types import ToolRef
from juice_agents.core.registry.teams.types import TeamManifest

DEFAULT_TEAM_NAME = "default"

DEFAULT_TEAMMATE_INSTRUCTIONS = (
    "You are a Team member. A task in the turn prompt is already claimed for "
    "you; complete it with team_task_complete and concise evidence. In a "
    "message-only turn, inspect team_task_list and claim ready work if needed. "
    "Use send_message to discuss blockers or results with root or "
    "other members. Use team_member_stop_request if you need root to close "
    "your session; ordinary message text does not stop it. Incoming messages "
    "appear as attachments at the start of "
    "your next turn. The root owns user communication and Team completion."
)

_TEAM_MEMBER_TOOL_NAMES = (
    "team_task_list",
    "team_task_claim",
    "team_task_complete",
    "send_message",
    "team_member_stop_request",
)


def _workspace_tools(workspace_dir: str) -> tuple[ToolRef, ...]:
    """Bind only file-capable worker tools to the selected workspace."""

    tools: list[ToolRef] = []
    for raw_tool in GENERAL_TEAM_TOOLS:
        tool = ToolRef.from_raw(raw_tool)
        if tool.name in {"todos", "plan", "exit_plan", "agent_tool"}:
            continue
        params = dict(tool.params)
        if workspace_dir:
            if tool.name == "shell":
                params.setdefault("default_workdir", workspace_dir)
            elif tool.name in {"read", "write", "edit"}:
                params.setdefault("root_dir", workspace_dir)
        tools.append(tool.copy_with(params=params))
    tools.extend(ToolRef(name=name) for name in _TEAM_MEMBER_TOOL_NAMES)
    return tuple(tools)


def _member(
    *,
    name: str,
    description: str,
    workspace_dir: str,
    agent_type: str,
) -> AgentConfig:
    """Return one independent global Team worker declaration.

    ``allowed_modes`` is part of the declaration itself, which makes a Team
    manifest safe to validate without constructing a Runner.
    """

    base = get_general_config(
        name=name,
        agent_type=normalize_agent_type_policy(agent_type),
        description=description,
        instructions=DEFAULT_TEAMMATE_INSTRUCTIONS,
        max_steps=12,
        tools=[tool.to_dict() for tool in _workspace_tools(workspace_dir)],
    )
    return base.copy_with(
        allowed_modes=["team"],
        managed_agent_names=[],
        # TeamManager inbox and task claims span multiple member turns.
        lifecycle="persistent",
    )


def build_default_team_member_configs(
    *,
    workspace_dir: str | Path | None = None,
    agent_type: str = "default",
) -> tuple[AgentConfig, ...]:
    """Create global declarations referenced by the default Team manifest."""

    workspace = "" if workspace_dir is None else str(Path(workspace_dir).expanduser().resolve())
    return (
        _member(
            name="researcher",
            description="Researches and summarizes evidence for the root coordinator.",
            workspace_dir=workspace,
            agent_type=agent_type,
        ),
        _member(
            name="developer",
            description="Implements bounded development tasks delegated by the root coordinator.",
            workspace_dir=workspace,
            agent_type=agent_type,
        ),
    )


def build_default_team_config(
    *,
    workspace_dir: str | Path | None = None,
    agent_type: str = "default",
) -> TeamManifest:
    """Return the schema-2 default Team relationship.

    The unused arguments preserve the public factory signature while the
    worker declarations are supplied separately by
    :func:`build_default_team_member_configs`.
    """

    del workspace_dir, agent_type
    return TeamManifest(
        team_name=DEFAULT_TEAM_NAME,
        member_names=(),
        description="Team coordinated by the Runner root.",
    )


__all__ = [
    "DEFAULT_TEAM_NAME",
    "DEFAULT_TEAMMATE_INSTRUCTIONS",
    "build_default_team_config",
    "build_default_team_member_configs",
]
