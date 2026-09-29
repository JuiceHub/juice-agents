"""Short-lived file lock for workspace cron task mutations."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


def cron_lock_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir).expanduser().resolve() / ".juice" / "scheduled_tasks.lock"


def _process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read_lock(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _try_acquire(path: Path, owner: str) -> bool:
    body = json.dumps(
        {"owner": owner, "pid": os.getpid(), "acquired_at": time.time()},
        ensure_ascii=False,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(body + "\n")
        return True
    except FileExistsError:
        existing = _read_lock(path)
        pid = int(existing.get("pid") or 0) if existing else 0
        if existing and _process_running(pid):
            return False
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(body + "\n")
            return True
        except FileExistsError:
            return False


@contextmanager
def acquire_cron_lock(
    workspace_dir: str | Path,
    *,
    owner: str | None = None,
    timeout_seconds: float = 2.0,
    poll_seconds: float = 0.05,
) -> Iterator[dict[str, object]]:
    """Acquire a workspace cron lock, waiting briefly before failing."""

    path = cron_lock_path(workspace_dir)
    lock_owner = owner or f"cron:{os.getpid()}:{uuid4().hex[:8]}"
    deadline = time.time() + max(0.0, float(timeout_seconds))
    acquired = False
    while True:
        if _try_acquire(path, lock_owner):
            acquired = True
            break
        if time.time() >= deadline:
            existing = _read_lock(path) or {}
            raise TimeoutError(f"cron lock is held by {existing.get('owner') or 'unknown'}")
        time.sleep(max(0.01, float(poll_seconds)))

    try:
        yield {"owner": lock_owner, "path": str(path)}
    finally:
        if acquired:
            existing = _read_lock(path) or {}
            if str(existing.get("owner") or "") == lock_owner:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


__all__ = ["acquire_cron_lock", "cron_lock_path"]
