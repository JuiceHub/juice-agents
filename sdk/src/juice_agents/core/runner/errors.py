"""Public Runner errors."""

from __future__ import annotations


class RunnerBusyError(RuntimeError):
    """Raised when one Runner is asked to execute overlapping user requests."""


__all__ = ["RunnerBusyError"]
