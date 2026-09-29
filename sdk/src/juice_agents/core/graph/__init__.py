"""轻量 LangGraph 风格 API（多智能体与团队图）。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "START": ("juice_agents.core.graph.constants", "START"),
    "END": ("juice_agents.core.graph.constants", "END"),
    "Send": ("juice_agents.core.graph.types", "Send"),
    "Command": ("juice_agents.core.graph.types", "Command"),
    "GraphConfig": ("juice_agents.core.graph.types", "GraphConfig"),
    "GraphBuildContext": ("juice_agents.core.graph.types", "GraphBuildContext"),
    "CompiledPayloadGraph": ("juice_agents.core.graph.types", "CompiledPayloadGraph"),
    "GraphCancelledError": ("juice_agents.core.graph.types", "GraphCancelledError"),
    "GraphIncompleteError": ("juice_agents.core.graph.types", "GraphIncompleteError"),
    "GraphPausedError": ("juice_agents.core.graph.types", "GraphPausedError"),
    "StateGraph": ("juice_agents.core.graph.state_graph", "StateGraph"),
    "GraphRunStore": ("juice_agents.core.graph.runs", "GraphRunStore"),
    "GraphRunManager": ("juice_agents.core.graph.runs", "GraphRunManager"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


__all__ = list(_EXPORTS.keys())
