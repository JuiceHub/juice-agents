"""Examples launch as ordinary files and submit useful default tasks to Juice."""

from __future__ import annotations

import importlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from examples import _runtime
from juice_agents import Juice
from juice_agents.core.agent.builtin.configs import get_general_config
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.agents import registry as registry_module
from tests.support.models import SequenceModel, StaticModel


REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = (
    "agent_research",
    "agent_evolution",
    "group_operations",
    "team_delivery",
)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_direct_file_help_from_external_directory(scenario, tmp_path):
    """A launch must not depend on the repository being the current directory.

    Only the SDK src directory is available through PYTHONPATH. The example
    entry itself must establish its package import context, and parsing help
    must finish before model resolution or workspace-state creation.
    """

    entry = REPO_ROOT / "examples" / scenario / "__main__.py"
    state_dir = entry.parent / ".juice"
    had_state = state_dir.exists()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "sdk" / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, str(entry), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--model" in result.stdout
    assert "--resume" in result.stdout
    assert state_dir.exists() == had_state
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_direct_file_without_arguments_submits_its_demo(monkeypatch, capsys, scenario, tmp_path):
    """Execute the real file entry while replacing only the public SDK boundary.

    The fake accepts the current RunnerManager API rather than arbitrary
    keywords, so removed builder/group/mode arguments cannot slip through.
    No model is loaded and no real workspace files are generated.
    """

    entry = REPO_ROOT / "examples" / scenario / "__main__.py"
    tasks = []

    def stream(task):
        tasks.append(task)
        yield {"kind": "round_end", "outcome": "submitted", "output": "demo complete"}

    runner = SimpleNamespace(
        runner_id="default-demo",
        root_config=get_general_config(),
        config_context=ConfigurationContext.from_workspace(tmp_path),
        _root=lambda: SimpleNamespace(instance=SimpleNamespace(tools={})),
        stream=stream,
        stop=Mock(),
    )
    create_calls = []

    def create(*, permission_mode="default", runner_config=None, team_name=None):
        create_calls.append((permission_mode, runner_config, team_name))
        return runner

    juice = SimpleNamespace(runners=SimpleNamespace(create=create, resume=Mock()))
    captured_workspaces = []

    def juice_from_args(_args, *, workspace, **_kwargs):
        captured_workspaces.append(workspace)
        return juice

    monkeypatch.setattr(_runtime, "_juice_from_args", juice_from_args)
    monkeypatch.setattr(sys, "argv", [str(entry)])
    namespace = runpy.run_path(str(entry), run_name="__main__")

    default_task = namespace["DEFAULT_TASK"]
    expected_task = (
        f"$workflow:deep_research {default_task}"
        if scenario == "agent_research" else default_task
    )
    assert tasks == [expected_task]
    assert captured_workspaces == [entry.parent]
    assert create_calls[0][1] == {
        "agent_research": "agent",
        "agent_evolution": "agent",
        "group_operations": "group",
        "team_delivery": "team",
    }[scenario]
    juice.runners.resume.assert_not_called()
    runner.stop.assert_called_once()
    assert "demo complete" in capsys.readouterr().out

    # Fixed procedures belong to the system declaration, while the dynamic
    # work order remains in the stream task. This also covers resumed policy
    # setup through the same configure callback in shared runtime tests.
    if scenario == "agent_evolution":
        assert namespace["EVOLUTION_SYSTEM_INSTRUCTIONS"] in runner.root_config.instructions
    if scenario == "group_operations":
        assert namespace["OPERATIONS_SYSTEM_INSTRUCTIONS"] in runner.root_config.instructions
    assert default_task not in runner.root_config.instructions


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_real_sdk_initializes_scenario_policy_and_tools(monkeypatch, tmp_path, scenario):
    """Only the provider is fake; exercise SDK, Registry, Runner and lazy root."""

    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: StaticModel())
    module = importlib.import_module(f"examples.{scenario}.__main__")
    mode = {"group_operations": "group", "team_delivery": "team"}.get(scenario, "agent")
    runner = Juice(workspace=tmp_path).runners.create(runner_config=mode)
    try:
        configure = {
            "agent_research": "configure_research_runner",
            "agent_evolution": "configure_evolution_runner",
            "group_operations": "configure_operations_runner",
        }.get(scenario)
        if configure:
            getattr(module, configure)(runner)
        root = runner._root().instance
        assert root.instructions == runner.root_config.instructions
        if scenario == "agent_research":
            assert root.tools["workflow_run"].workflow_name == "deep_research"
            assert root._manual_workflow_authorization == "deep_research"
            assert "workflow_list" not in root.tools
        elif scenario == "agent_evolution":
            assert {"tool_manage", "agent_manage", "agent_tool"} <= root.tools.keys()
        elif scenario == "group_operations":
            assert "agent_tool" in root.tools
            assert runner.is_agent_available("general_worker")
            assert runner.is_agent_available("review_worker")
        else:
            assert runner.team_name == "default"
            assert runner.team_manager is not None
            assert {"team_task_create", "team_member_create", "team_finish"} <= root.tools.keys()
    finally:
        runner.stop()


def test_research_demo_runs_selected_workflow_with_automatic_workflows_disabled(monkeypatch, tmp_path):
    """Run the full built-in workflow with deterministic provider responses.

    This catches the SDK/gateway distinction: a `$workflow` string alone does
    not install workflow_run in Runner.stream. Actual scoped authorization,
    local-file reading, ephemeral researchers and artifact persistence run here.
    """

    module = importlib.import_module("examples.agent_research.__main__")
    source = tmp_path / "__main__.py"
    source.write_text(Path(module.__file__).read_text(), encoding="utf-8")

    def call(name, args):
        actions = json.dumps([{"name": name, "args": args}], ensure_ascii=False)
        return f"<thought>demo</thought><actions>{actions}</actions>"

    def submit(output):
        return call("submit_output", {"output": output})

    model = SequenceModel([
        call("workflow_run", {"name": "deep_research", "args": {
            "question": module.DEFAULT_TASK,
            "source_mode": "workspace",
            "limits": {"max_research_rounds": 1, "max_concurrent_research_units": 1},
        }}),
        submit({"objective": "Inspect example parser", "complexity": "simple"}),
        submit({"topics": [{"question": "What local file is the evidence?", "source_mode": "workspace"}]}),
        call("read", {"path": str(source)}),
        submit({"sources": [{
            "id": "entry", "title": "Example source", "location": str(source),
            "source_type": "workspace", "authority": True, "independence_key": "example",
            "excerpt": "parser.add_argument",
        }], "claims": [{"text": "The example defines command-line arguments.", "source_ids": ["entry"], "confidence": 1.0}]}),
        submit({"report_markdown": "The example defines command-line arguments. [entry]"}),
        submit("research complete"),
    ])
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: model)
    monkeypatch.setattr(module, "showcase_workspace", lambda _entry: tmp_path)

    assert module.main([]) == "research complete"
    assert model.outputs == []
    reports = list(tmp_path.glob(".juice/runners/*/workflows/*/report.md"))
    assert len(reports) == 1
    assert "[entry]" in reports[0].read_text()
    debug = json.loads((reports[0].parent / "debug.json").read_text())
    assert debug["source_mode"] == "workspace"
