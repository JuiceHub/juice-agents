"""Explicit control over Runner-owned asynchronous tasks."""

from __future__ import annotations

import logging
from typing import Any

from ...runtime.base_tools import Tool

logger = logging.getLogger(__name__)


class AsyncTaskStopTool(Tool):
    """Stop one active background task through the current RunnerContext."""

    name = "async_task_stop"
    # It only stops a Runner-owned task and is explicitly allowed by the
    # Plan Mode policy so a planner can settle delegated investigation.
    is_read_only = True
    description = "停止运行中的后台 async task"
    inputs = {
        "async_task_id": {"type": "string", "description": "目标 async_task_id（由启动工具返回）"},
        "reason": {"type": "string", "description": "中止原因，可选", "required": False},
    }
    outputs = {
        "status": {"type": "string", "description": "stopped / not_found / already_finished"},
        "message": {"type": "string", "description": "操作结果说明"},
    }

    def __init__(self) -> None:
        super().__init__()
        self.owner_agent: Any | None = None

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _runner_context(self) -> Any:
        if self.owner_agent is None:
            raise ValueError("async_task_stop 需要先绑定 owner_agent")
        runner_context = getattr(self.owner_agent, "runner_context", None)
        if runner_context is None:
            raise RuntimeError("async_task_stop 必须绑定到 Runner，禁止独立调用")
        return runner_context

    def forward(self, async_task_id: str, reason: str = "agent_requested") -> dict[str, Any]:
        if not isinstance(async_task_id, str) or not async_task_id.strip():
            return {"status": "not_found", "message": "async_task_id 不能为空"}
        try:
            runner_context = self._runner_context()
            manager = getattr(getattr(runner_context, "runner", None), "async_task_manager", None)
            task = None if manager is None else manager.get_task(async_task_id)
            if task is None:
                return {"status": "not_found", "message": "任务已不存在"}
            current_status = str(task.get("status") or "").strip()
            if current_status not in {"pending", "running"}:
                return {"status": "already_finished", "message": f"任务已处于 {current_status} 状态，无需停止"}
            if runner_context.cancel_async_task(async_task_id, reason=reason) is None:
                return {"status": "not_found", "message": "任务已不存在"}
            return {"status": "stopped", "message": f"已成功停止任务 {async_task_id}"}
        except Exception as exc:  # pragma: no cover - external task races
            logger.exception("async_task_stop 执行失败: async_task_id=%s", async_task_id)
            return {"status": "failed", "message": f"停止失败：{exc}"}


__all__ = ["AsyncTaskStopTool"]
