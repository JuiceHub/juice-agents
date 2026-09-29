"""Lazy public exports for Runner-owned runtime managers.

Managers depend on Registry declarations, so eager package imports would form a
cycle while a Registry constructs a Tool class. Export lazily instead; this
does not change ownership or the public API.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AsyncTaskManager": (".async_tasks", "AsyncTaskManager"),
    "TaskEventSink": (".async_tasks", "TaskEventSink"),
    "GraphRunManager": (".graphs", "GraphRunManager"),
    "ToolManager": (".tools", "ToolManager"),
    "TeamManager": (".team", "TeamManager"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
