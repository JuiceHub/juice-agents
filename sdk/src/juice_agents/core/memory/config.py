"""Runtime configuration for workspace memory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from juice_agents.core.config.runtime_config import (
    read_runtime_config,
    update_workspace_config,
    workspace_config_path,
)


@dataclass(frozen=True, slots=True)
class DreamConfig:
    """Config for four-phase memory consolidation."""

    enabled: bool = True
    min_hours: int = 24
    min_sessions: int = 5


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    """Top-level workspace memory config."""

    enabled: bool = True
    auto_extract_enabled: bool = False
    dream: DreamConfig = DreamConfig()


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def get_memory_config(
    runtime_config_path: str | Path | None = None,
    *,
    workspace_dir: str | Path | None = None,
) -> MemoryConfig:
    """Read memory config from the unified runtime YAML, defaulting to enabled."""

    payload = read_runtime_config(runtime_config_path, workspace_dir=workspace_dir)
    raw_memory = payload.get("memory")
    if not isinstance(raw_memory, dict):
        raw_memory = {}
    raw_dream = raw_memory.get("dream")
    if not isinstance(raw_dream, dict):
        raw_dream = {}
    memory_enabled = _as_bool(raw_memory.get("enabled"), True)
    dream_enabled = _as_bool(raw_dream.get("enabled"), True) if memory_enabled else False
    return MemoryConfig(
        enabled=memory_enabled,
        auto_extract_enabled=_as_bool(raw_memory.get("auto_extract_enabled"), False),
        dream=DreamConfig(
            enabled=dream_enabled,
            min_hours=_positive_int(raw_dream.get("min_hours"), 24),
            min_sessions=_positive_int(raw_dream.get("min_sessions"), 5),
        ),
    )


def set_workspace_memory_feature(
    *,
    workspace_dir: str | Path,
    runtime_config_path: str | Path | None = None,
    feature: str,
    enabled: bool,
) -> MemoryConfig:
    """Persist a memory feature override into `<workspace>/.juice/config.yaml`."""

    normalized = str(feature or "").strip().lower()
    if normalized not in {"memory", "dream"}:
        raise ValueError("feature must be memory or dream")

    def _update(payload: dict[str, Any]) -> dict[str, Any]:
        memory = payload.get("memory")
        if not isinstance(memory, dict):
            memory = {}
        else:
            memory = dict(memory)
        if normalized == "memory":
            memory["enabled"] = bool(enabled)
        else:
            dream = memory.get("dream")
            if not isinstance(dream, dict):
                dream = {}
            else:
                dream = dict(dream)
            dream["enabled"] = bool(enabled)
            memory["dream"] = dream
        payload["memory"] = memory
        return payload

    update_workspace_config(workspace_dir, _update)
    return get_memory_config(runtime_config_path=runtime_config_path, workspace_dir=workspace_dir)


__all__ = [
    "DreamConfig",
    "MemoryConfig",
    "get_memory_config",
    "set_workspace_memory_feature",
    "workspace_config_path",
]
