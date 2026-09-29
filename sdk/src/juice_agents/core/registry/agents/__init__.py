"""Agent registry 子模块导出入口。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_ATTR_TO_MODULE = {
    "AgentRegistry": ".registry",
    "AgentConfig": ".types",
    "AgentRef": ".types",
    "RemainCompressionConfig": ".types",
    "CompactCompressionConfig": ".types",
    "SessionCompressionConfig": ".types",
    "AgentConfigStore": ".store",
    "agent_to_config": ".serialization",
    "agent_to_dict": ".serialization",
}

__all__ = list(_ATTR_TO_MODULE.keys())


def __getattr__(name: str) -> Any:
    module_name = _ATTR_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    return getattr(module, name)
