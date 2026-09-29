"""Teammates mixin: teammate lifecycle, agent run management."""

from __future__ import annotations

import concurrent.futures
import copy
import logging
import time
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from .lifecycle import (
    close_async_task,
    get_agent_run_id,
    get_teammate_flush_index,
    get_teammate_shutdown_requested,
    set_agent_last_error,
    set_agent_run,
    set_agent_status,
    set_teammate_flush_index,
    set_teammate_shutdown,
    clear_agent_run,
)
from .internal_types import TeammateAsyncTaskState

if TYPE_CHECKING:
    from .internal_types import AsyncTaskState as InternalAsyncTaskState, RunnerAgentState

logger = logging.getLogger(__name__)

_ASYNC_TASK_ACTIVE_STATUSES = {"pending", "running"}


class _AsyncTaskTeammatesMixin:
    """Mixin providing teammate async task lifecycle methods."""

    def ensure_teammate_async_task(
        self,
        agent_name: str,
        *,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> TeammateAsyncTaskState:
        normalized_agent_name = str(agent_name or "").strip()
        if not normalized_agent_name:
            raise ValueError("agent_name 不能为空")
        with self._lock:
            agent = self._require_agent_locked(normalized_agent_name)
            current_async_task = self._current_agent_async_task_locked(agent)
            if (
                isinstance(current_async_task, dict)
                and str(current_async_task.get("kind") or "").strip() == "teammate"
                and str(current_async_task.get("status") or "").strip() in _ASYNC_TASK_ACTIVE_STATUSES
            ):
                merged_metadata = dict(current_async_task.get("metadata") or {})
                merged_metadata.update(dict(metadata or {}))
                merged_metadata.setdefault("agent_name", normalized_agent_name)
                merged_metadata.setdefault("run_id", "")
                merged_metadata.setdefault("shutdown_requested", False)
                merged_metadata.setdefault("last_flushed_message_index", 0)
                current_async_task["metadata"] = merged_metadata
                current_async_task["description"] = str(description or current_async_task.get("description") or "").strip()
                set_agent_status(agent, "active")
                set_agent_last_error(agent, "")
                self._store_async_task_locked(current_async_task)
                self._persist_locked()
                return copy.deepcopy(current_async_task)

            teammate_metadata = {
                "agent_name": normalized_agent_name,
                "run_id": "",
                "shutdown_requested": False,
                "last_flushed_message_index": 0,
            }
            teammate_metadata.update(dict(metadata or {}))
            async_task = self._build_async_task(
                kind="teammate",
                owner_agent_id=str(agent.get("agent_id") or ""),
                owner_agent_name=normalized_agent_name,
                description=description,
                extra_fields={"metadata": teammate_metadata},
                status="running",
                start_immediately=True,
            )
            agent["current_async_task_id"] = str(async_task.get("async_task_id") or "")
            set_agent_status(agent, "active")
            set_agent_last_error(agent, "")
            self._ensure_async_task_output_dir(str(async_task.get("output_dir") or ""))
            self._store_async_task_locked(async_task)
            self._persist_locked()
            return copy.deepcopy(async_task)

    def launch_teammate_loop(
        self,
        agent_name: str,
        *,
        runner: Callable[[str, str, Path], Any],
    ) -> str:
        """为 teammate 的长生命周期 loop 启动后台线程。"""

        with self._lock:
            agent = self._require_agent_locked(agent_name)
            current_async_task = self._current_agent_async_task_locked(agent)
            if not isinstance(current_async_task, dict) or str(current_async_task.get("kind") or "").strip() != "teammate":
                raise ValueError(f"agent {agent_name} 没有活动中的 teammate async task")
            async_task_id = str(current_async_task.get("async_task_id") or "").strip()
            future = self._futures.get(async_task_id)
            if future is not None and not future.done():
                return async_task_id
            output_dir = Path(str(current_async_task.get("output_dir") or ""))
            self._ensure_async_task_output_dir(output_dir)
            future = self._executor.submit(self._run_teammate_loop, agent_name, async_task_id, output_dir, runner)
            self._futures[async_task_id] = future
            return async_task_id

    def _run_teammate_loop(
        self,
        agent_name: str,
        async_task_id: str,
        output_dir: Path,
        runner: Callable[[str, str, Path], Any],
    ) -> Any:
        self._append_async_task_output(
            output_dir,
            {"async_task_id": async_task_id, "event": "teammate_loop_started", "created_at": time.time()},
        )
        try:
            result = runner(agent_name, async_task_id, output_dir)
            return result
        except Exception as exc:
            error_text = str(exc)
            logger.exception("teammate loop 执行失败: agent=%s async_task=%s", agent_name, async_task_id)
            with self._lock:
                agent = self._agents.get(agent_name)
                async_task = self._async_tasks.get(async_task_id)
                if isinstance(async_task, dict) and str(async_task.get("status") or "").strip() in _ASYNC_TASK_ACTIVE_STATUSES:
                    close_async_task(async_task, status="failed", reason="teammate_loop_failed", error=error_text)
                    self._store_async_task_locked(async_task)
                if isinstance(agent, dict):
                    set_agent_last_error(agent, error_text)
                    set_agent_status(agent, "shutdown")
                    if str(agent.get("current_async_task_id") or "").strip() == async_task_id:
                        agent["current_async_task_id"] = ""
                self._persist_locked()
            self._append_async_task_output(
                output_dir,
                {"async_task_id": async_task_id, "event": "failed", "created_at": time.time(), "error": error_text},
            )
            raise
        finally:
            with self._lock:
                agent = self._agents.get(agent_name)
                async_task = self._async_tasks.get(async_task_id)
                if isinstance(async_task, dict) and str(async_task.get("status") or "").strip() in _ASYNC_TASK_ACTIVE_STATUSES:
                    close_async_task(async_task, status="killed", reason="teammate_loop_stopped")
                    self._store_async_task_locked(async_task)
                if isinstance(agent, dict) and str(agent.get("current_async_task_id") or "").strip() == async_task_id:
                    agent["current_async_task_id"] = ""
                    set_agent_status(agent, "shutdown")
                self._persist_locked()
            self._append_async_task_output(
                output_dir,
                {"async_task_id": async_task_id, "event": "teammate_loop_stopped", "created_at": time.time()},
            )

    def wait_for_teammate_stop(self, agent_name: str, *, timeout_seconds: float) -> bool:
        with self._lock:
            agent = self._agents.get(str(agent_name or "").strip())
            async_task_id = "" if not isinstance(agent, dict) else str(agent.get("current_async_task_id") or "").strip()
            future = None if not async_task_id else self._futures.get(async_task_id)
        if future is None:
            return True
        try:
            future.result(timeout=max(0.0, float(timeout_seconds)))
            return True
        except concurrent.futures.TimeoutError:
            return False
        except Exception:
            return True

    def acquire_agent_run(self, agent_name: str, *, run_id: str) -> bool:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return False
        with self._lock:
            agent = self._require_agent_locked(agent_name)
            current_async_task = self._current_agent_async_task_locked(agent)
            if (
                not isinstance(current_async_task, dict)
                or str(current_async_task.get("kind") or "").strip() != "teammate"
                or str(current_async_task.get("status") or "").strip() not in _ASYNC_TASK_ACTIVE_STATUSES
            ):
                return False
            if get_teammate_shutdown_requested(current_async_task) or str(agent.get("status") or "").strip() in {
                "shutdown_requested",
                "shutdown",
            }:
                return False
            current_run_id = get_agent_run_id(current_async_task)
            if current_run_id and current_run_id != normalized_run_id:
                return False
            set_agent_run(current_async_task, normalized_run_id)
            set_agent_status(agent, "active")
            set_agent_last_error(agent, "")
            self._store_async_task_locked(current_async_task)
            self._persist_locked()
            return True

    def finish_agent_run(self, agent_name: str, *, run_id: str, idle: bool) -> bool:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return False
        with self._lock:
            agent = self._require_agent_locked(agent_name)
            current_async_task = self._current_agent_async_task_locked(agent)
            if (
                not isinstance(current_async_task, dict)
                or str(current_async_task.get("kind") or "").strip() != "teammate"
                or str(current_async_task.get("status") or "").strip() not in _ASYNC_TASK_ACTIVE_STATUSES
                or get_agent_run_id(current_async_task) != normalized_run_id
            ):
                return False
            clear_agent_run(current_async_task)
            set_agent_status(agent, "idle" if idle else "active")
            self._store_async_task_locked(current_async_task)
            self._persist_locked()
            return True

    def request_agent_shutdown(self, agent_name: str) -> bool:
        with self._lock:
            agent = self._require_agent_locked(agent_name)
            if str(agent.get("status") or "").strip() == "shutdown":
                return False
            current_async_task = self._current_agent_async_task_locked(agent)
            if isinstance(current_async_task, dict) and str(current_async_task.get("kind") or "").strip() == "teammate":
                set_teammate_shutdown(current_async_task, requested=True)
                self._store_async_task_locked(current_async_task)
            set_agent_status(agent, "shutdown_requested")
            self._persist_locked()
            return True

    def clear_agent_shutdown_request(self, agent_name: str) -> bool:
        with self._lock:
            agent = self._require_agent_locked(agent_name)
            current_async_task = self._current_agent_async_task_locked(agent)
            if not isinstance(current_async_task, dict) or str(current_async_task.get("kind") or "").strip() != "teammate":
                return False
            set_teammate_shutdown(current_async_task, requested=False)
            if str(agent.get("status") or "").strip() != "shutdown":
                set_agent_status(agent, "active")
            self._store_async_task_locked(current_async_task)
            self._persist_locked()
            return True

    def shutdown_agent(self, agent_name: str, *, reason: str) -> TeammateAsyncTaskState:
        with self._lock:
            agent = self._require_agent_locked(agent_name)
            current_async_task = self._current_agent_async_task_locked(agent)
            if (
                not isinstance(current_async_task, dict)
                or str(current_async_task.get("kind") or "").strip() != "teammate"
                or str(current_async_task.get("status") or "").strip() not in _ASYNC_TASK_ACTIVE_STATUSES
            ):
                raise ValueError(f"agent {agent_name} 没有活动中的 teammate")
            async_task_id = str(current_async_task.get("async_task_id") or "").strip()
            future = self._futures.get(async_task_id)
            if future is not None and not future.done():
                set_teammate_shutdown(current_async_task, requested=True)
                set_agent_status(agent, "shutdown_requested")
                self._store_async_task_locked(current_async_task)
                self._persist_locked()
                return copy.deepcopy(current_async_task)

            closed_async_task = close_async_task(current_async_task, status="killed", reason=str(reason or "").strip())
            set_agent_status(agent, "shutdown")
            agent["current_async_task_id"] = ""
            self._store_async_task_locked(closed_async_task)
            self._persist_locked()
            return copy.deepcopy(closed_async_task)

    def restart_teammate(self, agent_name: str, *, reason: str) -> TeammateAsyncTaskState:
        normalized_agent_name = str(agent_name or "").strip()
        with self._lock:
            agent = self._require_agent_locked(normalized_agent_name)
            previous_async_task = self._current_agent_async_task_locked(agent)
            previous_description = f"{normalized_agent_name} teammate session"
            previous_metadata: dict[str, Any] = {"agent_name": normalized_agent_name}
            if isinstance(previous_async_task, dict) and str(previous_async_task.get("kind") or "").strip() == "teammate":
                previous_description = str(previous_async_task.get("description") or previous_description).strip()
                previous_metadata.update(dict(previous_async_task.get("metadata") or {}))
                if str(previous_async_task.get("status") or "").strip() in _ASYNC_TASK_ACTIVE_STATUSES:
                    close_async_task(previous_async_task, status="killed", reason=str(reason or "").strip())
                    self._store_async_task_locked(previous_async_task)
            agent["current_async_task_id"] = ""
            set_agent_status(agent, "active")
            set_agent_last_error(agent, "")
            previous_metadata["run_id"] = ""
            previous_metadata["shutdown_requested"] = False
            previous_metadata.setdefault("last_flushed_message_index", 0)
            async_task = self._build_async_task(
                kind="teammate",
                owner_agent_id=str(agent.get("agent_id") or ""),
                owner_agent_name=normalized_agent_name,
                description=previous_description,
                extra_fields={"metadata": previous_metadata},
                status="running",
                start_immediately=True,
            )
            agent["current_async_task_id"] = str(async_task.get("async_task_id") or "")
            self._ensure_async_task_output_dir(str(async_task.get("output_dir") or ""))
            self._store_async_task_locked(async_task)
            self._persist_locked()
            return copy.deepcopy(async_task)

    def set_teammate_flush_index(self, agent_name: str, *, index: int) -> int:
        with self._lock:
            agent = self._require_agent_locked(agent_name)
            current_async_task = self._current_agent_async_task_locked(agent)
            if not isinstance(current_async_task, dict) or str(current_async_task.get("kind") or "").strip() != "teammate":
                raise ValueError(f"agent {agent_name} 没有可写入的 teammate")
            set_teammate_flush_index(current_async_task, index)
            self._store_async_task_locked(current_async_task)
            self._persist_locked()
            return get_teammate_flush_index(current_async_task)
