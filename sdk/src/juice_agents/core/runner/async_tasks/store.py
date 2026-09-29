"""Runner scoped async task registry 的持久化。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from juice_agents.core.utils import sanitize_path_component

from .internal_types import AsyncTaskState, RunnerAgentState, AsyncTaskRuntimeState
from .kinds import ASYNC_TASK_KINDS as _ASYNC_TASK_KINDS, PUBLIC_TO_ASYNC_TASK_KIND

_SCOPE_STATUSES = {"running", "finalizing", "finalized"}
_AGENT_STATUSES = {"active", "idle", "shutdown_requested", "shutdown"}
_ASYNC_TASK_STATUSES = {"pending", "running", "completed", "failed", "killed"}
_DEFAULT_SCOPE_KIND = "agent"


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    return json.loads(text)


def _write_json(path: Path, payload: Any) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _touch_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def blank_async_tasks_store(
    *,
    scope_id: str,
    scope_kind: str,
    scope_meta: dict[str, Any] | None = None,
) -> AsyncTaskRuntimeState:
    normalized_scope_kind = str(scope_kind or _DEFAULT_SCOPE_KIND).strip() or _DEFAULT_SCOPE_KIND
    return {
        "scope_id": str(scope_id or "").strip(),
        "scope_kind": normalized_scope_kind,
        "scope_status": "running",
        "scope_meta": dict(scope_meta or {}),
        "agents": {},
        "async_tasks": [],
    }


def _normalize_agent_state(agent_name: str, raw: dict[str, Any]) -> RunnerAgentState:
    agent = dict(raw or {})
    status = str(agent.get("status") or "active").strip()
    if status not in _AGENT_STATUSES:
        status = "active"
    return {
        "agent_id": str(agent.get("agent_id") or "").strip(),
        "agent_name": str(agent.get("agent_name") or agent_name).strip(),
        "agent_kind": str(agent.get("agent_kind") or "").strip(),
        "status": status,
        "current_async_task_id": str(agent.get("current_async_task_id") or "").strip(),
        "last_error": str(agent.get("last_error") or "").strip(),
        "metadata": dict(agent.get("metadata") or {}),
    }


def _normalize_async_task_kind(value: Any) -> str:
    """接受公开 type 或内部 kind，统一归一化为内部 kind。

    刻意不用 `to_internal_kind()`：那个 helper 对未知值回退到 `agent_dispatch`，
    会把脏落盘数据静默改写成合法值。持久化入口必须对非法 kind 抛错。
    """

    kind = str(value or "").strip()
    # 公开名映射到内部名；已经是内部名（或彻底非法）时原样透传给下面的校验。
    kind = PUBLIC_TO_ASYNC_TASK_KIND.get(kind, kind)
    if kind not in _ASYNC_TASK_KINDS:
        raise ValueError(f"非法 async_task.kind: {kind}")
    return kind


def _normalize_async_task_state(raw: dict[str, Any]) -> AsyncTaskState:
    task = dict(raw or {})
    kind = _normalize_async_task_kind(task.get("kind") or task.get("type"))
    status = str(task.get("status") or "pending").strip()
    if status not in _ASYNC_TASK_STATUSES:
        raise ValueError(f"非法 async_task.status: {status}")
    async_task_id = str(task.get("async_task_id") or "").strip()
    output_dir = str(task.get("output_dir") or "").strip()
    notification = task.get("notification")
    if notification is not None:
        if not isinstance(notification, dict):
            raise ValueError("非法 async_task.notification")
        notification_state = str(notification.get("state") or "").strip()
        if notification_state not in {"pending", "delivered"}:
            raise ValueError(f"非法 async_task.notification.state: {notification_state!r}")
        notification = {
            "event_id": str(notification.get("event_id") or "").strip(),
            "state": notification_state,
            "created_at": float(notification.get("created_at") or 0.0),
            "delivered_at": (
                None
                if notification.get("delivered_at") is None
                else float(notification.get("delivered_at") or 0.0)
            ),
        }
    return {
        "async_task_id": async_task_id,
        "kind": kind,
        "owner_agent_id": str(task.get("owner_agent_id") or "").strip(),
        "owner_agent_name": str(task.get("owner_agent_name") or "").strip(),
        "status": status,
        "description": str(task.get("description") or "").strip(),
        "output_dir": output_dir,
        "created_at": float(task.get("created_at") or 0.0),
        "started_at": None if task.get("started_at") is None else float(task.get("started_at") or 0.0),
        "finished_at": None if task.get("finished_at") is None else float(task.get("finished_at") or 0.0),
        "closed_reason": str(task.get("closed_reason") or "").strip(),
        "error": str(task.get("error") or "").strip(),
        "metadata": dict(task.get("metadata") or {}),
        "notification": notification,
    }


def normalize_async_tasks_store(
    payload: dict[str, Any], *, fallback_scope_id: str, fallback_scope_kind: str
) -> AsyncTaskRuntimeState:
    store = dict(payload or {})
    scope_kind = str(store.get("scope_kind") or fallback_scope_kind or _DEFAULT_SCOPE_KIND).strip() or _DEFAULT_SCOPE_KIND
    scope_status = str(store.get("scope_status") or "running").strip()
    if scope_status not in _SCOPE_STATUSES:
        scope_status = "running"
    agents = {
        str(agent_name): _normalize_agent_state(str(agent_name), dict(agent or {}))
        for agent_name, agent in dict(store.get("agents") or {}).items()
        if str(agent_name or "").strip()
    }
    raw_tasks = list(store.get("async_tasks") or [])
    async_tasks = [_normalize_async_task_state(item) for item in raw_tasks if isinstance(item, dict)]
    return {
        "scope_id": str(store.get("scope_id") or fallback_scope_id).strip(),
        "scope_kind": scope_kind,
        "scope_status": scope_status,
        "scope_meta": dict(store.get("scope_meta") or {}),
        "agents": agents,
        "async_tasks": async_tasks,
    }


def load_async_tasks_store(
    path: str | Path,
    *,
    scope_id: str = "",
    scope_kind: str = _DEFAULT_SCOPE_KIND,
    scope_meta: dict[str, Any] | None = None,
) -> AsyncTaskRuntimeState:
    registry_path = Path(path)
    payload = _read_json(registry_path, default={})
    if not isinstance(payload, dict) or not payload:
        return blank_async_tasks_store(scope_id=scope_id, scope_kind=scope_kind, scope_meta=scope_meta)
    normalized = normalize_async_tasks_store(payload, fallback_scope_id=scope_id, fallback_scope_kind=scope_kind)
    if scope_meta:
        normalized["scope_meta"].update(dict(scope_meta))
    return normalized


def write_async_tasks_store(path: str | Path, payload: AsyncTaskRuntimeState) -> AsyncTaskRuntimeState:
    registry_path = Path(path)
    normalized = normalize_async_tasks_store(
        dict(payload or {}),
        fallback_scope_id=str(payload.get("scope_id") or ""),
        fallback_scope_kind=str(payload.get("scope_kind") or _DEFAULT_SCOPE_KIND),
    )
    written = _write_json(registry_path, normalized)
    for async_task in list(written.get("async_tasks") or []):
        if not isinstance(async_task, dict):
            continue
        async_task_id = str(async_task.get("async_task_id") or "").strip()
        if not async_task_id:
            continue
        task_dir = registry_path.parent / sanitize_path_component(async_task_id, fallback="task")
        _write_json(task_dir / "manifest.json", async_task)
        events_path = task_dir / "events.json"
        if not events_path.exists():
            _write_json(events_path, [])
        _touch_file(task_dir / "stdout.log")
        _touch_file(task_dir / "stderr.log")
    return written


def build_scoped_async_task_output_dir(store_path: str | Path, async_task_id: str) -> Path:
    registry_path = Path(store_path)
    task_dir = registry_path.parent / sanitize_path_component(async_task_id, fallback="task")
    task_dir.mkdir(parents=True, exist_ok=True)
    events_path = task_dir / "events.json"
    if not events_path.exists():
        _write_json(events_path, [])
    _touch_file(task_dir / "stdout.log")
    _touch_file(task_dir / "stderr.log")
    return task_dir


def read_async_task_output(
    output_dir: str | Path,
    *,
    tail_chars: int = 12_000,
) -> dict[str, Any]:
    """读取统一 async task 目录，返回状态、事件、最近结果与日志尾部。"""

    task_dir = Path(output_dir)
    manifest = _read_json(task_dir / "manifest.json", default={})
    events = _read_json(task_dir / "events.json", default=[])
    if not isinstance(manifest, dict):
        raise ValueError(f"async task manifest 非法: {task_dir}")
    if not isinstance(events, list):
        raise ValueError(f"async task events 非法: {task_dir}")
    latest_result = None
    for event in reversed(events):
        if isinstance(event, dict) and "result" in event:
            latest_result = event.get("result")
            break

    def _tail(path: Path) -> str:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-max(0, int(tail_chars)) :]

    return {
        "status": str(manifest.get("status") or "").strip(),
        "events": events,
        "latest_result": latest_result,
        "stdout": _tail(task_dir / "stdout.log"),
        "stderr": _tail(task_dir / "stderr.log"),
    }


__all__ = [
    "blank_async_tasks_store",
    "build_scoped_async_task_output_dir",
    "load_async_tasks_store",
    "normalize_async_tasks_store",
    "read_async_task_output",
    "write_async_tasks_store",
]
