"""The single, read-only catalog of framework Agent declarations.

Factories are imported lazily to keep Registry/type imports independent of
Agent and Runner construction. Every lookup returns detached data, without
workspace paths, so runtime overlays cannot modify the framework defaults.
"""

from __future__ import annotations

from .types import AgentConfig


def build_builtin_agent_configs() -> tuple[AgentConfig, ...]:
    """Collect root, general subagents, Group workers and Team candidates."""

    from juice_agents.core.agent.builtin.configs import (
        get_explore_config,
        get_general_subagent_config,
    )
    from juice_agents.core.agent.root_config import build_root_declaration
    from juice_agents.core.group.builtin.configs import build_default_group_worker_configs
    from juice_agents.core.team.builtin.configs import build_default_team_member_configs

    declarations = (
        build_root_declaration(),
        get_general_subagent_config(),
        get_explore_config(),
        *build_default_group_worker_configs(),
        *build_default_team_member_configs(),
    )
    return tuple(AgentConfig.from_dict(config) for config in declarations)


__all__ = ["build_builtin_agent_configs"]
