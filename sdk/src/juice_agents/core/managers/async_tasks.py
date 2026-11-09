"""Durable background-task manager with generic agent ownership semantics.

The low-level task machinery already has careful subprocess, notification and
coroutine lifecycle handling.  This facade makes that machinery a manager-owned
runtime service: callers bind a scope and a durable notification sink. Agent
lifecycle and round scheduling belong exclusively to AgentManager.
"""

from __future__ import annotations

import copy
import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Protocol

from juice_agents.core.runner.async_tasks.lifecycle import close_async_task
from juice_agents.core.runner.async_tasks.kinds import to_public_kind
from juice_agents.core.runner.async_tasks.registry import _AsyncTaskRuntime
from juice_agents.core.runner.async_tasks.store import read_async_task_output
from juice_agents.core.runner.workspace import RuntimeWorkspace

from juice_agents.core.runner.execution.async_io import blocking

logger = logging.getLogger(__name__)


class TaskEventSink(Protocol):
    """Minimal notification boundary required by the task manager."""

    def enqueue(self, owner_agent_id: str, event: dict[str, Any]) -> Any:
        """Deliver an already-persisted task notification."""


class _DiscardTaskEvents:
    """Safe standalone default; persistence remains the notification source."""

    def enqueue(self, owner_agent_id: str, event: dict[str, Any]) -> None:
        del owner_agent_id, event


