"""Static declaration reconciliation for Agent evolution tools.

The functions here change Registry declarations and return an explicit
``fresh_acquire`` receipt. They never touch a live Agent, construct a
temporary Agent, or look up a Runner. ``AgentManager`` is the only component
allowed to turn these declarations into runtime instances.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from juice_agents.core.registry.agents.registry import AgentRegistry
from juice_agents.core.registry.agents.types import AgentConfig

logger = logging.getLogger(__name__)


def _receipt(owner_agent: Any, *, config: AgentConfig | None = None) -> dict[str, Any]:
    """Describe a persisted declaration change without a live refresh path."""

    return {
        "status": "scheduled",
        "effective_from": "fresh_acquire",
        "owner_agent": str(getattr(owner_agent, "name", "") or ""),
        "agent_name": "" if config is None else config.name,
        "deferred_fields": [],
    }


def _owner_registry(owner_agent: Any) -> tuple[AgentRegistry, AgentConfig] | None:
    """Recover only the static declaration route attached at construction."""

    context = getattr(owner_agent, "_declared_config_context", None)
    raw_dir = getattr(owner_agent, "_declared_agent_config_dir", None)
    if context is None or not raw_dir:
        return None
    registry = AgentRegistry(
        config_context=context,
        config_dir=Path(raw_dir),
        tool_config_dir=context.tools_dir,
    )
    name = str(getattr(owner_agent, "name", "") or "").strip()
    if not name:
        return None
    try:
        return registry, registry.load_config(name)
    except FileNotFoundError:
        declared = getattr(owner_agent, "_declared_agent_config", None)
        if not isinstance(declared, AgentConfig):
            return None
        return registry, registry.save_config(declared, name=name)


def _update_owner_config(
    owner_agent: Any,
    operation: Callable[[AgentRegistry, str], AgentConfig],
) -> AgentConfig | None:
    resolved = _owner_registry(owner_agent)
    if resolved is None:
        return None
    registry, current = resolved
    return operation(registry, current.name)


def _require_saved(owner_agent: Any, saved: AgentConfig | None) -> dict[str, Any]:
    if saved is None:
        return {
            "status": "unsupported",
            "effective_from": "fresh_acquire",
            "owner_agent": str(getattr(owner_agent, "name", "") or ""),
            "deferred_fields": [],
            "reason": "Agent 缺少声明配置路由，需 fresh acquire",
        }
    return _receipt(owner_agent, config=saved)


def _trusted_live_runner(owner_agent: Any) -> Any | None:
    """Return the current Runner only when its Manager owns this instance.

    Evolution tools receive editable configuration data, so a claimed name or
    role cannot be used to alter Runner-local unload state.  The Manager
    record is the authority for this small runtime-only operation.
    """

    context = getattr(owner_agent, "runner_context", None)
    runner = getattr(context, "runner", None)
    if runner is None:
        return None
    try:
        managed = runner.agent_manager.get(str(getattr(context, "agent_id", "") or ""))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return runner if getattr(managed, "instance", None) is owner_agent else None


def _runner_local_receipt(owner_agent: Any, *, name: str, action: str) -> dict[str, Any]:
    runner = _trusted_live_runner(owner_agent)
    if runner is None:
        return {}
    if action == "disable":
        runner.disable_agent(name)
        status = "disabled"
    else:
        runner.enable_agent(name)
        status = "enabled"
    return {
        "status": status,
        "effective_from": "current_runner_mode",
        "owner_agent": str(getattr(owner_agent, "name", "") or ""),
        "agent_name": name,
        "mode_id": str(getattr(runner, "mode_id", "") or ""),
        "deferred_fields": [],
    }


def bind_tool(owner_agent: Any, name: str) -> dict[str, Any]:
    """Persist a Tool reference; a later Manager acquire realizes it."""

    return _require_saved(
        owner_agent,
        _update_owner_config(
            owner_agent,
            lambda registry, owner_name: registry.bind_tool_ref(owner_name, str(name or "").strip()),
        ),
    )


def unload_tool(owner_agent: Any, name: str) -> dict[str, Any]:
    """Remove a Tool reference without mutating a live Agent."""

    return _require_saved(
        owner_agent,
        _update_owner_config(
            owner_agent,
            lambda registry, owner_name: registry.unload_tool_ref(owner_name, str(name or "").strip()),
        ),
    )


def bind_skill(owner_agent: Any, name: str) -> dict[str, Any]:
    normalized = str(name or "").strip().lower().split("/")[-1]
    return _require_saved(
        owner_agent,
        _update_owner_config(
            owner_agent,
            lambda registry, owner_name: registry.bind_skill_ref(owner_name, normalized),
        ),
    )


def unload_skill(owner_agent: Any, name: str) -> dict[str, Any]:
    normalized = str(name or "").strip().lower().split("/")[-1]
    return _require_saved(
        owner_agent,
        _update_owner_config(
            owner_agent,
            lambda registry, owner_name: registry.unload_skill_ref(owner_name, normalized),
        ),
    )


def activate_saved_agent(
    requester: Any,
    config: AgentConfig,
    *,
    config_dir: str | Path,
    target_agent: Any | None = None,
    relationship_owner: Any | None = None,
    target_is_self: bool | None = None,
    update_relationship: bool = True,
) -> dict[str, Any]:
    """Persist a static owner relationship for a saved declaration.

    Live-target parameters remain only for the declaration tool's stable call
    shape. They cannot trigger a live refresh.
    """

    del config_dir, target_agent
    local = _runner_local_receipt(requester, name=config.name, action="enable")
    if local:
        # A save updates the Registry declaration; explicit activation also
        # clears only this Runner/mode's prior unload state.  It never writes
        # the effective root configuration back to YAML.
        local["effective_from"] = "fresh_acquire_and_current_runner_mode"
        return local
    is_self = (
        config.name == str(getattr(requester, "name", "") or "")
        if target_is_self is None
        else bool(target_is_self)
    )
    if update_relationship and not is_self:
        owner = relationship_owner or requester
        saved = _update_owner_config(
            owner,
            lambda registry, owner_name: registry.bind_managed_agent_ref(owner_name, config.name),
        )
        if saved is None:
            return _require_saved(owner, None)
    logger.info(
        "agent declaration saved: requester=%s target=%s relationship=%s",
        getattr(requester, "name", ""),
        config.name,
        update_relationship and not is_self,
    )
    return _receipt(requester, config=config)


def unload_agent(
    requester: Any,
    name: str,
    *,
    target_is_self: bool | None = None,
    relationship_owner: Any | None = None,
    target_agent: Any | None = None,
    update_relationship: bool = True,
) -> dict[str, Any]:
    """Remove a static relationship; AgentManager releases runtime separately."""

    del target_agent
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("agent name 不能为空")
    local = _runner_local_receipt(requester, name=normalized, action="disable")
    if local:
        return local
    is_self = (
        normalized == str(getattr(requester, "name", "") or "")
        if target_is_self is None
        else bool(target_is_self)
    )
    if update_relationship:
        owner = relationship_owner or requester
        saved = _update_owner_config(
            owner,
            lambda registry, owner_name: registry.unload_managed_agent_ref(owner_name, normalized),
        )
        if saved is None:
            return _require_saved(owner, None)
    logger.info(
        "agent declaration relationship removed: requester=%s target=%s self=%s",
        getattr(requester, "name", ""),
        normalized,
        is_self,
    )
    return _receipt(requester)


__all__ = [
    "activate_saved_agent",
    "bind_skill",
    "bind_tool",
    "unload_agent",
    "unload_skill",
    "unload_tool",
]
