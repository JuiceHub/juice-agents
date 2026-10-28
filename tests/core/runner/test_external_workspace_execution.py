"""Offline end-to-end execution and recovery from outside the project."""

from __future__ import annotations

import ast
import json
import shlex
import sys
from pathlib import Path

import pytest

from juice_agents.core.registry.agents import registry as agent_registry_module
from juice_agents.core.runner import Runner


class ScriptedModel:
    """Only generation is replaced; tools and persistence run normally."""

    def __init__(self):
        self.outputs = []

    def generate(self, _messages, **_kwargs):
        assert self.outputs, "Scripted model exhausted before task completion"
        return {"role": "assistant", "content": self.outputs.pop(0)}


def action(protocol, name, **args):
    if protocol == "codeact":
        call = f"{name}({', '.join(f'{key}={value!r}' for key, value in args.items())})"
        if name != "submit_output":
            call = f"print({call})"
        return f"<thought>Execute project task.</thought><code>{call}</code>"
    return "<thought>Execute project task.</thought><actions>" + json.dumps([
        {"name": name, "args": args},
    ]) + "</actions>"


@pytest.mark.parametrize("protocol", ["react", "codeact"])
def test_two_projects_read_edit_test_exit_and_resume(tmp_path, monkeypatch, protocol):
    framework = tmp_path / "framework cwd"
    framework.mkdir()
    monkeypatch.chdir(framework)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    models = {}
    monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **kwargs: models[Path(kwargs["workspace_dir"])])
    projects = [tmp_path / "first 项目", tmp_path / "second project"]
    sessions = []

    for index, project in enumerate(projects):
        project.mkdir()
        initial, expected = index + 1, index + 10
        (project / "calculation.py").write_text(f"VALUE = {initial}\n", encoding="utf-8")
        (project / "test_project.py").write_text(
            "import unittest\nfrom calculation import VALUE\n"
            f"class ProjectTest(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(VALUE, {expected})\n",
            encoding="utf-8",
        )
        skill = project / ".agents" / "skills" / f"project-{index}" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(f"---\nname: project-{index}\ndescription: Project {index} instructions\n---\nUse this project.\n", encoding="utf-8")
        model = models[project] = ScriptedModel()
        runner = Runner.create(runner_config="agent", permission_mode="accept", base_dir=project)
        runner.agent_registry.save_config(runner.agent_registry.load_config("root").copy_with(agent_type=protocol))
        model.outputs = [
            action(protocol, "read", path="calculation.py"),
            action(protocol, "edit", path="calculation.py", edits=[{
                "type": "replace", "old": f"VALUE = {initial}", "new": f"VALUE = {expected}",
            }]),
            action(protocol, "shell", command=f"{shlex.quote(sys.executable)} -B -m unittest -v test_project"),
            action(protocol, "submit_output", output="updated and verified"),
        ]
        try:
            events = list(runner.stream("Read calculation.py, update VALUE and run its tests."))
            assert next(event for event in events if event["kind"] == "round_end")["output"] == "updated and verified"
            observations = [event["action_step"].observations for event in events if event["kind"] == "action_step"]
            shell_result = ast.literal_eval(observations[2][0].removeprefix("[logs]\n"))
            assert shell_result["returncode"] == 0
            assert "OK" in shell_result["stderr"]
            assert (project / "calculation.py").read_text() == f"VALUE = {expected}\n"
            root = runner._root().instance
            assert root.skill_registry.local_dir == project / ".juice" / "skills"
            assert f"project-{index}" in {meta["name"] for meta in root.skills_metadata}
            assert f"project-{1 - index}" not in {meta["name"] for meta in root.skills_metadata}
            declaration = (project / ".juice" / "agents" / "root.yaml").read_text()
            assert str(project) not in declaration
            sessions.append((project, runner.runner_id, expected))
        finally:
            runner.close()

    for project, runner_id, expected in sessions:
        restored = Runner.resume(runner_id=runner_id, base_dir=project)
        model = models[project]
        model.outputs = [
            action(protocol, "read", path="calculation.py"),
            action(protocol, "submit_output", output="restored"),
        ]
        try:
            events = list(restored.stream("Read the current project value after resuming."))
            observations = [event["action_step"].observations for event in events if event["kind"] == "action_step"]
            assert f"VALUE = {expected}" in json.dumps(observations)
            assert next(event for event in events if event["kind"] == "round_end")["output"] == "restored"
            if protocol == "codeact":
                result = restored._root().instance.executor("print(open('calculation.py').read())")
                assert result.error is None
                assert f"VALUE = {expected}" in result.logs
        finally:
            restored.close()

    assert Path.cwd() == framework
    assert list(framework.iterdir()) == []
