"""Executor mixin: _run_async_task and failure summary builder."""

from __future__ import annotations

import copy
import logging
import time
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from .kinds import to_public_kind

if TYPE_CHECKING:
    from .internal_types import AsyncTaskState as InternalAsyncTaskState

logger = logging.getLogger(__name__)


def _coerce_jsonable(value: Any) -> Any:
    try:
        import json
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return str(value)


class _AsyncTaskExecutorMixin:
    """Mixin providing async task execution logic."""

    def _commit_terminal_async_task(
        self,
        *,
        async_task_id: str,
        status: str,
        finished_at: float,
        output_event: dict[str, Any],
        summary: str,
        state_updates: dict[str, Any],
        extra_payload: dict[str, Any] | None = None,
    ) -> InternalAsyncTaskState | None:
        """
        原子化提交后台任务的终态、最终事件与 pending notification。

        ``get_async_task`` 与持久化都共用 registry 的重入锁。这里先写最终
        ``events.json``，再把终态和 notification 一次持久化；锁释放后调用方
        才能观察到 completed/failed。这样“终态可见”就意味着 worker 已不再
        向任务目录写文件，避免轮询方立即清理工作目录时与后台落盘发生竞态。
        """

        with self._lock:
            async_task = copy.deepcopy(self._async_tasks[async_task_id])
            if str(async_task.get("status") or "").strip() == "killed":
                return None

            output_dir = Path(str(async_task["output_dir"]))
            self._append_async_task_output(
                output_dir,
                {
                    "async_task_id": async_task_id,
                    "created_at": finished_at,
                    **output_event,
                },
            )
            async_task.update(state_updates)
            async_task["status"] = status
            async_task["finished_at"] = finished_at

            # 只更新内存快照，不在 notification 之前暴露终态。
            # _emit_notification 会把二者合并为同一次持久化提交。
            self._store_async_task_locked(async_task)
            self._emit_notification(
                async_task,
                status=status,
                summary=summary,
                extra_payload=extra_payload,
            )
            return async_task

    def _build_async_task_failure_summary(self, async_task: InternalAsyncTaskState) -> str:
        public_type = to_public_kind(async_task.get("kind"))
        metadata = dict(async_task.get("metadata") or {})
        if public_type == "local_agent":
            target_name = str(metadata.get("target_agent_name") or "").strip()
            if target_name:
                return f"local_agent 子任务失败：{target_name}"
            return "local_agent 子任务失败"
        if public_type == "local_bash":
            return "后台 shell 命令执行失败"
        if public_type == "local_graph":
            graph_name = str(metadata.get("graph_name") or "").strip()
            if graph_name:
                return f"local_graph 任务失败：{graph_name}"
            return "local_graph 任务执行失败"
        return f"{public_type} 任务执行失败"

    def _run_async_task(
        self,
        async_task_id: str,
        runner: Callable[[InternalAsyncTaskState, Path], Any],
        summary_builder: Callable[[Any], str],
    ) -> Any:
        with self._lock:
            async_task = copy.deepcopy(self._async_tasks[async_task_id])
            if str(async_task.get("status") or "").strip() == "killed":
                return None
            async_task["status"] = "running"
            async_task["started_at"] = time.time()
            self._store_async_task_locked(async_task)
            self._persist_locked()
        output_dir = Path(str(async_task["output_dir"]))
        self._append_async_task_output(
            output_dir,
            {"async_task_id": async_task_id, "event": "started", "created_at": async_task["started_at"]},
        )
        try:
            result = runner(copy.deepcopy(async_task), output_dir)
            finished_at = time.time()
            jsonable_result = _coerce_jsonable(result)
            self._commit_terminal_async_task(
                async_task_id=async_task_id,
                status="completed",
                finished_at=finished_at,
                output_event={"event": "completed", "result": jsonable_result},
                summary=summary_builder(result),
                state_updates={"closed_reason": "", "error": ""},
                extra_payload={"result": jsonable_result},
            )
            return result
        except Exception as exc:
            finished_at = time.time()
            error_text = str(exc)
            logger.exception("后台 async task 执行失败: async_task_id=%s", async_task_id)
            self._commit_terminal_async_task(
                async_task_id=async_task_id,
                status="failed",
                finished_at=finished_at,
                output_event={"event": "failed", "error": error_text},
                summary=self._build_async_task_failure_summary(async_task),
                state_updates={"error": error_text},
            )
            raise
