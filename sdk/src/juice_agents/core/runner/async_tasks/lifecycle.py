"""Terminal transitions for independent asynchronous tasks."""

from __future__ import annotations

import time
from .internal_types import AsyncTaskState


def close_async_task(async_task: AsyncTaskState, *, status: str, reason: str = "", error: str = "") -> AsyncTaskState:
    """Mutate task state; caller atomically saves it with its notification."""
    async_task.update(status=status, finished_at=time.time(), closed_reason=reason)
    if error:
        async_task["error"] = error
    return async_task
