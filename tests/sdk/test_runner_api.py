"""The public Runner entry point preserves sessions, configuration and UI binding."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from juice_agents import Runner
from juice_agents.core.registry.agents import registry as registry_module


class _ScriptedModel:
    """Replace provider generation while retaining real Agents, tools and storage."""

    def __init__(self, actions: list[dict[str, Any]]) -> None:
        self.actions = list(actions)
        self.messages: list[list[dict[str, Any]]] = []

    def generate(self, messages: list[dict[str, Any]], **_kwargs: Any) -> dict[str, str]:
        self.messages.append(messages)
        assert self.actions, "The Agent requested an unexpected model step."
        action = self.actions.pop(0)
        return {
            "role": "assistant",
            "content": f"<thought>execute</thought><actions>{json.dumps([action])}</actions>",
        }


def _submit(output: str) -> dict[str, Any]:
    return {"name": "submit_output", "args": {"output": output}}


@pytest.mark.parametrize("mode_id", ["agent", "plan", "group", "team"])
def test_public_runner_runs_and_resumes_with_explicit_runtime_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode_id: str,
) -> None:
    """Removing facade defaults must preserve workspace and explicit config inputs."""

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    unrelated = tmp_path / "caller"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    workspace = tmp_path / "project"
    workspace.mkdir()
    runtime_config = tmp_path / "runtime.yaml"
    runtime_config.write_text(
        "runtime:\n  model_name: file_model\n  model_effort: low\nskills:\n  enabled: false\n",
        encoding="utf-8",
    )
    original_config = runtime_config.read_bytes()
    overrides = {"runtime": {"model_name": "override_model"}}
    resolved: list[dict[str, Any]] = []
    models: list[_ScriptedModel] = []

    def resolve_model(**kwargs: Any) -> _ScriptedModel:
        resolved.append(kwargs)
        model = _ScriptedModel([_submit("first-output" if not models else "second-output")])
        models.append(model)
        return model

    monkeypatch.setattr(registry_module, "resolve_runtime_model", resolve_model)
    options = {
        "runtime_config_path": runtime_config,
        "runtime_overrides": overrides,
    }
    runner = Runner.create(base_dir=workspace, runner_config=mode_id, permission_mode="accept", **options)
    runner_id = runner.runner_id
    try:
        assert runner.run("first-task") == "first-output"
    finally:
        runner.close()

    resumed = Runner.resume(base_dir=workspace, runner_id=runner_id, **options)
    try:
        assert resumed.runner_id == runner_id
        assert resumed.mode_id == mode_id
        assert resumed.base_dir == workspace
        assert resumed.runtime_config_path == runtime_config
        assert resumed.config_context.overrides == overrides
        assert resumed.config_context.read_merged_config()["skills"]["enabled"] is False
        assert resumed.run("second-task") == "second-output"
        # The second request reaches a freshly constructed Agent with the first
        # round's user message and result restored from its persisted session.
        history = json.dumps(models[1].messages[0], ensure_ascii=False)
        assert "first-task" in history and "first-output" in history
        assert resumed.layout.root_dir.is_relative_to(workspace / ".juice")
    finally:
        resumed.close()

    assert len(resolved) == 2
    for request in resolved:
        assert request["model_name"] == "override_model"
        assert request["model_effort"] == "low"
        assert request["workspace_dir"] == workspace
        assert Path(request["runtime_config_path"]) == runtime_config
        assert request["runtime_overrides"] == overrides
    assert runtime_config.read_bytes() == original_config
    assert not (workspace / ".juice" / "config.yaml").exists()
    assert not (unrelated / ".juice").exists()


def test_public_runner_rebinds_real_ask_tool_after_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callbacks remain process-local and bind to real tools on each new Runner."""

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    models: list[_ScriptedModel] = []

    def resolve_model(**_kwargs: Any) -> _ScriptedModel:
        model = _ScriptedModel([
            {"name": "ask", "args": {"question": "Choose a result.", "options": ["continue"]}},
            _submit("done"),
        ])
        models.append(model)
        return model

    monkeypatch.setattr(registry_module, "resolve_runtime_model", resolve_model)
    requests: list[dict[str, Any]] = []

    def handler(answer: str):
        def respond(request: dict[str, Any]) -> dict[str, Any]:
            requests.append(request)
            return {"status": "answered", "selected": ["continue"], "custom_response": answer}

        return respond

    runner = Runner.create(base_dir=tmp_path, runner_config="agent")
    runner_id = runner.runner_id
    try:
        # Ask is an optional root tool. Bind its existing registry declaration
        # before lazy Agent acquisition, then exercise the actual execution path.
        runner.agent_registry.bind_tool_ref("root", "ask")
        runner.set_ask_handler(handler("created-answer"))
        assert runner.run("first-task") == "done"
        assert len(requests) == 1
        assert "created-answer" in json.dumps(models[0].messages[1])
    finally:
        runner.close()

    resumed = Runner.resume(runner_id=runner_id, base_dir=tmp_path)
    try:
        assert resumed.ask_user({"question": "unbound"})["status"] == "error"
        resumed.set_ask_handler(handler("resumed-answer"))
        assert resumed.run("second-task") == "done"
        assert len(requests) == 2
        assert "resumed-answer" in json.dumps(models[1].messages[1])
        resumed.set_ask_handler(None)
        assert resumed.ask_user({"question": "unbound"})["status"] == "error"
    finally:
        resumed.close()

    assert {request["question"] for request in requests} == {"Choose a result."}
    assert requests[0]["request_id"] != requests[1]["request_id"]
    assert requests[0]["options"] == [{"label": "continue", "value": "continue", "description": ""}]
