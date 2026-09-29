from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from juice_agents.core.runner.worktrees import WorktreeError, WorktreeManager, slugify_worktree_name


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "tests@example.com")
    _git(path, "config", "user.name", "Juice Tests")
    (path / ".gitignore").write_text(".juice/\n.env\n", encoding="utf-8")
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", ".gitignore", "tracked.txt")
    _git(path, "commit", "-m", "init")
    return path


def test_slugify_worktree_name_generates_safe_names() -> None:
    assert slugify_worktree_name("Feature / A") == "feature-a"
    assert slugify_worktree_name("", prefix="agent").startswith("agent-")
    with pytest.raises(WorktreeError):
        slugify_worktree_name("////")


def test_worktree_create_list_status_and_remove(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = WorktreeManager(repo)

    status = manager.ensure_worktree("feature-x")

    assert status.slug == "feature-x"
    assert status.branch == "worktree-feature-x"
    assert status.exists is True
    assert Path(status.path).exists()
    assert (Path(status.path) / "tracked.txt").read_text(encoding="utf-8") == "base\n"

    listed = manager.list(active_path=status.path)
    assert [item.slug for item in listed] == ["feature-x"]
    assert listed[0].active is True

    removed = manager.remove("feature-x")
    assert removed.exists is False
    assert not Path(status.path).exists()
    assert "worktree-feature-x" not in _git(repo, "branch", "--format=%(refname:short)").splitlines()


def test_worktree_remove_refuses_dirty_or_ahead_without_discard(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = WorktreeManager(repo)
    status = manager.ensure_worktree("dirty")
    worktree = Path(status.path)

    (worktree / "new.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="dirty working tree"):
        manager.remove("dirty")
    manager.remove("dirty", discard=True)

    ahead = manager.ensure_worktree("ahead")
    ahead_path = Path(ahead.path)
    (ahead_path / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(ahead_path, "add", "tracked.txt")
    _git(ahead_path, "commit", "-m", "ahead")

    with pytest.raises(WorktreeError, match="ahead"):
        manager.remove("ahead")
    manager.remove("ahead", discard=True)


def test_worktreeinclude_copies_only_ignored_matches(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / ".worktreeinclude").write_text(".env\nignored/*.txt\ntracked.txt\n", encoding="utf-8")
    (repo / ".env").write_text("TOKEN=local\n", encoding="utf-8")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "secret.txt").write_text("secret\n", encoding="utf-8")
    with (repo / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write("ignored/\n")
    _git(repo, "add", ".gitignore", ".worktreeinclude")
    _git(repo, "commit", "-m", "include")

    status = WorktreeManager(repo).ensure_worktree("include")
    worktree = Path(status.path)

    assert (worktree / ".env").read_text(encoding="utf-8") == "TOKEN=local\n"
    assert (worktree / "ignored" / "secret.txt").read_text(encoding="utf-8") == "secret\n"
    assert (worktree / "tracked.txt").read_text(encoding="utf-8") == "base\n"