class AsyncTaskManager(_AsyncTaskRuntime):
    """Own a scope's task state, workers, processes, cancellation and recovery.

    ``state_dir`` is optional for ephemeral callers.  When supplied, the
    manager persists to ``<state_dir>/registry.json`` and task output folders
    below it.  Its event sink is deliberately duck-typed so a Runner, gateway
    or test can consume notifications without becoming a dependency of this
    package.

    A private implementation supplies low-level process bookkeeping, but this
    manager instance owns task state only. Agent identities are immutable owner
    references on tasks, never a second live-Agent or round registry.
    """

    def __init__(
        self,
        *,
        event_sink: TaskEventSink | None = None,
        state_dir: str | Path | None = None,
        scope_id: str = "standalone",
        scope_kind: str = "agent",
        scope_meta: dict[str, Any] | None = None,
        runtime_workspace: RuntimeWorkspace | None = None,
        default_base_dir: str | Path | None = None,
        max_workers: int = 8,
    ) -> None:
        self._released_manager = False
        self._state_dir = None if state_dir is None else Path(state_dir).expanduser().resolve()
        super().__init__(
            event_queue=event_sink or _DiscardTaskEvents(),
            runtime_workspace=runtime_workspace,
            default_base_dir=default_base_dir,
            max_workers=max_workers,
            outputs_dir=None if self._state_dir is None else self._state_dir / "tasks",
        )
        if self._state_dir is not None:
            self.bind_scope(
                store_path=self._state_dir / "registry.json",
                scope_id=scope_id,
                scope_kind=scope_kind,
                scope_meta=scope_meta,
            )

    @property
    def state_dir(self) -> Path | None:
        return self._state_dir

    @property
    def state_path(self) -> Path | None:
        return None if not self.store_path else Path(self.store_path)

    def bind(
        self,
        *,
        state_dir: str | Path,
        scope_id: str,
        scope_kind: str = "agent",
        scope_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bind or restore one durable manager scope."""

        self._ensure_open()
        self._state_dir = Path(state_dir).expanduser().resolve()
        snapshot = self.bind_scope(
            store_path=self._state_dir / "registry.json",
            scope_id=scope_id,
            scope_kind=scope_kind,
            scope_meta=scope_meta,
        )
        logger.info("async task manager bound: scope_id=%s scope_kind=%s", scope_id, scope_kind)
        return dict(snapshot)

    def launch(
        self,
        *,
        task_type: str,
        owner_agent_id: str,
        owner_agent_name: str,
        description: str,
        runner: Callable[[dict[str, Any], Path], Any],
        summary_builder: Callable[[Any], str],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Launch a generic background task under the manager's concurrency limit."""

        self._ensure_open()
        return super().launch_async_task(
            async_task_type=task_type,  # public task names are validated by the substrate
            owner_agent_id=owner_agent_id,
            owner_agent_name=owner_agent_name,
            description=description,
            extra_fields={"metadata": dict(metadata or {})},
            runner=runner,
            summary_builder=summary_builder,
        )

    def launch_shell(
        self,
        *,
        owner_agent_id: str,
        owner_agent_name: str,
        command: str,
        cwd: str | Path,
        timeout_seconds: float | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one shell process through the manager-owned task pool.

        The Runner may decide that a capability is available, but it must not
        create subprocesses itself.  Keeping process construction beside task
        state gives cancellation, audit and recovery one owner.
        """

        normalized_command = str(command or "").strip()
        if not normalized_command:
            raise ValueError("后台 shell command 不能为空")
        working_dir = Path(cwd).expanduser().resolve()

        return super().launch_local_bash_async_task(
            owner_agent_id=owner_agent_id, owner_agent_name=owner_agent_name,
            command=normalized_command, cwd=str(working_dir), timeout_seconds=timeout_seconds,
            metadata=dict(metadata or {}),
        )

    def list_tasks(
        self,
        *,
        owner_agent_id: str | None = None,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            self._task_state(item)
            for item in super().list_async_tasks(owner_agent_id=owner_agent_id, statuses=statuses)
        ]

    def get_task(self, async_task_id: str) -> dict[str, Any] | None:
        state = super().get_async_task(async_task_id)
        return None if state is None else self._task_state(state)

    def read_output(self, async_task_id: str) -> dict[str, Any]:
        """Read one task's durable output through its owning Manager.

        Transports must not know the private task-store layout.  The manager
        first validates that the requested identifier belongs to this scope,
        then delegates the file format details to its private substrate.
        """

        task = self.get_task(async_task_id)
        if task is None:
            raise ValueError(f"async_task_id 不存在: {async_task_id}")
        return read_async_task_output(str(task.get("output_dir") or ""))

    def cancel(self, async_task_id: str, *, reason: str = "agent_requested",
               wait_timeout_seconds: float = 1.0) -> dict[str, Any] | None:
        """Persist a task's terminal cancellation before returning control."""

        self._ensure_open()
        stopped = super().stop_async_task(async_task_id, reason=reason, wait_timeout_seconds=wait_timeout_seconds)
        logger.info("async task cancellation requested: async_task_id=%s", async_task_id)
        return None if stopped is None else self.get_task(async_task_id)

    def stop(self, *, reason: str = "manager_released", wait_timeout_seconds: float = 1.0) -> list[dict[str, Any]]:
        self._ensure_open()
        super().stop_active_async_tasks(reason=reason, wait_timeout_seconds=wait_timeout_seconds)
        return self.list_tasks()

    def recover(self, *, reason: str = "stale_on_recovery") -> list[dict[str, Any]]:
        """Close work that belonged to a process which no longer exists.

        Python threads/process handles cannot be resumed across process
        boundaries.  The durable contract therefore converges active records
        to ``killed`` and replays their notifications rather than pretending a
        callback can be safely restarted from an arbitrary instruction.
        """

        self._ensure_open()
        closed: list[dict[str, Any]] = []
        with self._lock:
            for task in list(self._async_tasks.values()):
                if str(task.get("status") or "") not in {"pending", "running"}:
                    continue
                close_async_task(task, status="killed", reason=reason)
                self._prepare_notification_locked(
                    task, status="killed",
                    summary=f"后台任务已因恢复停止：{task.get('description') or task.get('async_task_id')}",
                    extra_payload={"closed_reason": reason},
                )
                closed.append(copy.deepcopy(task))
            if closed:
                self._persist_locked()
        replayed = self.requeue_pending_notifications()
        logger.info("async task manager recovered: closed=%d replayed=%d", len(closed), replayed)
        return [self._task_state(task) for task in closed]

    def release(self, *, wait: bool = True, timeout_seconds: float | None = 5.0) -> bool:
        """Stop active work, flush state and release manager-owned workers."""

        if not self._released_manager:
            self.stop(reason="manager_released")
            self._released_manager = True
        drained = super().shutdown(wait=wait, timeout_seconds=timeout_seconds)
        logger.info("async task manager released: drained=%s", drained)
        return drained

    close = release

    async def alaunch(self, **kwargs: Any) -> dict[str, Any]:
        return await blocking(self.launch, **kwargs)

    async def alaunch_shell(self, **kwargs: Any) -> dict[str, Any]:
        return await blocking(self.launch_shell, **kwargs)

    async def acancel(self, async_task_id: str, **kwargs: Any) -> dict[str, Any] | None:
        await blocking(self.cancel, async_task_id, wait_timeout_seconds=0, **kwargs)
        future = self._futures.get(async_task_id)
        if future is not None:
            await self._await_workers([future], None)
        return self.get_task(async_task_id)

    async def await_active(self, **kwargs: Any) -> list[dict[str, Any]]:
        owner = kwargs.pop("owner_agent_id", None)
        timeout = kwargs.pop("timeout_seconds", 30.0)
        if kwargs:
            raise TypeError(f"未知等待参数: {', '.join(kwargs)}")
        with self._lock:
            pending = [future for task_id, future in self._futures.items()
                       if not future.done() and (owner is None or
                           self._async_tasks[task_id].get("owner_agent_id") == owner)]
        await self._await_workers(pending, timeout)
        return self.public_active_async_tasks(owner_agent_id=owner)

    async def aclose(self, *, wait: bool = True, timeout_seconds: float | None = 5.0) -> bool:
        if not self._released_manager:
            await blocking(self.stop, reason="manager_released", wait_timeout_seconds=0)
            self._released_manager = True
        with self._lock:
            pending = [future for future in self._futures.values() if not future.done()]
        drained = await self._await_workers(pending, timeout_seconds) if wait else not pending
        self._shutdown_complete = drained
        logger.info("async task manager released: drained=%s", drained)
        return drained

    @staticmethod
    async def _await_workers(futures, timeout: float | None) -> bool:
        """等待完成通知不占线程；重复取消也不会绕过已经开始的收尾。"""
        if not futures:
            return True
        wrapped = [asyncio.wrap_future(future) for future in futures]
        for future in wrapped:
            future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        waiter = asyncio.create_task(asyncio.wait(wrapped, timeout=timeout))
        cancelled = False
        while True:
            try:
                _, pending = await asyncio.shield(waiter)
                break
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return not pending

    def _ensure_open(self) -> None:
        if self._released_manager:
            raise RuntimeError("AsyncTaskManager 已释放")

    @staticmethod
    def _task_state(raw: dict[str, Any]) -> dict[str, Any]:
        """Project the private runtime record into the manager contract."""

        return {
            "async_task_id": str(raw.get("async_task_id") or ""),
            "type": to_public_kind(raw.get("kind")),
            "status": str(raw.get("status") or ""),
            "owner_agent_id": str(raw.get("owner_agent_id") or ""),
            "owner_agent_name": str(raw.get("owner_agent_name") or ""),
            "description": str(raw.get("description") or ""),
            "output_dir": str(raw.get("output_dir") or ""),
            "created_at": raw.get("created_at"),
            "started_at": raw.get("started_at"),
            "finished_at": raw.get("finished_at"),
            "closed_reason": str(raw.get("closed_reason") or ""),
            "error": str(raw.get("error") or ""),
            "metadata": dict(raw.get("metadata") or {}),
            "notification": copy.deepcopy(raw.get("notification")),
        }


__all__ = ["AsyncTaskManager", "TaskEventSink"]
