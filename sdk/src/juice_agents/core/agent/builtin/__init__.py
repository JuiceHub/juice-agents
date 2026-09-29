"""Agent 领域内置角色入口。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "build_general_agent": (".builder", "build_general_agent"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value
