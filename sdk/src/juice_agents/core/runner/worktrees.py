"""Git worktree support owned by Runner.

The manager deliberately keeps all persistent metadata under the state
workspace's ``.juice`` directory while linked working copies live below
``.juice/worktrees/<slug>``.  Runner can then switch the active tool workspace
without moving its transcript, async task store, or managed agent state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_MAX_SLUG_LENGTH = 80
_SLUG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


class WorktreeError(RuntimeError):
    """Raised when a worktree operation cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class WorktreeStatus:
    name: str
    slug: str
    path: str
    branch: str
    base_sha: str
    head_sha: str
    dirty: bool
    ahead: int
    exists: bool
    active: bool = False
    temporary: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WorktreeMetadata:
    name: str
    slug: str
    path: str
    branch: str
    base_sha: str
    created_at: str
    temporary: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorktreeMetadata":
        return cls(
            name=str(payload.get("name") or payload.get("slug") or "").strip(),
            slug=str(payload.get("slug") or "").strip(),
            path=str(payload.get("path") or "").strip(),
            branch=str(payload.get("branch") or "").strip(),
            base_sha=str(payload.get("base_sha") or "").strip(),
            created_at=str(payload.get("created_at") or "").strip(),
            temporary=bool(payload.get("temporary", False)),
        )


def slugify_worktree_name(name: str | None = None, *, prefix: str = "task") -> str:
    """Return a branch/path safe slug for explicit or generated worktree names."""

    raw = str(name or "").strip()
    if not raw:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        raw = f"{prefix}-{stamp}-{uuid4().hex[:6]}"
    slug = _SLUG_RE.sub("-", raw).strip("-_.").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        raise WorktreeError("worktree name must contain at least one safe character")
    if slug in {".", ".."} or "/" in slug or "\\" in slug:
        raise WorktreeError(f"invalid worktree name: {name!r}")
    return slug[:_MAX_SLUG_LENGTH].strip("-_.") or slug[:_MAX_SLUG_LENGTH]


