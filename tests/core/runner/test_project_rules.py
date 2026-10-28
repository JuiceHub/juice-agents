"""Project instructions are scoped, bounded and replaced on session recovery."""

from pathlib import Path
import subprocess

import pytest

from juice_agents.core.registry.agents import registry as registry_module
from juice_agents.core.runner import Runner
from juice_agents.core.runner.project_rules import load_workspace_rules


@pytest.fixture(autouse=True)
def _git_boundary(tmp_path, monkeypatch):
    # Test temp files live under this repository. Stop Git from inheriting the
    # framework's .git when a case intentionally creates a non-Git workspace.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_rules_follow_git_root_to_start_directory_and_override(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    monkeypatch.setenv("HOME", str(home))
    _write(home / ".juice/AGENTS.md", "user convention")
    _write(tmp_path / "AGENTS.md", "outside repository")
    _write(repo / "AGENTS.md", "root convention")
    _write(repo / "pkg/AGENTS.md", "shadowed convention")
    _write(repo / "pkg/AGENTS.override.md", "package convention")
    _write(repo / "pkg/src/AGENTS.override.md", " \n")
    _write(repo / "pkg/src/AGENTS.md", "source convention")
    _write(repo / "pkg/src/deeper/AGENTS.md", "not loaded yet")

    rules = load_workspace_rules(repo / "pkg/src")

    assert [doc.source.name for doc in rules.documents] == [
        "AGENTS.md", "AGENTS.md", "AGENTS.override.md", "AGENTS.md",
    ]
    assert rules.prompt.index("user convention") < rules.prompt.index("root convention")
    assert rules.prompt.index("root convention") < rules.prompt.index("package convention")
    assert rules.prompt.index("package convention") < rules.prompt.index("source convention")
    assert "shadowed convention" not in rules.prompt
    assert "outside repository" not in rules.prompt
    assert "not loaded yet" not in rules.prompt
    assert str(repo / "pkg") in rules.prompt
    assert "before editing" in rules.prompt.lower()


def test_non_git_rules_do_not_inherit_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write(tmp_path / "AGENTS.md", "unrelated parent")
    workspace = tmp_path / "project"
    _write(workspace / "AGENTS.md", "local convention")
    rules = load_workspace_rules(workspace)
    assert len(rules.documents) == 1
    assert "local convention" in rules.prompt
    assert "unrelated parent" not in rules.prompt


def test_rules_bound_utf8_bytes_and_report_truncation(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write(tmp_path / "AGENTS.md", "中文" * 20_000)
    rules = load_workspace_rules(tmp_path)
    assert len(rules.prompt.encode("utf-8")) <= 32 * 1024
    assert "�" not in rules.prompt
    assert rules.diagnostics and "truncat" in caplog.text.lower()
    assert str(tmp_path / "AGENTS.md") in caplog.text


def test_git_worktree_uses_its_own_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid", "commit", "--allow-empty", "-m", "initial")
    worktree = tmp_path / "linked tree"
    _git(repo, "worktree", "add", "-b", "test-workspace", str(worktree))
    _write(repo / "AGENTS.md", "original root")
    _write(worktree / "AGENTS.md", "worktree root")
    nested = worktree / "src"
    nested.mkdir()
    rules = load_workspace_rules(nested)
    assert "worktree root" in rules.prompt
    assert "original root" not in rules.prompt


class _Model:
    def generate(self, messages, **kwargs):
        return {"role": "assistant", "content": '<thought>done</thought><actions>[{"name":"submit_output","args":{"output":"done"}}]</actions>'}


@pytest.mark.parametrize("agent_type", ["react", "codeact"])
def test_resume_replaces_rules_in_system_prompt_and_preserves_role(tmp_path, monkeypatch, agent_type):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "project"
    rules_file = workspace / "AGENTS.md"
    _write(rules_file, "old project convention")
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **kwargs: _Model())
    runner = Runner.create(base_dir=workspace, permission_mode="accept")
    declaration = runner.agent_registry.load_config("root").copy_with(
        agent_type=agent_type, system_prompt="Role: specialist reviewer.",
    )
    runner.agent_registry.save_config(declaration)
    runner._refresh_root_config()
    try:
        agent = runner._root().instance
        assert "Role: specialist reviewer." in agent.system_prompt
        assert "old project convention" in agent.context.system_prompt
        assert "old project convention" not in (agent.instructions or "")
        runner_id = runner.runner_id
    finally:
        runner.close()

    _write(rules_file, "new project convention")
    restored = Runner.resume(base_dir=workspace, runner_id=runner_id)
    try:
        agent = restored._root().instance
        assert "Role: specialist reviewer." in agent.system_prompt
        assert "old project convention" not in agent.context.system_prompt
        assert agent.context.system_prompt.count("new project convention") == 1
        agent.refresh_system_prompt()
        assert agent.context.system_prompt.count("new project convention") == 1
        system = [msg for msg in agent.context.to_messages() if msg["role"] == "system"]
        assert "new project convention" in str(system)
    finally:
        restored.close()
