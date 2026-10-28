"""Workspace tools must not depend on the framework process working directory."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from juice_agents.core.agent.agents import _RegistryLazyTool
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.skills import SkillRegistry
from juice_agents.core.registry.tools import ToolRegistry


def test_tool_construction_and_relative_shell_workdir_use_workspace(tmp_path, monkeypatch):
    framework = tmp_path / "framework"
    project = tmp_path / "项目 with spaces"
    framework.mkdir()
    (project / "nested").mkdir(parents=True)
    (project / "source.txt").write_text("project", encoding="utf-8")
    monkeypatch.chdir(framework)
    registry = ToolRegistry(config_context=ConfigurationContext.from_workspace(project))

    assert "project" in json.dumps(registry.instantiate("read")(path="source.txt"))
    registry.instantiate("write")(path="created.txt", content="new")
    assert (project / "created.txt").read_text() == "new"
    shell = registry.instantiate("shell")
    assert Path(shell(command="pwd")["stdout"]) == project
    assert Path(shell(command="pwd", workdir="nested")["stdout"]) == project / "nested"
    assert not (framework / "created.txt").exists()


def test_background_shell_resolves_relative_workdir_before_dispatch(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "nested").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    received = {}

    def launch(**kwargs):
        received.update(kwargs)
        return {"status": "launched"}

    tool = ToolRegistry(config_context=ConfigurationContext.from_workspace(project)).instantiate("shell")
    tool.bind_owner_agent(SimpleNamespace(runner_context=SimpleNamespace(launch_local_bash=launch)))
    assert tool(command="pwd", workdir="nested", background=True)["status"] == "launched"
    assert Path(received["cwd"]) == project / "nested"


def test_runtime_added_builtin_preserves_owner_configuration_context(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "value.txt").write_text("owner project", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    context = ConfigurationContext.from_workspace(project)
    lazy = _RegistryLazyTool(tool_name="read", tool_config_dir=context.tools_dir)
    lazy.bind_owner_agent(SimpleNamespace(name="worker", _declared_config_context=context))
    assert "owner project" in json.dumps(lazy(path="value.txt"))


def test_runtime_added_generated_tool_receives_same_context(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    context = ConfigurationContext.from_workspace(project, overrides={"runtime": {"agent_type": "react"}})
    ToolRegistry(config_context=context).save_config({
        "name": "context_probe",
        "description": "Report the generated tool's project context",
        "inputs": {},
        "outputs": {"workspace": {"type": "string", "description": "Project directory"}},
        "forward": "def forward(self):\n    return {'workspace': str(self.config_context.workspace_dir)}\n",
    })
    tool = _RegistryLazyTool(tool_name="context_probe", tool_config_dir=context.tools_dir)
    tool.bind_owner_agent(SimpleNamespace(name="worker", _declared_config_context=context))
    assert tool()["workspace"] == str(project)


def test_skills_explicit_workspace_is_authority_for_local_and_external_roots(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    registry = SkillRegistry(workspace_dir=project)
    assert registry.local_dir == project / ".juice" / "skills"
    assert project / ".agents" / "skills" in registry.external_dirs
    assert tmp_path / ".agents" / "skills" not in registry.external_dirs


def test_custom_skill_storage_does_not_infer_project_from_arbitrary_parents(tmp_path):
    project = tmp_path / "project"
    registry = SkillRegistry(workspace_dir=project, local_dir=tmp_path / "cache" / "skills")
    assert project / ".agents" / "skills" in registry.external_dirs
    assert tmp_path / ".agents" / "skills" not in registry.external_dirs


def test_configured_relative_skill_source_resolves_from_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    context = ConfigurationContext.from_workspace(project, overrides={"skills": {"external_dirs": ["shared_skills"]}})
    registry = SkillRegistry(config_context=context)
    assert project / "shared_skills" in registry.external_dirs
    assert tmp_path / "shared_skills" not in registry.external_dirs


@pytest.mark.parametrize("code", [
    "with open('plain.txt', 'w') as handle:\n    handle.write('hello')\nprint(open('plain.txt').read())",
    "from pathlib import Path\nPath('plain.txt').write_text('hello')\nprint(Path('plain.txt').read_text())",
    "import os\nprint(os.getcwd())\nprint(os.listdir('.'))",
    "import subprocess\nprint(subprocess.check_output(['pwd'], text=True).strip())",
    "from pathlib import Path\nprint(Path.cwd())",
    "import subprocess\nprint(subprocess.getoutput('pwd'))",
    "import os\nos.makedirs(name='nested')\nos.chdir('nested')\nprint(os.getcwd())",
])
def test_python_tool_plain_relative_paths_use_workspace_without_chdir(tmp_path, monkeypatch, code):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    tool = ToolRegistry(config_context=ConfigurationContext.from_workspace(project)).instantiate("python")
    result = tool(code=code)
    assert result["error"] is None, result
    if "plain.txt" in code:
        assert (project / "plain.txt").read_text() == "hello"
        assert "hello" in result["logs"]
    else:
        assert str(project) in result["logs"]
    assert Path.cwd() == tmp_path
    assert not (tmp_path / "plain.txt").exists()