class WorktreeManager:
    """Small, testable wrapper around ``git worktree``."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.repo_root = self._git(["rev-parse", "--show-toplevel"]).strip()
        if not self.repo_root:
            raise WorktreeError(f"not a git repository: {self.base_dir}")
        self.repo_root_path = Path(self.repo_root).resolve()
        self.worktrees_root = self.repo_root_path / ".juice" / "worktrees"
        self.metadata_root = self.worktrees_root / ".metadata"

    def _git(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        check: bool = True,
    ) -> str:
        command = ["git", "-C", str(cwd or getattr(self, "repo_root_path", self.base_dir)), *args]
        logger.debug("Running git command: %s", " ".join(command))
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if check and completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            if args[:1] == ["worktree"] and "is not a git command" in stderr:
                raise WorktreeError("git worktree is not available; install Git 2.5+")
            raise WorktreeError(stderr or f"git command failed: {' '.join(args)}")
        return completed.stdout

    def _branch_exists(self, branch: str) -> bool:
        completed = subprocess.run(
            ["git", "-C", str(self.repo_root_path), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.returncode == 0

    def _metadata_path(self, slug: str) -> Path:
        return self.metadata_root / f"{slug}.json"

    def _write_metadata(self, meta: WorktreeMetadata) -> None:
        self.metadata_root.mkdir(parents=True, exist_ok=True)
        self._metadata_path(meta.slug).write_text(
            json.dumps(meta.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_metadata(self, slug: str) -> WorktreeMetadata | None:
        path = self._metadata_path(slug)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise WorktreeError(f"invalid worktree metadata: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise WorktreeError(f"invalid worktree metadata object: {path}")
        return WorktreeMetadata.from_dict(payload)

    def _metadata_entries(self) -> list[WorktreeMetadata]:
        if not self.metadata_root.exists():
            return []
        entries: list[WorktreeMetadata] = []
        for path in sorted(self.metadata_root.glob("*.json")):
            try:
                entries.append(WorktreeMetadata.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except Exception as exc:
                logger.warning("Skipping invalid worktree metadata: path=%s error=%s", path, exc)
        return entries

    def ensure_worktree(self, name: str | None = None, *, temporary: bool = False) -> WorktreeStatus:
        """Create or reuse a named worktree and return its status."""

        slug = slugify_worktree_name(name, prefix="agent" if temporary else "task")
        display_name = str(name or slug).strip() or slug
        branch = f"worktree-{slug}"
        path = self.worktrees_root / slug
        existing_meta = self._read_metadata(slug)
        if existing_meta is not None and Path(existing_meta.path).exists():
            return self.status(slug, active_path=Path(existing_meta.path))
        if path.exists():
            raise WorktreeError(f"worktree path already exists without valid metadata: {path}")
        if self._branch_exists(branch):
            raise WorktreeError(f"branch already exists: {branch}")

        base_sha = self._git(["rev-parse", "HEAD"]).strip()
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        self._git(["worktree", "add", "-b", branch, str(path), "HEAD"])
        meta = WorktreeMetadata(
            name=display_name,
            slug=slug,
            path=str(path),
            branch=branch,
            base_sha=base_sha,
            created_at=datetime.now(timezone.utc).isoformat(),
            temporary=temporary,
        )
        self._write_metadata(meta)
        self._copy_worktree_include(path)
        logger.info("Created worktree: slug=%s branch=%s path=%s", slug, branch, path)
        return self.status(slug, active_path=path)

    def list(self, *, active_path: str | Path | None = None) -> list[WorktreeStatus]:
        active = Path(active_path).expanduser().resolve() if active_path else None
        return [self.status(meta.slug, active_path=active) for meta in self._metadata_entries()]

    def status(self, name: str | None = None, *, active_path: str | Path | None = None) -> WorktreeStatus:
        slug = slugify_worktree_name(name) if name else ""
        meta = self._read_metadata(slug) if slug else None
        if meta is None and name:
            path = self.worktrees_root / slug
            meta = WorktreeMetadata(
                name=slug,
                slug=slug,
                path=str(path),
                branch=f"worktree-{slug}",
                base_sha="",
                created_at="",
                temporary=False,
            )
        if meta is None:
            return WorktreeStatus(
                name="",
                slug="",
                path="",
                branch="",
                base_sha="",
                head_sha="",
                dirty=False,
                ahead=0,
                exists=False,
                active=False,
                temporary=False,
                error="no active worktree",
            )
        path = Path(meta.path).expanduser().resolve()
        exists = path.exists()
        active = bool(active_path and path == Path(active_path).expanduser().resolve())
        if not exists:
            return WorktreeStatus(
                name=meta.name,
                slug=meta.slug,
                path=str(path),
                branch=meta.branch,
                base_sha=meta.base_sha,
                head_sha="",
                dirty=False,
                ahead=0,
                exists=False,
                active=active,
                temporary=meta.temporary,
                error="worktree path does not exist",
            )
        try:
            head_sha = self._git(["rev-parse", "HEAD"], cwd=path).strip()
            dirty = bool(self._git(["status", "--porcelain"], cwd=path).strip())
            ahead = 0
            if meta.base_sha:
                raw_ahead = self._git(["rev-list", "--count", f"{meta.base_sha}..HEAD"], cwd=path).strip()
                ahead = int(raw_ahead or "0")
            return WorktreeStatus(
                name=meta.name,
                slug=meta.slug,
                path=str(path),
                branch=meta.branch,
                base_sha=meta.base_sha,
                head_sha=head_sha,
                dirty=dirty,
                ahead=ahead,
                exists=True,
                active=active,
                temporary=meta.temporary,
            )
        except Exception as exc:
            return WorktreeStatus(
                name=meta.name,
                slug=meta.slug,
                path=str(path),
                branch=meta.branch,
                base_sha=meta.base_sha,
                head_sha="",
                dirty=True,
                ahead=1,
                exists=True,
                active=active,
                temporary=meta.temporary,
                error=str(exc),
            )

    def remove(self, name: str, *, discard: bool = False) -> WorktreeStatus:
        """Remove a managed worktree, refusing unsafe cleanup unless discarded."""

        slug = slugify_worktree_name(name)
        status = self.status(slug)
        if not status.exists:
            raise WorktreeError(status.error or f"worktree not found: {name}")
        if not discard and (status.error or status.dirty or status.ahead > 0):
            reasons = []
            if status.error:
                reasons.append(status.error)
            if status.dirty:
                reasons.append("dirty working tree")
            if status.ahead > 0:
                reasons.append(f"{status.ahead} commit(s) ahead of base")
            raise WorktreeError(
                "refusing to remove worktree; use discard=true to override: "
                + ", ".join(reasons)
            )
        args = ["worktree", "remove"]
        if discard:
            args.append("--force")
        args.append(status.path)
        self._git(args)
        if status.branch and self._branch_exists(status.branch):
            self._git(["branch", "-D", status.branch])
        self._metadata_path(slug).unlink(missing_ok=True)
        logger.info("Removed worktree: slug=%s discard=%s", slug, discard)
        return WorktreeStatus(
            **{**status.to_dict(), "exists": False, "active": False}
        )

    def _copy_worktree_include(self, target_root: Path) -> None:
        include_file = self.repo_root_path / ".worktreeinclude"
        if not include_file.exists():
            return
        try:
            from pathspec import PathSpec
        except Exception as exc:
            raise WorktreeError("pathspec is required for .worktreeinclude support") from exc

        patterns = [
            line.rstrip("\n")
            for line in include_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not patterns:
            return
        spec = PathSpec.from_lines("gitwildmatch", patterns)
        copied = 0
        for source in self.repo_root_path.rglob("*"):
            if not source.is_file() and not source.is_symlink():
                continue
            try:
                rel = source.relative_to(self.repo_root_path).as_posix()
            except ValueError:
                continue
            if rel.startswith(".git/") or rel.startswith(".juice/worktrees/"):
                continue
            if not spec.match_file(rel):
                continue
            if not self._is_gitignored(rel):
                continue
            destination = target_root / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
                destination.symlink_to(source.readlink())
            else:
                shutil.copy2(source, destination)
            copied += 1
        logger.info("Copied .worktreeinclude files: count=%d target=%s", copied, target_root)

    def _is_gitignored(self, rel_path: str) -> bool:
        completed = subprocess.run(
            ["git", "-C", str(self.repo_root_path), "check-ignore", "--quiet", "--", rel_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.returncode == 0


__all__ = [
    "WorktreeError",
    "WorktreeManager",
    "WorktreeMetadata",
    "WorktreeStatus",
    "slugify_worktree_name",
]
