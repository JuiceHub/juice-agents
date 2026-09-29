"""Pure Agent declaration availability rules.

The Registry, prompt projection and dispatch paths all need the same answer
to one question: can this declaration be used in the current Runner mode?
Keeping that answer free of Registry I/O and live Agent objects prevents
presentation code from becoming an alternate authorization implementation.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .types import AgentConfig


ROOT_AGENT_NAME = "root"


def normalize_agent_names(values: Iterable[Any] | None) -> frozenset[str] | None:
    """Return a normalized allow-list while preserving ``None`` as unrestricted."""

    if values is None:
        return None
    return frozenset(str(value).strip() for value in values if str(value).strip())


def is_mode_allowed(config: AgentConfig, mode_id: str) -> bool:
    """Check an AgentConfig's portable mode declaration without side effects."""

    normalized_mode = str(mode_id or "").strip()
    if not normalized_mode:
        raise ValueError("mode_id 不能为空")
    return config.allowed_modes is None or normalized_mode in config.allowed_modes


def is_agent_available(
    config: AgentConfig,
    *,
    mode_id: str,
    allowed_agent_names: Iterable[Any] | None = None,
    disabled_agent_names: Iterable[Any] | None = None,
    include_root: bool = False,
) -> bool:
    """Apply the single availability rule shared by listing and dispatch.

    ``allowed_agent_names=None`` means that the caller is a root coordinator
    which may discover every registered declaration.  Passing an empty
    iterable is different: it represents a normal child with no managed
    children.  Disabled names are runner/mode runtime state and never mutate
    the declaration itself.
    """

    if not include_root and config.name == ROOT_AGENT_NAME:
        return False
    if not is_mode_allowed(config, mode_id):
        return False
    allowed = normalize_agent_names(allowed_agent_names)
    if allowed is not None and config.name not in allowed:
        return False
    disabled = normalize_agent_names(disabled_agent_names) or frozenset()
    return config.name not in disabled


def filter_available_agent_configs(
    configs: Iterable[AgentConfig],
    *,
    mode_id: str,
    allowed_agent_names: Iterable[Any] | None = None,
    disabled_agent_names: Iterable[Any] | None = None,
    include_root: bool = False,
) -> list[AgentConfig]:
    """Return declaration copies that satisfy the shared availability rule."""

    return [
        AgentConfig.from_dict(config)
        for config in configs
        if is_agent_available(
            config,
            mode_id=mode_id,
            allowed_agent_names=allowed_agent_names,
            disabled_agent_names=disabled_agent_names,
            include_root=include_root,
        )
    ]


__all__ = [
    "ROOT_AGENT_NAME",
    "filter_available_agent_configs",
    "is_agent_available",
    "is_mode_allowed",
    "normalize_agent_names",
]
