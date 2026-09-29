"""Durable background-task manager with generic agent ownership semantics.

The low-level task machinery already has careful subprocess, notification and
thread-pool handling.  This facade makes that machinery a manager-owned
runtime service: callers bind a scope and an event sink, then use ``agent_*``
methods rather than embedding team/group-specific lifecycle logic in Runner.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
import subprocess
from typing import Any, Callable, Protocol

from juice_agents.core.runner.async_tasks.lifecycle import close_async_task
from juice_agents.core.runner.async_tasks.kinds import to_public_kind
from juice_agents.core.runner.async_tasks.registry import _AsyncTaskRuntime
from juice_agents.core.runner.async_tasks.store import read_async_task_output
from juice_agents.core.runner.workspace import RuntimeWorkspace

logger = logging.getLogger(__name__)


class TaskEventSink(Protocol):
    """Minimal notification boundary required by the task manager."""

    def enqueue(self, owner_agent_id: str, event: dict[str, Any]) -> Any:
        """Deliver an already-persisted task notification."""


class _DiscardTaskEvents:
    """Safe standalone default; persistence remains the notification source."""

    def enqueue(self, owner_agent_id: str, event: dict[str, Any]) -> None:
        del owner_agent_id, event


_ROLE_TO_INTERNAL_KIND = {
    "root": "agent_owner",
    "member": "agent_owner",
    "functional": "agent_owner",
    "persistent": "teammate",
    "manager": "group_manager",
    "worker": "group_worker",
}


class AsyncTaskManager(_AsyncTaskRuntime):
    """Own a scope's task state, workers, processes, cancellation and recovery.

    ``state_dir`` is optional for ephemeral callers.  When supplied, the
    manager persists to ``<state_dir>/registry.json`` and task output folders
    below it.  Its event sink is deliberately duck-typed so a Runner, gateway
    or test can consume notifications without becoming a dependency of this
    package.

    A private implementation supplies low-level process bookkeeping, but this
    manager instance owns the state and exposes only agent-named operations.
    Team/group collaboration therefore uses the same manager surface as every
    other RunnerConfig.
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
        state_callback: Callable[[list[dict[str, Any]], dict[str, dict[str, Any]]], None] | None = None,
    ) -> None:
        self._released_manager = False
        self._state_dir = None if state_dir is None else Path(state_dir).expanduser().resolve()
        super().__init__(
            event_queue=event_sink or _DiscardTaskEvents(),
            runtime_workspace=runtime_workspace,
            default_base_dir=default_base_dir,
            max_workers=max_workers,
            public_sync=state_callback,
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

    def register_agent(
        self,
        *,
        agent_name: str,
        agent_id: str,
        role: str = "member",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register a manager-owned agent runtime, not a static declaration."""

        self._ensure_open()
        normalized_role = str(role or "member").strip().lower() or "member"
        internal_kind = _ROLE_TO_INTERNAL_KIND.get(normalized_role, "agent_owner")
        state = super().register_agent(
            agent_name=str(agent_name or "").strip(),
            agent_id=str(agent_id or "").strip(),
            agent_kind=internal_kind,  # runtime validates only the value shape
            metadata={"role": normalized_role, **dict(metadata or {})},
        )
        return self._agent_state(state)

    def get_agent(self, agent_name: str) -> dict[str, Any] | None:
        state = super().get_agent(agent_name)
        return None if state is None else self._agent_state(state)

    def list_agents(self) -> list[dict[str, Any]]:
        with self._lock:
            states = [copy.deepcopy(value) for value in self._agents.values() if isinstance(value, dict)]
        return [self._agent_state(state) for state in states]

    def ensure_persistent_agent(
        self,
        agent_name: str,
        *,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create/reuse the single durable worker record for a member agent."""

        self._ensure_open()
        task = super().ensure_teammate_async_task(
            agent_name,
            description=description,
            metadata=dict(metadata or {}),
        )
        return self._task_state(task)

    def launch_persistent_agent_loop(
        self,
        agent_name: str,
        *,
        runner: Callable[[str, str, Path], Any],
    ) -> str:
        self._ensure_open()
        return super().launch_teammate_loop(agent_name, runner=runner)

    def acquire_agent_run(self, agent_name: str, *, run_id: str) -> bool:
        self._ensure_open()
        return super().acquire_agent_run(agent_name, run_id=run_id)

    def finish_agent_run(self, agent_name: str, *, run_id: str, idle: bool = True) -> bool:
        return super().finish_agent_run(agent_name, run_id=run_id, idle=idle)

    def request_agent_shutdown(self, agent_name: str) -> bool:
        return super().request_agent_shutdown(agent_name)

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
        """Launch a generic background task in the manager's worker pool."""

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

        def run_shell(_task: dict[str, Any], _output_dir: Path) -> dict[str, Any]:
            completed = subprocess.run(
                normalized_command,
                shell=True,
                cwd=working_dir,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            # Retain stderr and the return code rather than silently losing a
            # failed command's useful diagnostics.  The task substrate owns
            # output persistence and notification delivery for this payload.
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }

        return self.launch(
            task_type="local_bash",
            owner_agent_id=owner_agent_id,
            owner_agent_name=owner_agent_name,
            description=normalized_command,
            runner=run_shell,
            summary_builder=lambda result: (
                "background shell completed"
                if int(result.get("returncode", 1)) == 0
                else f"background shell exited with {result.get('returncode')}"
            ),
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

    def cancel(self, async_task_id: str, *, reason: str = "agent_requested") -> dict[str, Any] | None:
        """Persist a task's terminal cancellation before returning control."""

        self._ensure_open()
        stopped = super().stop_async_task(async_task_id, reason=reason)
        logger.info("async task cancellation requested: async_task_id=%s", async_task_id)
        return None if stopped is None else self.get_task(async_task_id)

    def stop(self, *, reason: str = "manager_released") -> list[dict[str, Any]]:
        self._ensure_open()
        super().stop_active_async_tasks(reason=reason)
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
            for task in self._async_tasks.values():
                if str(task.get("status") or "") not in {"pending", "running"}:
                    continue
                close_async_task(task, status="killed", reason=reason)
                self._store_async_task_locked(task)
                closed.append(copy.deepcopy(task))
            if closed:
                self._persist_locked()
        for task in closed:
            self._emit_notification(
                task,
                status="killed",
                summary=f"后台任务已因恢复停止：{task.get('description') or task.get('async_task_id')}",
                extra_payload={"closed_reason": reason},
            )
        replayed = self.requeue_pending_notifications()
        logger.info("async task manager recovered: closed=%d replayed=%d", len(closed), replayed)
        return [self._task_state(task) for task in closed]

    def release(self, *, wait: bool = True, timeout_seconds: float = 5.0) -> bool:
        """Stop active work, flush state and release manager-owned workers."""

        if self._released_manager:
            return True
        self.stop(reason="manager_released")
        self._released_manager = True
        drained = super().shutdown(wait=wait, timeout_seconds=timeout_seconds)
        logger.info("async task manager released: drained=%s", drained)
        return drained

    close = release

    def _ensure_open(self) -> None:
        if self._released_manager:
            raise RuntimeError("AsyncTaskManager 已释放")

    @staticmethod
    def _agent_state(raw: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(raw.get("metadata") or {})
        return {
            "agent_id": str(raw.get("agent_id") or ""),
            "agent_name": str(raw.get("agent_name") or ""),
            "role": str(metadata.get("role") or raw.get("agent_kind") or "member"),
            "status": str(raw.get("status") or ""),
            "current_async_task_id": str(raw.get("current_async_task_id") or ""),
            "last_error": str(raw.get("last_error") or ""),
            "metadata": metadata,
        }

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
        }


__all__ = ["AsyncTaskManager", "TaskEventSink"]
