"""Runner async_tasks / agents 生命周期辅助。"""

from __future__ import annotations

import time
from typing import Any

from .internal_types import AsyncTaskState, RunnerAgentState, RunnerAgentStatus


def build_agent_state(
    *,
    agent_id: str,
    agent_name: str,
    agent_kind: str,
    metadata: dict[str, Any] | None = None,
) -> RunnerAgentState:
    return {
        "agent_id": str(agent_id or "").strip(),
        "agent_name": str(agent_name or "").strip(),
        "agent_kind": str(agent_kind or "").strip(),
        "status": "active",
        "current_async_task_id": "",
        "last_error": "",
        "metadata": dict(metadata or {}),
    }


def set_agent_status(agent: RunnerAgentState, status: RunnerAgentStatus) -> RunnerAgentState:
    agent["status"] = status
    return agent


def set_agent_last_error(agent: RunnerAgentState, error: str) -> RunnerAgentState:
    agent["last_error"] = str(error or "").strip()
    return agent


def clear_agent_run(async_task: AsyncTaskState) -> AsyncTaskState:
    metadata = dict(async_task.get("metadata") or {})
    metadata["run_id"] = ""
    async_task["metadata"] = metadata
    return async_task


def set_agent_run(async_task: AsyncTaskState, run_id: str) -> AsyncTaskState:
    metadata = dict(async_task.get("metadata") or {})
    metadata["run_id"] = str(run_id or "").strip()
    async_task["metadata"] = metadata
    return async_task


def set_teammate_shutdown(async_task: AsyncTaskState, requested: bool) -> AsyncTaskState:
    metadata = dict(async_task.get("metadata") or {})
    metadata["shutdown_requested"] = bool(requested)
    async_task["metadata"] = metadata
    return async_task


def set_teammate_flush_index(async_task: AsyncTaskState, index: int) -> AsyncTaskState:
    metadata = dict(async_task.get("metadata") or {})
    metadata["last_flushed_message_index"] = max(0, int(index))
    async_task["metadata"] = metadata
    return async_task


def get_teammate_flush_index(async_task: AsyncTaskState) -> int:
    return max(0, int(dict(async_task.get("metadata") or {}).get("last_flushed_message_index") or 0))


def get_agent_run_id(async_task: AsyncTaskState) -> str:
    return str(dict(async_task.get("metadata") or {}).get("run_id") or "").strip()


def get_teammate_shutdown_requested(async_task: AsyncTaskState) -> bool:
    return bool(dict(async_task.get("metadata") or {}).get("shutdown_requested"))


def close_async_task(
    async_task: AsyncTaskState,
    *,
    status: str,
    reason: str = "",
    error: str = "",
) -> AsyncTaskState:
    async_task["status"] = str(status or "")
    async_task["finished_at"] = time.time()
    async_task["closed_reason"] = str(reason or "")
    if error:
        async_task["error"] = str(error)
    clear_agent_run(async_task)
    if str(async_task.get("kind") or "").strip() == "teammate":
        set_teammate_shutdown(async_task, requested=False)
    return async_task


__all__ = [
    "build_agent_state",
    "clear_agent_run",
    "close_async_task",
    "get_agent_run_id",
    "get_teammate_flush_index",
    "get_teammate_shutdown_requested",
    "set_agent_last_error",
    "set_agent_run",
    "set_teammate_flush_index",
    "set_teammate_shutdown",
    "set_agent_status",
]
