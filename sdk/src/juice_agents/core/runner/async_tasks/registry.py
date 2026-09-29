"""Runner 原生 async task registry、scope store 与 agent lifecycle。"""

from __future__ import annotations

import atexit
import concurrent.futures
import copy
import json
import logging
import subprocess
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

# `concurrent.futures` exposes ThreadPoolExecutor lazily.  Resolve the public
# class before registering our shutdown hook, which also loads
# ``concurrent.futures.thread`` and registers its ``_python_exit`` hook first.
# ``threading._shutdown`` invokes hooks in reverse order, so our hook can set
# the cooperative stop event before futures tries to join non-daemon workers.
from concurrent.futures import ThreadPoolExecutor

from juice_agents.core.runner.workspace import RuntimeWorkspace
from juice_agents.core.utils import sanitize_path_component

from .kinds import to_public_kind
from .store import (
    build_scoped_async_task_output_dir,
    load_async_tasks_store,
    write_async_tasks_store,
)
from ..execution.event_queue import RunnerEventQueue
from .internal_types import (
    AsyncTaskKind,
    AsyncTaskState as InternalAsyncTaskState,
    RunnerAgentKind,
    RunnerAgentState,
    AsyncTaskRuntimeState,
    TeammateAsyncTaskState,
)
from .lifecycle import (
    build_agent_state,
    close_async_task,
    set_agent_status,
    set_teammate_shutdown,
)
from ..types.definitions import AsyncTaskState

from .executor import _AsyncTaskExecutorMixin
from .teammates import _AsyncTaskTeammatesMixin
from .launchers import _AsyncTaskLaunchersMixin

logger = logging.getLogger(__name__)

_ASYNC_TASK_ACTIVE_STATUSES = {"pending", "running"}

# 所有存活的 registry 弱引用。调用方漏掉 stop() 时（脚本直接丢弃 runner、测试跑完不收尾），
# 靠下面的 hook 兜底回收线程池。
_LIVE_RUNTIMES: weakref.WeakSet = weakref.WeakSet()


def _shutdown_live_runtimes() -> None:
    for runtime in list(_LIVE_RUNTIMES):
        try:
            runtime.shutdown(wait=True, timeout_seconds=2.0)
        except Exception:  # 停机阶段不能抛，否则盖掉真正的退出路径
            logger.debug("退出时回收线程池失败", exc_info=True)


# 必须走 threading._register_atexit，不能用 atexit.register：
# concurrent.futures.thread 正是用前者注册 `_python_exit`（它要 join 非 daemon worker），
# 而 threading 的这批回调整体早于普通 atexit 回调执行。用 atexit.register 的话，
# `_python_exit` 会先 join 住不肯退出的 worker，我们的回收永远等不到执行 —— 进程永久挂死。
#
# 同一批内是后进先出：本模块 import 时 concurrent.futures 已经注册完，
# 所以这里注册的 hook 先于 `_python_exit` 运行，正是需要的顺序。
if hasattr(threading, "_register_atexit"):
    threading._register_atexit(_shutdown_live_runtimes)
else:  # 理论上不会走到：CPython 3.9+ 都有这个私有接口
    atexit.register(_shutdown_live_runtimes)


def _to_public_async_task(async_task: dict[str, Any]) -> AsyncTaskState:
    async_task_id = str(async_task.get("async_task_id") or "").strip()
    return {
        "async_task_id": async_task_id,
        "type": to_public_kind(async_task.get("kind")),
        "status": str(async_task.get("status") or "").strip(),
        "owner_agent_id": str(async_task.get("owner_agent_id") or "").strip(),
        "owner_agent_name": str(async_task.get("owner_agent_name") or "").strip(),
        "description": str(async_task.get("description") or "").strip(),
        "output_dir": str(async_task.get("output_dir") or "").strip(),
        "created_at": float(async_task.get("created_at") or 0.0),
        "started_at": async_task.get("started_at"),
        "finished_at": async_task.get("finished_at"),
        "closed_reason": str(async_task.get("closed_reason") or "").strip(),
        "error": str(async_task.get("error") or "").strip(),
        "metadata": dict(async_task.get("metadata") or {}),
    }


