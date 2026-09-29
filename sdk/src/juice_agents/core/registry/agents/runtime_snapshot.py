"""Serialize a live Agent into a validated declaration snapshot.

Runtime snapshots are used for one-time declaration materialization and Runner
reconstruction. Subsequent session persistence does not overwrite files edited
explicitly through ``agent_manage``.
"""

from __future__ import annotations

from typing import Any

from juice_agents.core.registry.tools.types import ToolRef

from .types import AgentConfig, DEFAULT_MODEL_CONFIG_NAME, DEFAULT_MODEL_EFFORT


def _runtime_agent_type(agent: Any) -> str:
    return str(
        getattr(agent, "_resolved_agent_type", "")
        or ("codeact" if "codeact" in type(agent).__name__.lower() else "react")
    )


def _declared_tool_refs(agent: Any) -> list[dict[str, Any]]:
    """Persist declared references, never incidental live Tool instances."""
    declared = getattr(agent, "_declared_tool_refs", None)
    if not isinstance(declared, tuple):
        return []
    return [ToolRef.from_raw(item).to_dict() for item in declared]


def build_runtime_agent_config(
    agent: Any,
    *,
    model_config_name: str = DEFAULT_MODEL_CONFIG_NAME,
    model_effort: str = DEFAULT_MODEL_EFFORT,
    tool_refs: list[dict[str, Any]] | None = None,
    log_file_path: str | None = None,
) -> AgentConfig:
    """Build a validated declaration snapshot while preserving runtime identity."""
    declared = getattr(agent, "_declared_agent_config", None)
    if isinstance(declared, AgentConfig):
        payload = declared.to_dict()
    else:
        payload: dict[str, Any] = {
            "name": str(getattr(agent, "name", "") or "").strip(),
            "agent_type": _runtime_agent_type(agent),
            "model_config_name": str(model_config_name or DEFAULT_MODEL_CONFIG_NAME),
            "model_effort": str(model_effort or DEFAULT_MODEL_EFFORT),
            "description": str(getattr(agent, "description", "") or ""),
            "instructions": getattr(agent, "instructions", None),
            "prompt_language": getattr(agent, "prompt_language", "en"),
            "max_steps": int(getattr(agent, "max_steps", 5) or 5),
            "context_window_tokens": int(
                getattr(agent, "context_window_tokens", 128_000)
            ),
            "max_tool_calls_per_step": int(
                getattr(agent, "max_tool_calls_per_step", 8)
            ),
            "session_compression": dict(
                getattr(agent, "session_compression", {}) or {}
            ),
            "tools": _declared_tool_refs(agent),
            "managed_agent_names": list(
                getattr(agent, "available_managed_agent_names", []) or []
            ),
            "lifecycle": str(
                getattr(agent, "_agent_lifecycle", "") or "functional"
            ),
            "isolation": str(getattr(agent, "_agent_isolation", "") or "none"),
        }
        if getattr(agent, "output_schema", None) is not None:
            payload["output_schema"] = getattr(agent, "output_schema")
        if getattr(agent, "log_file_path", None) is not None:
            payload["log_file_path"] = str(getattr(agent, "log_file_path"))
        if hasattr(agent, "authorized_imports"):
            payload["additional_authorized_imports"] = list(
                getattr(agent, "authorized_imports") or []
            )

    if tool_refs is not None:
        payload["tools"] = list(tool_refs)
    payload["managed_agent_names"] = list(
        getattr(agent, "available_managed_agent_names", []) or []
    )
    payload["lifecycle"] = str(
        getattr(agent, "_agent_lifecycle", "")
        or payload.get("lifecycle")
        or "functional"
    )
    payload["isolation"] = str(
        getattr(agent, "_agent_isolation", "")
        or payload.get("isolation")
        or "none"
    )
    if log_file_path is not None:
        payload["log_file_path"] = str(log_file_path)
    payload.setdefault(
        "model_config_name",
        str(model_config_name or DEFAULT_MODEL_CONFIG_NAME),
    )
    payload.setdefault("model_effort", str(model_effort or DEFAULT_MODEL_EFFORT))
    return AgentConfig.from_dict(payload)


__all__ = ["build_runtime_agent_config"]
