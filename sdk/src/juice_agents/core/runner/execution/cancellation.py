"""Runner stream cancellation primitives."""

from __future__ import annotations

import threading
from typing import Any


class StreamCancelled(RuntimeError):
    """Raised when a running stream is interrupted by the user."""

    def __init__(self, reason: str = "user_cancelled") -> None:
        self.reason = str(reason or "user_cancelled")
        super().__init__(self.reason)


def raise_if_cancelled(cancel_event: threading.Event | None, reason: Any = None) -> None:
    """Raise `StreamCancelled` when a cooperative cancellation token is set."""

    if cancel_event is not None and cancel_event.is_set():
        raise StreamCancelled(str(reason or "user_cancelled"))


__all__ = ["StreamCancelled", "raise_if_cancelled"]
