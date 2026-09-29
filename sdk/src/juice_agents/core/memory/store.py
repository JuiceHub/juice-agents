"""Workspace memory filesystem store."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ENTRYPOINT_NAME = "MEMORY.md"
TOPICS_DIRNAME = "topics"
LOCK_NAME = ".dream.lock"
LAST_DREAM_NAME = ".dream.last"
DEFAULT_DREAM_LOCK_STALE_SECONDS = 3600


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized or "memory"


def _title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-") if part) or slug


class MemoryStore:
    """Read and write `.juice/memory` with strict path containment."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.topics_dir = self.root / TOPICS_DIRNAME
        self.entrypoint = self.root / ENTRYPOINT_NAME
        self.lock_path = self.root / LOCK_NAME
        self.last_dream_path = self.root / LAST_DREAM_NAME
        self.ensure_initialized()

    @classmethod
    def for_workspace(cls, workspace_dir: str | Path) -> "MemoryStore":
        return cls(Path(workspace_dir).expanduser().resolve() / ".juice" / "memory")

    def ensure_initialized(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        if not self.entrypoint.exists():
            self.entrypoint.write_text("# Workspace Memory\n\n", encoding="utf-8")

    def _resolve(self, path: str | Path | None) -> Path:
        raw = ENTRYPOINT_NAME if path is None or str(path).strip() == "" else str(path)
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.expanduser().resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path is outside memory root: {resolved}") from exc
        return resolved

    def read(self, path: str | Path | None = None) -> str:
        resolved = self._resolve(path)
        if not resolved.exists():
            raise FileNotFoundError(str(resolved))
        if not resolved.is_file():
            raise ValueError(f"memory path is not a file: {resolved}")
        return resolved.read_text(encoding="utf-8")

    def status(
        self,
        *,
        enabled: bool = True,
        dream_enabled: bool = True,
        dream_config: Any | None = None,
        runner_id: str = "",
    ) -> dict[str, Any]:
        topics = sorted(self.topics_dir.glob("*.md"))
        last_completed_at = self.dream_last_completed_at()
        last_dream_at = (
            datetime.fromtimestamp(last_completed_at).isoformat(timespec="seconds")
            if last_completed_at is not None
            else None
        )
        payload = {
            "enabled": bool(enabled),
            "dream_enabled": bool(dream_enabled),
            "memory_dir": str(self.root),
            "entrypoint": str(self.entrypoint),
            "topic_count": len(topics),
            "last_dream_at": last_dream_at,
        }
        if dream_config is not None:
            payload.update(
                self.dream_due_status(config=dream_config, runner_id=runner_id)
            )
        return payload

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _frontmatter(self, *, slug: str, memory_type: str, source: str) -> str:
        today = datetime.now().date().isoformat()
        return "\n".join(
            [
                "---",
                f"name: {slug}",
                f"type: {memory_type or 'general'}",
                f'updated_at: "{today}"',
                f"source: {source or 'explicit'}",
                "---",
                "",
            ]
        )

    def write_topic(
        self,
        *,
        title: str,
        content: str,
        memory_type: str = "general",
        source: str = "explicit",
    ) -> dict[str, Any]:
        slug = _slugify(title)
        display_title = str(title or "").strip() or _title_from_slug(slug)
        topic_path = self.topics_dir / f"{slug}.md"
        body = str(content or "").strip()
        if not body:
            raise ValueError("memory content cannot be empty")
        text = f"{self._frontmatter(slug=slug, memory_type=memory_type, source=source)}## Facts\n{body}\n"
        topic_path.write_text(text, encoding="utf-8")
        rel_path = self._relative(topic_path)
        self._upsert_index_line(display_title, rel_path, body)
        return {"updated": True, "path": rel_path, "title": display_title}

    def _upsert_index_line(self, title: str, rel_path: str, body: str) -> None:
        self.ensure_initialized()
        summary = next((line.strip("- ").strip() for line in body.splitlines() if line.strip()), "")
        if len(summary) > 120:
            summary = summary[:117].rstrip() + "..."
        line = f"- [{title}]({rel_path}) - {summary or title}"
        lines = self.entrypoint.read_text(encoding="utf-8").splitlines()
        kept = [existing for existing in lines if f"]({rel_path})" not in existing]
        if kept and kept[-1].strip():
            kept.append("")
        kept.append(line)
        self.entrypoint.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        needle = str(query or "").strip().lower()
        if not needle:
            return []
        hits: list[dict[str, Any]] = []
        files = sorted(self.topics_dir.glob("*.md"))
        for path in files:
            if not path.exists():
                continue
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if needle in line.lower():
                    hits.append(
                        {
                            "path": self._relative(path),
                            "line": line_no,
                            "snippet": line.strip(),
                        }
                    )
                    if len(hits) >= limit:
                        return hits
        return hits

    def forget(self, path_or_title: str, *, reason: str = "") -> dict[str, Any]:
        target = str(path_or_title or "").strip()
        if not target:
            raise ValueError("path_or_title cannot be empty")
        if target.endswith(".md") or "/" in target:
            path = self._resolve(target)
        else:
            path = self.topics_dir / f"{_slugify(target)}.md"
        rel_path = self._relative(path)
        removed = False
        if path.exists():
            path.unlink()
            removed = True
        self._remove_index_line(rel_path)
        return {"removed": removed, "path": rel_path, "reason": reason}

    def _remove_index_line(self, rel_path: str) -> None:
        if not self.entrypoint.exists():
            return
        lines = [
            line
            for line in self.entrypoint.read_text(encoding="utf-8").splitlines()
            if f"]({rel_path})" not in line
        ]
        self.entrypoint.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def dream_last_completed_at(self) -> float | None:
        """Return the last successful dream timestamp from `.dream.last` mtime."""

        if not self.last_dream_path.exists():
            return None
        return self.last_dream_path.stat().st_mtime

    def count_runner_sessions_touched_since(
        self,
        since_ts: float | None,
        *,
        exclude_runner_id: str = "",
    ) -> int:
        """Count persisted managed agent sessions changed after the last dream.

        The count is intentionally file based and cheap: it scans session files under
        `.juice/runners/*/agents/*/session.json` and excludes the live runner.
        """

        workspace = self.root.parent.parent
        runners_dir = workspace / ".juice" / "runners"
        if not runners_dir.exists():
            return 0
        cutoff = float(since_ts or 0.0)
        excluded = str(exclude_runner_id or "").strip()
        count = 0
        for session_path in runners_dir.glob("*/agents/*/session.json"):
            try:
                runner_id = session_path.relative_to(runners_dir).parts[0]
            except (IndexError, ValueError):
                continue
            if excluded and runner_id == excluded:
                continue
            if session_path.stat().st_mtime <= cutoff:
                continue
            count += 1
        return count

    def dream_due_status(self, *, config: Any, runner_id: str = "") -> dict[str, Any]:
        """Return whether automatic dream should run and why."""

        last_completed_at = self.dream_last_completed_at()
        min_hours = int(getattr(config, "min_hours", 24) or 24)
        min_sessions = int(getattr(config, "min_sessions", 5) or 5)
        lock_owner = self.current_dream_lock_owner()
        eligible_sessions = self.count_runner_sessions_touched_since(
            last_completed_at,
            exclude_runner_id=runner_id,
        )
        if lock_owner:
            return {
                "auto_dream_due": False,
                "next_dream_reason": "dream already running",
                "eligible_session_count": eligible_sessions,
                "dream_lock_owner": lock_owner,
            }
        if last_completed_at is not None:
            elapsed_hours = (time.time() - last_completed_at) / 3600
            if elapsed_hours < min_hours:
                return {
                    "auto_dream_due": False,
                    "next_dream_reason": (
                        f"need {min_hours} hours, found {elapsed_hours:.1f}"
                    ),
                    "eligible_session_count": eligible_sessions,
                    "dream_lock_owner": "",
                }
        if eligible_sessions < min_sessions:
            return {
                "auto_dream_due": False,
                "next_dream_reason": (
                    f"need {min_sessions} sessions, found {eligible_sessions}"
                ),
                "eligible_session_count": eligible_sessions,
                "dream_lock_owner": "",
            }
        return {
            "auto_dream_due": True,
            "next_dream_reason": "due",
            "eligible_session_count": eligible_sessions,
            "dream_lock_owner": "",
        }

    def current_dream_lock_owner(
        self,
        *,
        stale_seconds: int = DEFAULT_DREAM_LOCK_STALE_SECONDS,
    ) -> str:
        """Return the active dream lock owner, ignoring stale reclaimable locks."""

        lock = self._read_dream_lock()
        if lock is None:
            return ""
        if self._is_reclaimable_dream_lock(lock, stale_seconds=stale_seconds):
            return ""
        return str(lock.get("owner") or "").strip()

    def acquire_dream_lock(
        self,
        owner: str,
        *,
        stale_seconds: int = DEFAULT_DREAM_LOCK_STALE_SECONDS,
    ) -> dict[str, Any]:
        self.ensure_initialized()
        if self.lock_path.exists():
            lock = self._read_dream_lock()
            if lock is not None and not self._is_reclaimable_dream_lock(
                lock,
                stale_seconds=stale_seconds,
            ):
                return {
                    "acquired": False,
                    "owner": str(lock.get("owner") or "").strip(),
                    "path": str(self.lock_path),
                }
            self.release_dream_lock()
        payload = {
            "owner": str(owner or "memory-maintenance"),
            "pid": os.getpid(),
            "created_at": time.time(),
        }
        self.lock_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return {"acquired": True, "owner": str(owner or "memory-maintenance"), "path": str(self.lock_path)}

    def release_dream_lock(self) -> None:
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            return

    def record_dream_completed(self, *, completed_at: float | None = None) -> None:
        self.ensure_initialized()
        timestamp = float(completed_at or time.time())
        self.last_dream_path.write_text(f"completed_at={timestamp}", encoding="utf-8")
        os.utime(self.last_dream_path, (timestamp, timestamp))

    def _read_dream_lock(self) -> dict[str, Any] | None:
        if not self.lock_path.exists():
            return None
        text = self.lock_path.read_text(encoding="utf-8").strip()
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload = {"owner": text}
        if not isinstance(payload, dict):
            payload = {"owner": text}
        stat = self.lock_path.stat()
        return {
            "owner": str(payload.get("owner") or text or "memory-maintenance").strip(),
            "pid": payload.get("pid"),
            "created_at": float(payload.get("created_at") or stat.st_mtime),
            "mtime": stat.st_mtime,
        }

    @staticmethod
    def _process_is_running(pid: Any) -> bool:
        try:
            normalized = int(pid)
        except (TypeError, ValueError):
            return False
        if normalized <= 0:
            return False
        try:
            os.kill(normalized, 0)
        except OSError:
            return False
        return True

    def _is_reclaimable_dream_lock(
        self,
        lock: dict[str, Any],
        *,
        stale_seconds: int,
    ) -> bool:
        age = time.time() - float(lock.get("mtime") or lock.get("created_at") or 0)
        if age >= max(int(stale_seconds or DEFAULT_DREAM_LOCK_STALE_SECONDS), 1):
            return True
        pid = lock.get("pid")
        return pid is not None and not self._process_is_running(pid)
