"""Prebuilt declaration assembly.

Prebuilt code selects static Runner declarations only. It never persists
resource definitions or constructs a root Agent: ``Runner.run`` lazily asks
``AgentManager`` to acquire it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from juice_agents.core.registry.agents.defaults import build_builtin_agent_configs
from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.runner import Runner
from juice_agents.core.runner.config import RunnerConfig, mode_registry


def build_prebuilt_agent_declarations(
    *,
    base_dir: str | Path,
    runner_config: RunnerConfig | str | None = None,
) -> tuple[AgentConfig, ...]:
    """Return detached built-in declarations available in one Runner mode.

    Registry owns the complete, workspace-independent catalog. This helper
    only projects that catalog; local overrides are resolved by Registry's
    ``load_config`` and ``list_configs`` rather than by an initialization step.
    """

    del base_dir
    config = mode_registry.resolve(runner_config)
    return tuple(
        declaration
        for declaration in build_builtin_agent_configs()
        if not declaration.allowed_modes or config.mode_id in declaration.allowed_modes
    )


def create_prebuilt_runner(
    *,
    permission_mode: str = "default",
    runner_config: RunnerConfig | str | None = None,
    base_dir: str | Path | None = None,
    runner_id: str | None = None,
    team_name: str | None = None,
    runtime_config_path: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
    **removed: Any,
) -> Runner:
    """Create a declarative Runner without saving built-in resource definitions."""

    if removed:
        raise TypeError("prebuilt Runner 已移除 mode/root Agent 注入参数: " + ", ".join(sorted(removed)))
    workspace = Path(base_dir or Path.cwd()).expanduser().resolve()
    config = mode_registry.resolve(runner_config)
    return Runner.create(
        permission_mode=permission_mode,
        runner_config=config,
        base_dir=workspace,
        runner_id=runner_id,
        team_name=team_name,
        runtime_config_path=runtime_config_path,
        runtime_overrides=runtime_overrides,
    )


def resume_prebuilt_runner(
    *,
    runner_id: str,
    base_dir: str | Path,
    runtime_config_path: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
    **removed: Any,
) -> Runner:
    """Resume a current-schema prebuilt Runner through the normal Runner API."""

    if removed:
        raise TypeError("resume_prebuilt_runner 不接受旧运行时注入参数: " + ", ".join(sorted(removed)))
    return Runner.resume(
        runner_id=runner_id, base_dir=base_dir,
        runtime_config_path=runtime_config_path, runtime_overrides=runtime_overrides,
    )


__all__ = [
    "build_prebuilt_agent_declarations",
    "create_prebuilt_runner",
    "resume_prebuilt_runner",
]
