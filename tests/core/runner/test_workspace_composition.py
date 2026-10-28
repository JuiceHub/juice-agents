"""Workspace and system rules propagate through real Manager composition."""

from pathlib import Path
import json
import threading

import pytest

from juice_agents import Juice
from juice_agents.core.config.runtime_config import read_workspace_config
from juice_agents.core.registry.agents import registry as registry_module
from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.runner import Runner
from juice_agents.core.runner.errors import RunnerBusyError


def _call(name, **args):
    return "<thought>execute</thought><actions>" + json.dumps([{"name": name, "args": args}]) + "</actions>"


class _ScopedModel:
    """Only generation is replaced: tools, rules and dispatch remain real."""

    def __init__(self, scope):
        self.scope = scope
        self.outputs = []
        self.calls = 0
        self.lock = threading.Lock()

    def generate(self, messages, **kwargs):
        system = str([msg["content"] for msg in messages if msg["role"] == "system"])
        assert f"project-rule-{self.scope}" in system
        assert f"project-rule-{1 - self.scope}" not in system
        with self.lock:
            self.calls += 1
            assert self.outputs, "script exhausted"
            return {"role": "assistant", "content": self.outputs.pop(0)}


@pytest.mark.parametrize("kind", ["child", "workflow", "team"])
def test_managed_work_uses_project_tools_rules_and_state(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    unrelated = tmp_path / "framework cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    runners = []
    models = []
    models_by_workspace = {}
    monkeypatch.setattr(
        registry_module, "resolve_runtime_model",
        lambda **kwargs: models_by_workspace[Path(kwargs["workspace_dir"])],
    )
    try:
        for scope in range(2):
            workspace = tmp_path / f"项目 {scope}"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text(f"project-rule-{scope}", encoding="utf-8")
            model = _ScopedModel(scope)
            models_by_workspace[workspace] = model
            runner = Runner.create(base_dir=workspace, runner_config="team" if kind == "team" else "agent", permission_mode="accept")
            runner._root()
            runners.append(runner)
            models.append(model)

        # Run after constructing both projects so shared globals cannot silently
        # select whichever project happened to initialize most recently.
        for scope, (runner, model) in enumerate(zip(runners, models)):
            workspace = runner.base_dir
            model.outputs = [
                _call("write", path="result.txt", content=f"result-{scope}"),
                _call("submit_output", output="done"),
            ]
            if kind == "child":
                result = runner.invoke_agent({
                    "agent_ref": AgentConfig.from_dict({"name": "local_writer", "instructions": "Write the result.", "tools": ["write"]}),
                    "task": "write result", "execution": "sync", "ephemeral": True,
                })
                assert result["output"] == "done"
            elif kind == "workflow":
                folder = workspace / ".juice/workflows"
                folder.mkdir(parents=True)
                (folder / "writer.py").write_text(
                    "WORKFLOW_METADATA = {'name': 'writer', 'read_only': False, 'tools': ['write']}\n"
                    "def run(ctx, args):\n"
                    "    return ctx.agent('write result', instructions='Write result.', tools=['write'])\n",
                    encoding="utf-8",
                )
                assert runner.workflow_manager.call("writer", {}, owner_agent_name="root")["result"] == "done"
            else:
                runner.create_team_member("writer", config={"instructions": "Write result.", "tools": ["write"]})
                task = runner.team_manager.add_task("Write result", eligible_members=["writer"])
                assert runner.async_task_manager.wait_active_async_tasks(timeout_seconds=5) == []
                saved = next(item for item in runner.team_manager.list_tasks() if item["task_id"] == task["task_id"])
                assert saved["status"] == "completed", saved
            assert (workspace / "result.txt").read_text(encoding="utf-8") == f"result-{scope}"
            assert model.calls == 2
            assert runner.layout.root_dir.is_relative_to(workspace / ".juice")
        assert not (unrelated / "result.txt").exists()
        assert Path.cwd() == unrelated
    finally:
        for runner in runners:
            runner.close()


def test_runtime_override_reconfiguration_preserves_session_and_team_board(tmp_path, monkeypatch):
    """Transient choices update real fresh Agents without writing project YAML."""

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    models = {}

    class Model:
        def __init__(self, name):
            self.name = name

        def generate(self, messages, **kwargs):
            return {"role": "assistant", "content": _call("submit_output", output=self.name)}

    def resolve(**kwargs):
        name = kwargs["model_name"]
        return models.setdefault(name, Model(name))

    monkeypatch.setattr(registry_module, "resolve_runtime_model", resolve)
    client = Juice(workspace=tmp_path)
    runner = client.runners.create(runner_config="team", permission_mode="accept", runtime_overrides={"runtime": {"model_name": "first"}})
    try:
        assert runner.run("first task") == "first"
        old_context = runner._root().instance.context
        old_root = runner._root().instance
        config_context = runner.config_context
        with pytest.raises(ValueError, match="agent_type"):
            runner.reconfigure(runtime_overrides={"runtime": {"agent_type": "bogus"}})
        assert runner._root().instance is old_root
        assert runner.config_context is config_context
        # A request can hold the gate before its first generator step. It must
        # still prevent model/protocol replacement at that boundary.
        stream = runner.stream("reserved request")
        try:
            with pytest.raises(RunnerBusyError):
                runner.reconfigure(runtime_overrides={"runtime": {"model_name": "blocked"}})
        finally:
            stream.close()
        assert runner._root().instance is old_root
        runner_id = runner.runner_id
        team_run = runner.state["team_run_id"]
        board = runner.team_manager
        runner.reconfigure(runtime_overrides={"runtime": {"model_name": "second"}})
        assert runner.runner_id == runner_id
        assert runner.state["team_run_id"] == team_run
        assert runner.team_manager is board
        assert runner.run("second task") == "second"
        context = runner._root().instance.context
        assert len(context.nodes) > len(old_context.nodes)
        assert not read_workspace_config(tmp_path)
    finally:
        runner.close()
    resumed = client.runners.resume(runner_id=runner_id, runtime_overrides={"runtime": {"model_name": "third"}})
    try:
        assert resumed.run("third task") == "third"
        assert resumed.runner_id == runner_id
    finally:
        resumed.close()
