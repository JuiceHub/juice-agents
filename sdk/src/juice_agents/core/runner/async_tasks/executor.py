"""Executor mixin: _arun_async_task and failure summary builder."""

from __future__ import annotations

import copy
import asyncio
import inspect

from ..execution.async_io import blocking, cancel_and_drain
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
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
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

            # Commit terminal state and the exact notification together. Deliver
            # only after releasing the lock: inbox callbacks may acknowledge it.
            event = self._prepare_notification_locked(
                async_task,
                status=status,
                summary=summary,
                extra_payload=extra_payload,
            )
            self._persist_locked()
        self._deliver_notification(async_task, event)
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
        if public_type == "local_workflow":
            workflow_name = str(metadata.get("workflow_name") or "").strip()
            if workflow_name:
                return f"local_workflow 任务失败：{workflow_name}"
            return "local_workflow 任务执行失败"
        return f"{public_type} 任务执行失败"

    async def _arun_async_task(
        self,
        async_task_id: str,
        runner: Callable[[InternalAsyncTaskState, Path], Any],
        summary_builder: Callable[[Any], str],
    ) -> Any:
        def prepare():
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
            return async_task, output_dir
        prepared = await blocking(prepare)
        if prepared is None:
            return None
        async_task, output_dir = prepared
        cancel_event = self.cancel_event_for(async_task_id)
        try:
            async def request():
                if inspect.iscoroutinefunction(runner):
                    return await runner(copy.deepcopy(async_task), output_dir)
                result = await blocking(runner, copy.deepcopy(async_task), output_dir,
                                        on_cancel=cancel_event.set)
                return await result if inspect.isawaitable(result) else result

            operation = asyncio.create_task(request())
            try:
                while not operation.done() and not cancel_event.is_set():
                    await asyncio.wait((operation,), timeout=0.02)
                if cancel_event.is_set():
                    await cancel_and_drain(operation)
                result = await asyncio.shield(operation)
            finally:
                if not operation.done():
                    await cancel_and_drain(operation)
                    await asyncio.gather(operation, return_exceptions=True)
            finished_at = time.time()
            jsonable_result = _coerce_jsonable(result)
            await blocking(self._commit_terminal_async_task,
                async_task_id=async_task_id,
                status="completed",
                finished_at=finished_at,
                output_event={"event": "completed", "result": jsonable_result},
                summary=await blocking(summary_builder, result),
                state_updates={"closed_reason": "", "error": ""},
                extra_payload={"result": jsonable_result},
            )
            return result
        except asyncio.CancelledError:
            cancel_event.set()
            return None
        except Exception as exc:
            finished_at = time.time()
            error_text = str(exc)
            logger.exception("后台 async task 执行失败: async_task_id=%s", async_task_id)
            await blocking(self._commit_terminal_async_task,
                async_task_id=async_task_id,
                status="failed",
                finished_at=finished_at,
                output_event={"event": "failed", "error": error_text},
                summary=self._build_async_task_failure_summary(async_task),
                state_updates={"error": error_text},
            )
            raise
