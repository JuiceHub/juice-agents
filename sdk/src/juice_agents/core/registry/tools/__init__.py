"""Tool registry 子模块导出入口。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_ATTR_TO_MODULE = {
    "ToolRegistry": ".registry",
    "ToolConfig": ".types",
    "ToolRef": ".types",
    "normalize_tool_ref": ".types",
    "normalize_tool_refs": ".types",
    "ToolConfigStore": ".store",
    "create_dynamic_tool_class": ".registry",
    "get_default_tool_factories": ".defaults",
}

__all__ = list(_ATTR_TO_MODULE.keys())


def __getattr__(name: str) -> Any:
    module_name = _ATTR_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    return getattr(module, name)
