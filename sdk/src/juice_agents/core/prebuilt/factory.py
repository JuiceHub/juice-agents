"""Prebuilt declaration assembly.

Prebuilt code seeds static Registry declarations only.  It never constructs a
root Agent: ``Runner.run`` lazily asks ``AgentManager`` to acquire it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from juice_agents.core.agent.builtin.configs import (
    get_explore_config,
    get_general_subagent_config,
)
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.group.builtin.configs import build_default_group_worker_configs
from juice_agents.core.registry import AgentRegistry, TeamRegistry
from juice_agents.core.runner import Runner
from juice_agents.core.runner.config import RunnerConfig, mode_registry
from juice_agents.core.team.builtin.configs import (
    build_default_team_config,
    build_default_team_member_configs,
)

logger = logging.getLogger(__name__)


def build_prebuilt_agent_declarations(
    *,
    base_dir: str | Path,
    runner_config: RunnerConfig | str | None = None,
) -> tuple[Any, ...]:
    """Build a mode's default Agent declarations without persisting them.

    Cold discovery must be able to describe the same defaults that a future
    Runner will seed, while remaining a read-only operation.  This helper is
    the single source for both paths: callers may project its return value in
    memory, and :func:`seed_prebuilt_declarations` writes those values only at
    the explicit Runner-creation boundary.
    """

    config = mode_registry.resolve(runner_config)
    workspace = Path(base_dir).expanduser().resolve()
    if config.mode_id in {"agent", "plan"}:
        return (get_general_subagent_config(), get_explore_config())
    if config.mode_id == "group":
        return build_default_group_worker_configs()
    if config.mode_id == "team":
        return build_default_team_member_configs(workspace_dir=workspace)
    # Registered custom modes deliberately receive no framework defaults.
    # Their visible Agents must come entirely from the workspace Registry.
    return ()


def seed_prebuilt_declarations(
    *,
    base_dir: str | Path,
    runner_config: RunnerConfig | str | None = None,
) -> RunnerConfig:
    """Materialize missing built-in YAML declarations for one workspace.

    This is static configuration setup, not runtime bootstrap.  Existing YAML
    always wins, so users can customize a built-in mode without code changes.
    """

    config = mode_registry.resolve(runner_config)
    workspace = Path(base_dir).expanduser().resolve()
    registry = AgentRegistry(config_context=ConfigurationContext.from_workspace(workspace))
    # The same in-memory declarations are used by cold `/agents` projection.
    # Persisting them is reserved for this explicit Runner-creation boundary.
    declarations = build_prebuilt_agent_declarations(
        base_dir=workspace,
        runner_config=config,
    )
    for declaration in declarations:
        registry.seed_config(declaration)
    if config.mode_id == "team":
        teams = TeamRegistry(config_context=ConfigurationContext.from_workspace(workspace))
        manifest = build_default_team_config()
        try:
            teams.get_manifest(manifest.team_name)
        except FileNotFoundError:
            teams.create(manifest)
    logger.info("prebuilt_declarations_seeded workspace=%s mode_id=%s count=%d", workspace, config.mode_id, len(declarations))
    return config


def create_prebuilt_runner(
    *,
    permission_mode: str = "default",
    runner_config: RunnerConfig | str | None = None,
    base_dir: str | Path | None = None,
    runner_id: str | None = None,
    team_name: str | None = None,
    runtime_config_path: str | Path | None = None,
    **removed: Any,
) -> Runner:
    """Create a declarative Runner after seeding its static definitions."""

    if removed:
        raise TypeError("prebuilt Runner 已移除 mode/root Agent 注入参数: " + ", ".join(sorted(removed)))
    workspace = Path(base_dir or Path.cwd()).expanduser().resolve()
    config = seed_prebuilt_declarations(base_dir=workspace, runner_config=runner_config)
    return Runner.create(
        permission_mode=permission_mode,
        runner_config=config,
        base_dir=workspace,
        runner_id=runner_id,
        team_name=team_name,
        runtime_config_path=runtime_config_path,
    )


def resume_prebuilt_runner(
    *,
    runner_id: str,
    base_dir: str | Path,
    runtime_config_path: str | Path | None = None,
    **removed: Any,
) -> Runner:
    """Resume a current-schema prebuilt Runner through the normal Runner API."""

    if removed:
        raise TypeError("resume_prebuilt_runner 不接受旧运行时注入参数: " + ", ".join(sorted(removed)))
    return Runner.resume(runner_id=runner_id, base_dir=base_dir, runtime_config_path=runtime_config_path)


__all__ = [
    "build_prebuilt_agent_declarations",
    "create_prebuilt_runner",
    "resume_prebuilt_runner",
    "seed_prebuilt_declarations",
]
