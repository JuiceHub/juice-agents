"""Lazy exports for Agent protocols and implementations.

Mutable execution ownership lives in ``core.managers.agents``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["MultiStepAgent", "ReActAgent", "CodeActAgent", "COLOR_RESET"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        return getattr(import_module(".agents", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
