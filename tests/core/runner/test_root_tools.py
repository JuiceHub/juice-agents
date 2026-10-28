"""Root tool evolution through real Registry, Manager, protocol and checkpoints.

Only model generation is scripted. Each test keeps declarations and runtime
artifacts inside pytest's temporary workspace, with no external provider calls.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from juice_agents.core.registry.agents import registry as agent_registry_module
from juice_agents.core.runner import Runner


TOOL_NAME = "workspace_echo"


class _Model:
    """Retain request snapshots so history cannot masquerade as a tool listing."""

    def __init__(self) -> None:
        self.outputs: list[str] = []
        self.messages: list[Any] = []

    def generate(self, messages: Any, **_kwargs: Any) -> dict[str, str]:
        if not self.outputs:
            raise AssertionError("scripted model outputs exhausted")
        self.messages.append(copy.deepcopy(messages))
        return {"role": "assistant", "content": self.outputs.pop(0)}


def _config(version: int = 1) -> dict[str, Any]:
    return {
        "description": f"Echo text version {version}",
        "inputs": {"text": {"type": "string", "description": "Text to echo", "required": False}},
        "outputs": {"value": {"type": "string", "description": "Versioned text"}},
        "forward": f"def forward(self, text=''):\n    return {{'value': 'v{version}:' + text}}\n",
    }


def _call(agent_type: str, tool_name: str, **args: Any) -> str:
    """Use the real wire protocol; CodeAct tool arguments are always keywords."""

    if agent_type == "codeact":
        source = f"{tool_name}({', '.join(f'{key}={value!r}' for key, value in args.items())})"
        if tool_name == TOOL_NAME:
            source = f"print({source})"
        return f"<thought>run tool</thought><code>{source}</code>"
    return "<thought>run tool</thought><actions>" + json.dumps([{"name": tool_name, "args": args}]) + "</actions>"


def _save(agent_type: str, version: int | None = 1) -> str:
    args: dict[str, Any] = {"action": "save", "name": TOOL_NAME}
    if version is not None:
        args["config"] = _config(version)
    return _call(agent_type, "tool_manage", **args)


def _system_text(messages: list[dict[str, Any]]) -> str:
    """Inspect the current formal system tool list, excluding old observations."""

    parts = []
    for message in messages:
        if message["role"] != "system":
            continue
        content = message["content"]
        parts.append(content if isinstance(content, str) else "\n".join(block.get("text", "") for block in content))
    return "\n".join(parts)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., tuple[Runner, Any, _Model]]]:
    runners: list[Runner] = []

    def create(*, agent_type: str = "react") -> tuple[Runner, Any, _Model]:
        workspace = tmp_path / str(len(runners))
        model = _Model()
        monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: model)
        runner = Runner.create(runner_config="agent", permission_mode="accept", base_dir=workspace)
        runner.root_config = replace(runner.root_config, agent_type=agent_type)
        # The test provider and protocol are properties of the persisted root,
        # exactly like a workspace edit made before the first lazy acquire.
        runner.agent_registry.save_config(runner.agent_registry.load_config("root").copy_with(agent_type=agent_type))
        runners.append(runner)
        return runner, runner._root().instance, model

    yield create
    for runner in runners:
        # Restart tests explicitly close the original Runner before recovery;
        # released managers must not receive a second close request.
        if not runner.async_task_manager._released_manager:
            runner.close()


def _round(runner: Runner, task: str = "create and use the tool") -> tuple[list[Any], list[Any]]:
    events = list(runner.stream(task))
    assert next(event for event in events if event["kind"] == "round_end")["output"] == "done"
    return events, [event["action_step"] for event in events if event["kind"] == "action_step"]


@pytest.mark.parametrize("agent_type", ["react", "codeact"])
def test_root_can_call_saved_tool_on_the_next_step_without_reconstruction(harness, agent_type: str) -> None:
    runner, agent, model = harness(agent_type=agent_type)
    model.outputs = [_save(agent_type), _call(agent_type, TOOL_NAME, text="hello"), _call(agent_type, "submit_output", output="done")]

    _events, steps = _round(runner)

    assert "- workspace_echo: " not in _system_text(model.messages[0])
    assert "- workspace_echo: Echo text version 1" in _system_text(model.messages[1])
    assert "v1:hello" in json.dumps(steps[1].observations)
    assert runner._root().instance is agent
    assert TOOL_NAME in {ref.name for ref in runner.agent_registry.load_config("root").tools}
    assert (runner.config_context.tools_dir / f"{TOOL_NAME}.yaml").is_file()


@pytest.mark.parametrize("agent_type", ["react", "codeact"])
def test_root_unload_removes_prompt_and_callable_then_save_can_rebind(harness, agent_type: str) -> None:
    runner, agent, model = harness(agent_type=agent_type)
    model.outputs = [
        _save(agent_type),
        _call(agent_type, TOOL_NAME, text="first"),
        _call(agent_type, "tool_manage", action="unload", name=TOOL_NAME),
        _call(agent_type, TOOL_NAME, text="blocked"),
        _save(agent_type, version=None),
        _call(agent_type, TOOL_NAME, text="again"),
        _call(agent_type, "submit_output", output="done"),
    ]

    events, steps = _round(runner)

    assert "- workspace_echo: " not in _system_text(model.messages[3])
    assert "- workspace_echo: " in _system_text(model.messages[5])
    observations = json.dumps([step.observations for step in steps])
    assert "v1:first" in observations and "v1:again" in observations
    assert "v1:blocked" not in observations
    assert runner._root().instance is agent
    assert (runner.config_context.tools_dir / f"{TOOL_NAME}.yaml").is_file()


@pytest.mark.parametrize("agent_type", ["react", "codeact"])
def test_saving_existing_tool_refreshes_its_source_and_description(harness, agent_type: str) -> None:
    runner, agent, model = harness(agent_type=agent_type)
    model.outputs = [
        _save(agent_type), _call(agent_type, TOOL_NAME, text="old"),
        _save(agent_type, version=2), _call(agent_type, TOOL_NAME, text="new"),
        _call(agent_type, "submit_output", output="done"),
    ]

    _events, steps = _round(runner)

    assert "v1:old" in json.dumps(steps[1].observations)
    assert "v2:new" in json.dumps(steps[3].observations)
    assert "- workspace_echo: Echo text version 2" in _system_text(model.messages[3])
    assert runner._root().instance is agent


@pytest.mark.parametrize("restart", ["resume", "new_runner"])
def test_persisted_root_tool_is_available_after_restart(harness, restart: str) -> None:
    runner, _agent, model = harness()
    model.outputs = [_save("react"), _call("react", "submit_output", output="done")]
    _round(runner)
    runner_id, workspace = runner.runner_id, runner.config_context.workspace_dir
    runner.close()

    restored = Runner.resume(runner_id=runner_id, base_dir=workspace) if restart == "resume" else Runner.create(runner_config="agent", permission_mode="accept", base_dir=workspace)
    try:
        model.outputs = [_call("react", TOOL_NAME, text="restored"), _call("react", "submit_output", output="done")]
        model.messages.clear()
        _events, steps = _round(restored)
        assert "- workspace_echo: Echo text version 1" in _system_text(model.messages[0])
        assert "v1:restored" in json.dumps(steps[0].observations)
    finally:
        restored.close()


def test_mode_overlays_preserve_root_declaration_and_plan_policy(harness) -> None:
    runner, agent, model = harness()
    model.outputs = [_save("react"), _call("react", "submit_output", output="done")]
    _round(runner)
    root_path = runner.config_context.agents_dir / "root.yaml"
    canonical = root_path.read_text(encoding="utf-8")
    saved_tool = agent.callables[TOOL_NAME]

    for mode_id in ("plan", "team", "agent"):
        runner.reconfigure(mode_id=mode_id)
        assert root_path.read_text(encoding="utf-8") == canonical
        if mode_id == "plan":
            # A persisted custom tool cannot gain write permission merely by
            # surviving a mode switch; both the view and execution policy gate it.
            assert TOOL_NAME not in runner._root().instance.model_visible_callables
            assert runner._check_tool_policy(saved_tool, {})["allowed"] is False

    assert TOOL_NAME in runner._root().instance.model_visible_callables
    assert runner.agent_registry.list_available_agents(name="root", mode_id="agent")["agents"] == []
    with pytest.raises(PermissionError, match="当前 Runner 不允许调用 Agent"):
        runner.invoke_agent({"agent_ref": "root", "task": "cannot dispatch root", "execution": "sync"})
