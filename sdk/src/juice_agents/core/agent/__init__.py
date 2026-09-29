"""
agents 包导出入口。

这里使用 lazy import，避免仅访问 `juice_agents.core.agent` 时就把整个
`agents.py` 执行链提前拉起，形成循环依赖。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "MultiStepAgent",
    "ReActAgent",
    "CodeActAgent",
    "COLOR_RESET",
    "AGENT_SNAPSHOT_SCHEMA_VERSION",
    "AgentExecutionInterrupted",
    "AgentLifecycle",
    "AgentManager",
    "AgentManagerCallbacks",
    "AgentSnapshot",
    "AgentStatus",
    "JsonAgentSnapshotStore",
    "ManagedAgent",
]


def __getattr__(name: str) -> Any:
    if name in {
        "AGENT_SNAPSHOT_SCHEMA_VERSION",
        "AgentExecutionInterrupted",
        "AgentLifecycle",
        "AgentManager",
        "AgentManagerCallbacks",
        "AgentSnapshot",
        "AgentStatus",
        "JsonAgentSnapshotStore",
        "ManagedAgent",
    }:
        module = import_module(".manager", __name__)
        return getattr(module, name)
    if name in __all__:
        module = import_module(".agents", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