class _AsyncTaskRuntime(
    _AsyncTaskExecutorMixin,
    _AsyncTaskTeammatesMixin,
    _AsyncTaskLaunchersMixin,
):
    """单个 Runner 或 Team scope 内的后台异步任务注册表。"""

    def __init__(
        self,
        *,
        event_queue: RunnerEventQueue,
        runtime_workspace: RuntimeWorkspace | None = None,
        default_base_dir: str | Path | None = None,
        max_workers: int = 8,
        public_sync: Callable[[list[AsyncTaskState], dict[str, dict[str, Any]]], None] | None = None,
        outputs_dir: str | Path | None = None,
    ) -> None:
        self.event_queue = event_queue
        self.runtime_workspace = runtime_workspace or RuntimeWorkspace(workspace_dir=None)
        self.default_base_dir = Path(default_base_dir or Path.cwd()).resolve()
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_workers))
        self._shutdown_complete = False
        # 进程级停机信号。长生命周期的 worker（teammate loop）必须在轮询时检查它：
        # 这是「不依赖磁盘状态、不依赖调用方记得调 stop()」的唯一可靠退出条件。
        self._shutdown_event = threading.Event()
        _LIVE_RUNTIMES.add(self)
        self._async_tasks: dict[str, InternalAsyncTaskState] = {}
        self._agents: dict[str, RunnerAgentState] = {}
        self._futures: dict[str, concurrent.futures.Future[Any]] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()
        self._store_path: Path | None = None
        self._scope_id = ""
        self._scope_kind = "agent"
        self._scope_status = "running"
        self._scope_meta: dict[str, Any] = {}
        self._public_sync = public_sync
        self._outputs_dir = None if outputs_dir is None else Path(outputs_dir).resolve()

    @property
    def store_path(self) -> str:
        return "" if self._store_path is None else str(self._store_path)

    def _next_async_task_id(self, kind: AsyncTaskKind, owner_agent_name: str) -> str:
        safe_owner = sanitize_path_component(owner_agent_name, fallback="agent")
        if kind == "teammate":
            count = sum(
                1
                for item in self._async_tasks.values()
                if isinstance(item, dict)
                and str(item.get("kind") or "").strip() == "teammate"
                and str((item.get("metadata") or {}).get("agent_name") or "").strip() == owner_agent_name
            )
            return f"teammate_{safe_owner}_{count + 1:04d}"
        safe_kind = sanitize_path_component(kind, fallback="task")
        return f"{safe_kind}_{safe_owner}_{uuid4().hex[:12]}"

    def _allocate_output_dir(self, owner_agent_id: str, owner_agent_name: str, async_task_id: str) -> Path:
        if self._outputs_dir is not None:
            task_dir = self._outputs_dir / sanitize_path_component(async_task_id, fallback="task")
            task_dir.mkdir(parents=True, exist_ok=True)
            return task_dir
        if self._store_path is not None:
            return build_scoped_async_task_output_dir(self._store_path, async_task_id)
        safe_agent_id = sanitize_path_component(owner_agent_id.replace(":", "_"), fallback="agent")
        output_dir = self.runtime_workspace.runtime_async_tasks_dir(
            safe_agent_id,
            default_base_dir=self.default_base_dir,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        task_dir = output_dir / sanitize_path_component(async_task_id, fallback="task")
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    def _snapshot_store_locked(self) -> AsyncTaskRuntimeState:
        return {
            "scope_id": self._scope_id,
            "scope_kind": self._scope_kind,
            "scope_status": self._scope_status,
            "scope_meta": copy.deepcopy(self._scope_meta),
            "agents": copy.deepcopy(self._agents),
            "async_tasks": [copy.deepcopy(item) for item in self._async_tasks.values()],
        }

    def _persist_locked(self) -> AsyncTaskRuntimeState | None:
        payload: AsyncTaskRuntimeState | None = None
        if self._store_path is not None:
            payload = self._snapshot_store_locked()
            write_async_tasks_store(self._store_path, payload)
        if self._public_sync is not None:
            self._public_sync(
                [_to_public_async_task(item) for item in self._async_tasks.values()],
                copy.deepcopy(self._agents),
            )
        return payload

    def _merge_async_task_fields(
        self,
        *,
        async_task: InternalAsyncTaskState,
        extra_fields: dict[str, Any] | None,
    ) -> InternalAsyncTaskState:
        normalized = dict(async_task)
        if not extra_fields:
            return normalized
        metadata = dict(normalized.get("metadata") or {})
        incoming = dict(extra_fields)
        incoming_metadata = incoming.pop("metadata", None)
        if isinstance(incoming_metadata, dict):
            metadata.update(incoming_metadata)
        metadata.update(incoming)
        normalized["metadata"] = metadata
        return normalized

    def _store_async_task_locked(self, async_task: InternalAsyncTaskState) -> InternalAsyncTaskState:
        normalized = copy.deepcopy(dict(async_task))
        async_task_id = str(normalized.get("async_task_id") or "").strip()
        normalized["async_task_id"] = async_task_id
        self._async_tasks[async_task_id] = normalized
        return normalized

    def _ensure_async_task_output_dir(self, output_dir: str | Path) -> None:
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        events_path = path / "events.json"
        if not events_path.exists():
            events_path.write_text("[]\n", encoding="utf-8")
        for filename in ("stdout.log", "stderr.log"):
            (path / filename).touch(exist_ok=True)

    def _write_stream_log(self, output_dir: Path, filename: str, content: str) -> None:
        log_path = output_dir / filename
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(str(content or ""), encoding="utf-8")

    def _require_agent_locked(self, agent_name: str) -> RunnerAgentState:
        normalized_agent_name = str(agent_name or "").strip()
        agent = self._agents.get(normalized_agent_name)
        if not normalized_agent_name or agent is None:
            raise ValueError(f"未注册 agent: {agent_name}")
        return agent

    def _current_agent_async_task_locked(self, agent: RunnerAgentState) -> InternalAsyncTaskState | None:
        current_async_task_id = str(agent.get("current_async_task_id") or "").strip()
        if not current_async_task_id:
            return None
        async_task = self._async_tasks.get(current_async_task_id)
        return async_task if isinstance(async_task, dict) else None

    def _build_async_task(
        self,
        *,
        kind: AsyncTaskKind,
        owner_agent_id: str,
        owner_agent_name: str,
        description: str,
        extra_fields: dict[str, Any] | None = None,
        status: str = "pending",
        start_immediately: bool = False,
    ) -> InternalAsyncTaskState:
        now = time.time()
        async_task_id = self._next_async_task_id(kind, owner_agent_name)
        async_task: InternalAsyncTaskState = {
            "async_task_id": async_task_id,
            "kind": kind,
            "owner_agent_id": str(owner_agent_id or "").strip(),
            "owner_agent_name": str(owner_agent_name or "").strip(),
            "status": str(status or "pending").strip(),
            "description": str(description or "").strip(),
            "output_dir": str(self._allocate_output_dir(owner_agent_id, owner_agent_name, async_task_id)),
            "created_at": now,
            "started_at": now if start_immediately else None,
            "finished_at": None,
            "closed_reason": "",
            "error": "",
            "metadata": {},
            "notification": None,
        }
        return self._merge_async_task_fields(async_task=async_task, extra_fields=extra_fields)

    def bind_scope(
        self,
        *,
        store_path: str | Path,
        scope_id: str,
        scope_kind: str,
        scope_meta: dict[str, Any] | None = None,
    ) -> AsyncTaskRuntimeState:
        bound_store_path = Path(store_path).resolve()
        loaded = load_async_tasks_store(
            bound_store_path,
            scope_id=str(scope_id or "").strip(),
            scope_kind=scope_kind,
            scope_meta=scope_meta,
        )
        with self._lock:
            persisted_async_tasks = {
                str(item.get("async_task_id") or "").strip(): copy.deepcopy(item)
                for item in list(loaded.get("async_tasks") or [])
                if isinstance(item, dict) and str(item.get("async_task_id") or "").strip()
            }
            persisted_async_tasks.update(copy.deepcopy(self._async_tasks))
            persisted_agents = {
                str(agent_name): copy.deepcopy(agent)
                for agent_name, agent in dict(loaded.get("agents") or {}).items()
                if str(agent_name or "").strip()
            }
            persisted_agents.update(copy.deepcopy(self._agents))
            self._store_path = bound_store_path
            self._scope_id = str(loaded.get("scope_id") or scope_id).strip()
            self._scope_kind = str(loaded.get("scope_kind") or scope_kind).strip() or "agent"
            self._scope_status = str(loaded.get("scope_status") or "running").strip() or "running"
            self._scope_meta = dict(loaded.get("scope_meta") or {})
            self._async_tasks = persisted_async_tasks
            self._agents = persisted_agents
            self._persist_locked()
            snapshot = self._snapshot_store_locked()
        self.requeue_pending_notifications()
        return snapshot

    def reset_scope_state(self) -> AsyncTaskRuntimeState:
        """Discard persisted agents/tasks after an incompatible protocol change."""

        with self._lock:
            self._async_tasks = {}
            self._agents = {}
            self._futures = {}
            self._processes = {}
            self._scope_status = "running"
            self._persist_locked()
            return self._snapshot_store_locked()

    def get_async_task(self, async_task_id: str) -> InternalAsyncTaskState | None:
        with self._lock:
            item = self._async_tasks.get(str(async_task_id or "").strip())
            return copy.deepcopy(item) if isinstance(item, dict) else None

    def get_agent(self, agent_name: str) -> RunnerAgentState | None:
        with self._lock:
            agent = self._agents.get(str(agent_name or "").strip())
            return copy.deepcopy(agent) if isinstance(agent, dict) else None

    def list_async_tasks(
        self,
        *,
        owner_agent_id: str | None = None,
        statuses: set[str] | None = None,
    ) -> list[InternalAsyncTaskState]:
        normalized_owner = str(owner_agent_id or "").strip()
        normalized_statuses = {
            str(status or "").strip()
            for status in set(statuses or set())
            if str(status or "").strip()
        }
        with self._lock:
            snapshot = [copy.deepcopy(item) for item in self._async_tasks.values() if isinstance(item, dict)]
        result: list[InternalAsyncTaskState] = []
        for item in snapshot:
            if normalized_owner and str(item.get("owner_agent_id") or "").strip() != normalized_owner:
                continue
            if normalized_statuses and str(item.get("status") or "").strip() not in normalized_statuses:
                continue
            result.append(item)
        return result

    def list_active_async_tasks(self, owner_agent_id: str | None = None) -> list[InternalAsyncTaskState]:
        return self.list_async_tasks(owner_agent_id=owner_agent_id, statuses=_ASYNC_TASK_ACTIVE_STATUSES)

    def public_async_tasks(self) -> list[AsyncTaskState]:
        return [_to_public_async_task(item) for item in self.list_async_tasks()]

    def public_active_async_tasks(self, owner_agent_id: str | None = None) -> list[AsyncTaskState]:
        return [_to_public_async_task(item) for item in self.list_active_async_tasks(owner_agent_id=owner_agent_id)]

    def has_pending_notifications(self, owner_agent_id: str | None = None) -> bool:
        """Return whether an undelivered completion notification still exists.

        The async manifest is the source of truth.  The in-memory event queue is
        deliberately only a wake-up mechanism and may be empty while an event is
        being projected into a persisted attachment.
        """

        normalized_owner = str(owner_agent_id or "").strip()
        with self._lock:
            for task in self._async_tasks.values():
                if normalized_owner and str(task.get("owner_agent_id") or "").strip() != normalized_owner:
                    continue
                notification = task.get("notification")
                if (
                    isinstance(notification, dict)
                    and str(notification.get("state") or "").strip() == "pending"
                ):
                    return True
        return False

    def is_shutting_down(self) -> bool:
        """线程池是否已进入停机。长生命周期的轮询 worker 必须据此退出循环。"""

        return self._shutdown_event.is_set()

    def is_async_task_cancelled(self, async_task_id: str) -> bool:
        """Return whether an async task was externally closed before its runner finished."""

        with self._lock:
            current = self._async_tasks.get(str(async_task_id or "").strip())
            return not isinstance(current, dict) or str(current.get("status") or "").strip() == "killed"

    def wait_active_async_tasks(
        self, *, owner_agent_id: str | None = None, timeout_seconds: float = 30.0
    ) -> list[AsyncTaskState]:
        """
        等待当前活跃的后台任务自然完成（不主动 kill），供同步 run() 收尾使用。

        非流式 run() 语义下，调用方拿到结果后通常会立即释放工作目录等资源；若后台
        worker 线程仍在写文件会触发竞态（如 tempdir 清理时 Directory not empty）。
        这里收集仍在运行的 future 并 join，让后台任务有机会安全收尾。超时返回，不抛错，
        以免单个卡死任务拖垮整个调用。返回等待结束时仍处于活跃状态的任务快照。
        """

        with self._lock:
            futures_to_wait = [
                future
                for async_task_id, future in self._futures.items()
                if not future.done()
                and (
                    owner_agent_id is None
                    or str(
                        self._async_tasks.get(async_task_id, {}).get("owner_agent_id") or ""
                    ).strip()
                    == str(owner_agent_id).strip()
                )
            ]

        if futures_to_wait:
            concurrent.futures.wait(
                futures_to_wait, timeout=max(0.0, float(timeout_seconds))
            )

        return self.public_active_async_tasks(owner_agent_id=owner_agent_id)

    def shutdown(self, *, wait: bool = True, timeout_seconds: float = 5.0) -> bool:
        """释放线程池。返回 True 表示所有 worker 已退出。

        ThreadPoolExecutor 的 worker 是非 daemon 线程，且 concurrent.futures 注册了
        `_python_exit` atexit hook 去 join 它们。所以只要还有 worker 没退出，解释器就会
        永久卡在退出阶段 —— 进程既不报错也不退出。teammate loop 是 `while True`，
        正是这种「不会自己结束」的 worker，必须显式回收。

        幂等：重复调用安全，第二次直接返回上次的结果。
        """

        with self._lock:
            if self._shutdown_complete:
                return True
            pending = [future for future in self._futures.values() if not future.done()]

        # 先发停机信号：轮询型 worker（teammate loop）靠它退出。只 cancel future 是不够的，
        # cancel 对「已经在跑」的任务无效。
        self._shutdown_event.set()

        for future in pending:
            future.cancel()

        # shutdown(wait=False) 只关闭队列、不 join，由下面的 join 控制超时，
        # 避免在这里被一个不肯退出的 worker 无限期挂住。
        self._executor.shutdown(wait=False)

        # 以「worker 线程是否真的退出」为准，而不是只看 self._futures：
        # 阻塞解释器退出的是线程本身，而 _futures 只记录了走 registry 提交的那部分任务。
        drained = True
        if wait:
            deadline_left = max(0.0, float(timeout_seconds))
            for thread in list(getattr(self._executor, "_threads", ()) or ()):
                if not thread.is_alive():
                    continue
                thread.join(timeout=deadline_left)
                if thread.is_alive():
                    drained = False
                    break
            if not drained:
                logger.warning(
                    "线程池关闭超时，仍有 worker 未退出：scope_id=%s。"
                    "解释器退出时可能被 atexit join 阻塞。",
                    self._scope_id or "-",
                )

        with self._lock:
            # 只有真正收干净才记为已完成，否则下次调用还要再试一遍。
            self._shutdown_complete = drained
        return drained

    def stop_active_async_tasks(self, *, reason: str, wait_timeout_seconds: float = 1.0) -> list[AsyncTaskState]:
        """
        Close active async tasks before a runner mode/session reconfiguration.

        Python cannot safely kill an arbitrary running thread, so this method enforces
        the store contract immediately and asks supported runtimes to stop cooperatively:
        shell subprocesses are terminated, teammate loops receive shutdown_requested,
        and generic worker futures are cancelled when still pending. `_run_async_task`
        checks the killed state before committing any late result.
        """

        normalized_reason = str(reason or "runner_stopped").strip() or "runner_stopped"
        stopped: list[InternalAsyncTaskState] = []
        processes: list[subprocess.Popen[str]] = []
        futures_to_wait: list[concurrent.futures.Future[Any]] = []
        with self._lock:
            for async_task_id, async_task in list(self._async_tasks.items()):
                if str(async_task.get("status") or "").strip() not in _ASYNC_TASK_ACTIVE_STATUSES:
                    continue
                future = self._futures.get(async_task_id)
                if future is not None and not future.done():
                    future.cancel()
                    futures_to_wait.append(future)
                process = self._processes.get(async_task_id)
                if process is not None and process.poll() is None:
                    processes.append(process)
                if str(async_task.get("kind") or "").strip() == "teammate":
                    set_teammate_shutdown(async_task, requested=True)
                closed = close_async_task(async_task, status="killed", reason=normalized_reason)
                self._store_async_task_locked(closed)
                stopped.append(copy.deepcopy(closed))
                owner_name = str(closed.get("owner_agent_name") or "").strip()
                agent = self._agents.get(owner_name)
                if isinstance(agent, dict) and str(agent.get("current_async_task_id") or "").strip() == async_task_id:
                    agent["current_async_task_id"] = ""
                    if str(closed.get("kind") or "").strip() == "teammate":
                        set_agent_status(agent, "shutdown")
                    else:
                        set_agent_status(agent, "idle")
            if stopped:
                self._persist_locked()

        for process in processes:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
            except Exception:
                logger.exception("停止后台进程失败")
        if futures_to_wait:
            concurrent.futures.wait(
                futures_to_wait,
                timeout=max(0.0, float(wait_timeout_seconds)),
                return_when=concurrent.futures.ALL_COMPLETED,
            )
        # Cancellation is a terminal result just like completion/failure.  It
        # must be persisted as a pending notification before consumers resume,
        # otherwise a process restart can silently lose the kill outcome.
        for async_task in stopped:
            self._emit_notification(
                async_task,
                status="killed",
                summary=(
                    f"后台任务已停止：{async_task.get('description') or async_task['async_task_id']}"
                ),
                extra_payload={"closed_reason": normalized_reason},
            )
        return [_to_public_async_task(item) for item in stopped]

    def stop_async_task(self, async_task_id: str, *, reason: str = "agent_requested",
                        wait_timeout_seconds: float = 1.0) -> AsyncTaskState | None:
        """
        停止单个指定的 async task。

        用于 async_task_stop 工具，模型可主动中止运行中的后台任务。
        复用 stop_active_async_tasks 内部的 future.cancel + process.terminate 逻辑。

        返回：
        - 成功停止时返回被停止任务的 public state
        - 任务不存在或已经结束时返回 None
        """
        normalized_task_id = str(async_task_id or "").strip()
        if not normalized_task_id:
            return None
        normalized_reason = str(reason or "agent_requested").strip() or "agent_requested"

        process_to_kill: subprocess.Popen[str] | None = None
        future_to_cancel: concurrent.futures.Future[Any] | None = None
        closed: InternalAsyncTaskState | None = None
        with self._lock:
            async_task = self._async_tasks.get(normalized_task_id)
            if not isinstance(async_task, dict):
                return None
            if str(async_task.get("status") or "").strip() not in _ASYNC_TASK_ACTIVE_STATUSES:
                return _to_public_async_task(async_task)

            future_to_cancel = self._futures.get(normalized_task_id)
            if future_to_cancel is not None and not future_to_cancel.done():
                future_to_cancel.cancel()
            process_to_kill = self._processes.get(normalized_task_id)
            if str(async_task.get("kind") or "").strip() == "teammate":
                set_teammate_shutdown(async_task, requested=True)
            closed = close_async_task(async_task, status="killed", reason=normalized_reason)
            self._store_async_task_locked(closed)
            owner_name = str(closed.get("owner_agent_name") or "").strip()
            agent = self._agents.get(owner_name)
            if isinstance(agent, dict) and str(agent.get("current_async_task_id") or "").strip() == normalized_task_id:
                agent["current_async_task_id"] = ""
                if str(closed.get("kind") or "").strip() == "teammate":
                    set_agent_status(agent, "shutdown")
                else:
                    set_agent_status(agent, "idle")
            self._persist_locked()

        if process_to_kill is not None and process_to_kill.poll() is None:
            try:
                process_to_kill.terminate()
                process_to_kill.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process_to_kill.kill()
                process_to_kill.wait(timeout=1.0)
            except Exception:
                logger.exception("停止后台进程失败: async_task_id=%s", normalized_task_id)
        if future_to_cancel is not None:
            concurrent.futures.wait([future_to_cancel],
                                    timeout=max(0.0, float(wait_timeout_seconds)))
        if closed is not None:
            self._emit_notification(
                closed,
                status="killed",
                summary=(
                    f"后台任务已停止：{closed.get('description') or closed['async_task_id']}"
                ),
                extra_payload={"closed_reason": normalized_reason},
            )
        return _to_public_async_task(closed) if closed else None

    def register_agent(
        self,
        *,
        agent_name: str,
        agent_id: str,
        agent_kind: RunnerAgentKind,
        metadata: dict[str, Any] | None = None,
    ) -> RunnerAgentState:
        normalized_agent_name = str(agent_name or "").strip()
        if not normalized_agent_name:
            raise ValueError("agent_name 不能为空")
        with self._lock:
            current = self._agents.get(normalized_agent_name)
            if isinstance(current, dict):
                agent = copy.deepcopy(current)
                agent["agent_id"] = str(agent_id or "").strip()
                agent["agent_name"] = normalized_agent_name
                agent["agent_kind"] = str(agent_kind or "").strip()
                merged_metadata = dict(agent.get("metadata") or {})
                merged_metadata.update(dict(metadata or {}))
                agent["metadata"] = merged_metadata
            else:
                agent = build_agent_state(
                    agent_id=str(agent_id or "").strip(),
                    agent_name=normalized_agent_name,
                    agent_kind=str(agent_kind or "").strip(),
                    metadata=metadata,
                )
            self._agents[normalized_agent_name] = agent
            self._persist_locked()
            return copy.deepcopy(agent)

    def _append_async_task_output(self, output_dir: Path, line: dict[str, Any]) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        events_path = output_dir / "events.json"
        entries: list[dict[str, Any]]
        if events_path.exists():
            try:
                loaded = json.loads(events_path.read_text(encoding="utf-8").strip() or "[]")
            except Exception:
                loaded = []
            entries = list(loaded) if isinstance(loaded, list) else []
        else:
            entries = []
        entries.append(line)
        events_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    def persist(self) -> None:
        with self._lock:
            self._persist_locked()

    def upsert_persisted_async_task(self, async_task: InternalAsyncTaskState) -> InternalAsyncTaskState:
        with self._lock:
            normalized = self._store_async_task_locked(async_task)
            self._persist_locked()
            return copy.deepcopy(normalized)

    def upsert_persisted_agent(self, agent_name: str, agent_state: RunnerAgentState) -> RunnerAgentState:
        with self._lock:
            normalized_name = str(agent_name or "").strip()
            self._agents[normalized_name] = copy.deepcopy(dict(agent_state))
            self._persist_locked()
            return copy.deepcopy(self._agents[normalized_name])

    def _emit_notification(
        self,
        async_task: InternalAsyncTaskState,
        *,
        status: str,
        summary: str,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        public_async_task = _to_public_async_task(async_task)
        created_at = time.time()
        event_id = f"evt_{uuid4().hex}"
        payload = {
            "async_task_id": public_async_task["async_task_id"],
            "status": status,
            "summary": summary,
            "output_dir": public_async_task["output_dir"],
            "kind": public_async_task["type"],
        }
        max_observation_chars = dict(async_task.get("metadata") or {}).get(
            "max_observation_chars"
        )
        if isinstance(max_observation_chars, int) and not isinstance(
            max_observation_chars, bool
        ) and max_observation_chars > 0:
            payload["max_observation_chars"] = max_observation_chars
        payload.update(dict(extra_payload or {}))
        event = {
            "event_id": event_id,
            "event_type": "async_task_notification",
            "created_at": created_at,
            "payload": payload,
        }
        # 先把 pending 通知写进 task manifest，再把事件送入内存队列。
        # 因此即使进程在两步之间退出，resume 仍能从 manifest 重新唤醒。
        with self._lock:
            current = copy.deepcopy(self._async_tasks.get(public_async_task["async_task_id"]) or async_task)
            current["notification"] = {
                "event_id": event_id,
                "state": "pending",
                "created_at": created_at,
                "delivered_at": None,
            }
            self._store_async_task_locked(current)
            self._persist_locked()
        self.event_queue.enqueue(str(async_task.get("owner_agent_id") or ""), event)

    def mark_notifications_delivered(self, event_ids: set[str]) -> None:
        """在模型成功完成使用这些附件的 step 后确认通知。"""

        normalized_ids = {str(item or "").strip() for item in event_ids if str(item or "").strip()}
        if not normalized_ids:
            return
        changed = False
        delivered_at = time.time()
        with self._lock:
            for task_id, async_task in list(self._async_tasks.items()):
                notification = dict(async_task.get("notification") or {})
                if (
                    str(notification.get("state") or "") != "pending"
                    or str(notification.get("event_id") or "") not in normalized_ids
                ):
                    continue
                updated = copy.deepcopy(async_task)
                updated["notification"] = {
                    **notification,
                    "state": "delivered",
                    "delivered_at": delivered_at,
                }
                self._async_tasks[task_id] = updated
                changed = True
            if changed:
                self._persist_locked()

    def requeue_pending_notifications(self) -> int:
        """恢复时按 event_id 重放未确认通知；EventQueue 自身负责去重。"""

        events: list[tuple[str, dict[str, Any]]] = []
        with self._lock:
            for async_task in self._async_tasks.values():
                notification = dict(async_task.get("notification") or {})
                if str(notification.get("state") or "") != "pending":
                    continue
                public_task = _to_public_async_task(async_task)
                events.append(
                    (
                        str(async_task.get("owner_agent_id") or ""),
                        {
                            "event_id": str(notification.get("event_id") or ""),
                            "event_type": "async_task_notification",
                            "created_at": float(notification.get("created_at") or 0.0),
                            "payload": {
                                "async_task_id": public_task["async_task_id"],
                                "status": public_task["status"],
                                "summary": "后台任务已有可消费结果",
                                "output_dir": public_task["output_dir"],
                                "kind": public_task["type"],
                                "max_observation_chars": dict(
                                    async_task.get("metadata") or {}
                                ).get("max_observation_chars"),
                            },
                        },
                    )
                )
        for owner_id, event in events:
            if owner_id and event["event_id"]:
                self.event_queue.enqueue(owner_id, event)
        return len(events)

    def mark_scope_finalizing(self, reason: str = "") -> AsyncTaskRuntimeState:
        with self._lock:
            self._scope_status = "finalizing"
            if reason:
                self._scope_meta["scope_status_reason"] = str(reason)
            self._persist_locked()
            return self._snapshot_store_locked()

    def mark_scope_finalized(self, reason: str = "") -> AsyncTaskRuntimeState:
        with self._lock:
            self._scope_status = "finalized"
            if reason:
                self._scope_meta["scope_status_reason"] = str(reason)
            self._persist_locked()
            return self._snapshot_store_locked()


__all__ = ["_AsyncTaskRuntime"]
