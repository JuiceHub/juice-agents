"""Workspace-local scheduled task store and due-task firing."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .cron import next_cron_run_timestamp, parse_cron_expression
from .lock import acquire_cron_lock, cron_lock_path

logger = logging.getLogger(__name__)


def cron_tasks_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir).expanduser().resolve() / ".juice" / "scheduled_tasks.json"


def _normalize_task(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    task_id = str(raw.get("id") or "").strip()
    cron = str(raw.get("cron") or "").strip()
    prompt = str(raw.get("prompt") or "").strip()
    if not task_id or not cron or not prompt or parse_cron_expression(cron) is None:
        return None
    try:
        created_at = float(raw.get("created_at"))
    except (TypeError, ValueError):
        return None
    task: dict[str, Any] = {
        "id": task_id,
        "cron": cron,
        "prompt": prompt,
        "created_at": created_at,
        "recurring": bool(raw.get("recurring")),
    }
    if raw.get("last_fired_at") is not None:
        try:
            task["last_fired_at"] = float(raw.get("last_fired_at"))
        except (TypeError, ValueError):
            pass
    return task


def _read_tasks_unlocked(workspace_dir: str | Path) -> list[dict[str, Any]]:
    path = cron_tasks_path(workspace_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("failed to read cron tasks file %s: %s", path, exc)
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
        return []
    tasks: list[dict[str, Any]] = []
    for raw in payload.get("tasks") or []:
        task = _normalize_task(raw)
        if task is not None:
            tasks.append(task)
    return tasks


def _write_tasks_unlocked(workspace_dir: str | Path, tasks: list[dict[str, Any]]) -> Path:
    path = cron_tasks_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"tasks": list(tasks)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _task_with_next_run(task: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    anchor = float(task.get("last_fired_at") or task.get("created_at") or (now or time.time()))
    next_run_at = next_cron_run_timestamp(str(task.get("cron") or ""), anchor)
    return {**task, "next_run_at": next_run_at}


def list_cron_tasks(workspace_dir: str | Path) -> list[dict[str, Any]]:
    """Return valid scheduled tasks with computed next-run timestamps."""

    now = time.time()
    return [_task_with_next_run(task, now=now) for task in _read_tasks_unlocked(workspace_dir)]


def create_cron_task(
    workspace_dir: str | Path,
    cron: str,
    prompt: str,
    recurring: bool = True,
) -> dict[str, Any]:
    """Create a scheduled task in the workspace-local task file."""

    normalized_cron = str(cron or "").strip()
    normalized_prompt = str(prompt or "").strip()
    if parse_cron_expression(normalized_cron) is None:
        raise ValueError(f"invalid cron expression: {normalized_cron}")
    if next_cron_run_timestamp(normalized_cron, time.time()) is None:
        raise ValueError(f"cron expression has no run in the next year: {normalized_cron}")
    if not normalized_prompt:
        raise ValueError("prompt must be a non-empty string")

    with acquire_cron_lock(workspace_dir, owner="cron_create"):
        tasks = _read_tasks_unlocked(workspace_dir)
        task = {
            "id": uuid4().hex[:8],
            "cron": normalized_cron,
            "prompt": normalized_prompt,
            "created_at": time.time(),
            "recurring": bool(recurring),
        }
        tasks.append(task)
        _write_tasks_unlocked(workspace_dir, tasks)
    return _task_with_next_run(task)


def delete_cron_task(workspace_dir: str | Path, task_id: str) -> dict[str, Any]:
    """Delete a scheduled task by id."""

    normalized_id = str(task_id or "").strip()
    if not normalized_id:
        raise ValueError("task_id must be non-empty")
    with acquire_cron_lock(workspace_dir, owner="cron_delete"):
        tasks = _read_tasks_unlocked(workspace_dir)
        remaining = [task for task in tasks if str(task.get("id") or "") != normalized_id]
        deleted = len(remaining) != len(tasks)
        if deleted:
            _write_tasks_unlocked(workspace_dir, remaining)
    return {"deleted": deleted, "id": normalized_id}


def _is_due(task: dict[str, Any], now: float) -> bool:
    anchor = float(task.get("last_fired_at") or task.get("created_at") or now)
    next_run_at = next_cron_run_timestamp(str(task.get("cron") or ""), anchor)
    return next_run_at is not None and next_run_at <= now


def fire_due_cron_tasks(workspace_dir: str | Path, now: float | None = None) -> list[dict[str, Any]]:
    """
    Mark due tasks as fired and return prompts to execute.

    The store update happens under a short file lock so concurrent CLI
    instances do not return the same due task twice.
    """

    current = time.time() if now is None else float(now)
    with acquire_cron_lock(workspace_dir, owner="cron_fire", timeout_seconds=0.2):
        tasks = _read_tasks_unlocked(workspace_dir)
        fired: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for task in tasks:
            if not _is_due(task, current):
                remaining.append(task)
                continue
            fired_task = {**task, "fired_at": current}
            fired.append(fired_task)
            if bool(task.get("recurring")):
                remaining.append({**task, "last_fired_at": current})
        if fired:
            _write_tasks_unlocked(workspace_dir, remaining)
        return fired


def cron_status(workspace_dir: str | Path) -> dict[str, Any]:
    """Return a transport-friendly cron task summary."""

    tasks = list_cron_tasks(workspace_dir)
    next_values = [task.get("next_run_at") for task in tasks if task.get("next_run_at") is not None]
    lock_path = cron_lock_path(workspace_dir)
    return {
        "enabled": True,
        "tasks_path": str(cron_tasks_path(workspace_dir)),
        "lock_path": str(lock_path),
        "task_count": len(tasks),
        "next_run_at": min(next_values) if next_values else None,
        "lock_present": lock_path.exists(),
    }


__all__ = [
    "create_cron_task",
    "cron_status",
    "cron_tasks_path",
    "delete_cron_task",
    "fire_due_cron_tasks",
    "list_cron_tasks",
]
