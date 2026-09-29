"""Team 领域内置配置与装配入口。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "build_default_team_config": (".configs", "build_default_team_config"),
    "build_default_team_member_configs": (".configs", "build_default_team_member_configs"),
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
