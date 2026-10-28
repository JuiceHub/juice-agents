"""Agent evolution must update a live root's prompt and real dispatch path.

Only provider generation is scripted. Registry writes, both root protocols,
ToolManager dispatch and background child execution use their real components.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from juice_agents.core.agent.tools.builtin.agents.subagents_tools import AgentTool
from juice_agents.core.agent.tools.builtin.agents.agents_tools import AgentsListTool
from juice_agents.core.agent.tools.builtin.evolution.agent_manage import AgentManageTool
from juice_agents.core.registry.agents import registry as registry_module
from juice_agents.core.runner import Runner


WORKER_NAME = "workspace_worker"
CHILD_INSTRUCTIONS = "Return the dedicated child regression result."
CHILD_OUTPUT = "worker completed"


def _call(agent_type: str, tool_name: str, **args: Any) -> str:
    """Exercise public protocol syntax, including CodeAct's callable bridge."""

    if agent_type == "codeact":
        source = f"{tool_name}({', '.join(f'{key}={value!r}' for key, value in args.items())})"
        if tool_name != "submit_output":
            source = f"print({source})"
        return f"<thought>run tool</thought><code>{source}</code>"
    actions = json.dumps([{"name": tool_name, "args": args}])
    return f"<thought>run tool</thought><actions>{actions}</actions>"


def _system_text(messages: list[dict[str, Any]]) -> str:
    """Inspect formal system messages without matching previous tool results."""

    parts: list[str] = []
    for message in messages:
        if message["role"] != "system":
            continue
        content = message["content"]
        parts.append(content if isinstance(content, str) else "\n".join(block.get("text", "") for block in content))
    return "\n".join(parts)


class _Model:
    def __init__(self) -> None:
        self.outputs: list[str] = []
        self.messages: list[Any] = []
        self.child_calls = 0

    def generate(self, messages: Any, **_kwargs: Any) -> dict[str, str]:
        # Child execution runs on another thread and inherits root's model.
        # Route by child-only instructions so it cannot consume root's queue.
        if CHILD_INSTRUCTIONS in _system_text(messages):
            self.child_calls += 1
            return {"role": "assistant", "content": _call("react", "submit_output", output=CHILD_OUTPUT)}
        if not self.outputs:
            raise AssertionError("scripted root outputs exhausted")
        self.messages.append(copy.deepcopy(messages))
        return {"role": "assistant", "content": self.outputs.pop(0)}


def _worker_config(version: int = 1, *, allowed_modes: list[str] | None = None) -> dict[str, Any]:
    return {
        "agent_type": "react",
        "description": f"Workspace worker version {version}",
        "instructions": CHILD_INSTRUCTIONS,
        "allowed_modes": allowed_modes,
        "tools": [],
    }


def _save(agent_type: str, *, version: int = 1) -> str:
    return _call(agent_type, "agent_manage", action="save", name=WORKER_NAME, config=_worker_config(version))


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., tuple[Runner, Any, _Model]]]:
    runners: list[Runner] = []

    def create(*, agent_type: str = "react", mode_id: str = "agent", workspace: Path | None = None) -> tuple[Runner, Any, _Model]:
        model = _Model()
        monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: model)
        runner = Runner.create(
            runner_config=mode_id,
            permission_mode="accept",
            base_dir=workspace if workspace is not None else tmp_path / str(len(runners)),
        )
        runners.append(runner)
        # Persist protocol selection before the root's first lazy acquisition.
        runner.agent_registry.save_config(runner.agent_registry.load_config("root").copy_with(agent_type=agent_type))
        runner._refresh_root_config()
        return runner, runner._root().instance, model

    yield create
    for runner in runners:
        runner.close()


def _round(runner: Runner, model: _Model, outputs: list[str]) -> list[Any]:
    model.outputs = outputs
    events = list(runner.stream("exercise agent evolution"))
    assert next(event for event in events if event["kind"] == "round_end")["output"] == "done"
    return [event["action_step"] for event in events if event["kind"] == "action_step"]


def _assert_completed_children(runner: Runner, count: int) -> None:
    """Join actual task futures before checking durable results and teardown."""

    assert runner.async_task_manager.wait_active_async_tasks(timeout_seconds=3) == []
    tasks = [task for task in runner.async_task_manager.list_tasks() if task["metadata"].get("target_agent_name") == WORKER_NAME]
    assert len(tasks) == count
    for task in tasks:
        assert task["status"] == "completed", task
        assert runner.async_task_manager.read_output(task["async_task_id"])["latest_result"] == CHILD_OUTPUT


@pytest.mark.parametrize("agent_type", ["react", "codeact"])
def test_root_can_call_saved_agent_on_next_step_without_reconstruction(harness, agent_type: str) -> None:
    runner, root, model = harness(agent_type=agent_type)

    steps = _round(runner, model, [
        _save(agent_type),
        _call(agent_type, "agent_tool", name=WORKER_NAME, task="return the child result"),
        _call(agent_type, "submit_output", output="done"),
    ])

    _assert_completed_children(runner, 1)
    assert WORKER_NAME not in _system_text(model.messages[0])
    assert f"{WORKER_NAME}: Workspace worker version 1" in _system_text(model.messages[1])
    assert "async_task_id" in json.dumps(steps[1].observations)
    assert model.child_calls == 1
    assert runner._root().instance is root
    assert WORKER_NAME in root.available_managed_agent_names
    assert (runner.config_context.agents_dir / f"{WORKER_NAME}.yaml").is_file()


@pytest.mark.parametrize("agent_type", ["react", "codeact"])
def test_saving_existing_agent_refreshes_its_description(harness, agent_type: str) -> None:
    runner, root, model = harness(agent_type=agent_type)

    _round(runner, model, [
        _save(agent_type),
        _save(agent_type, version=2),
        _call(agent_type, "submit_output", output="done"),
    ])

    assert f"{WORKER_NAME}: Workspace worker version 1" in _system_text(model.messages[1])
    assert f"{WORKER_NAME}: Workspace worker version 2" in _system_text(model.messages[2])
    assert f"{WORKER_NAME}: Workspace worker version 1" not in _system_text(model.messages[2])
    assert runner._root().instance is root


@pytest.mark.parametrize("agent_type", ["react", "codeact"])
def test_root_unload_hides_agent_then_save_restores_dispatch(harness, agent_type: str) -> None:
    runner, root, model = harness(agent_type=agent_type)

    _round(runner, model, [
        _save(agent_type),
        _call(agent_type, "agent_tool", name=WORKER_NAME, task="before unload"),
        _call(agent_type, "agent_manage", action="unload", name=WORKER_NAME),
        _call(agent_type, "agent_tool", name=WORKER_NAME, task="must be blocked"),
        _save(agent_type),
        _call(agent_type, "agent_tool", name=WORKER_NAME, task="after reactivation"),
        _call(agent_type, "submit_output", output="done"),
    ])

    _assert_completed_children(runner, 2)
    assert WORKER_NAME not in _system_text(model.messages[3])
    assert f"{WORKER_NAME}: Workspace worker version 1" in _system_text(model.messages[5])
    assert runner._root().instance is root
    assert (runner.config_context.agents_dir / f"{WORKER_NAME}.yaml").is_file()


@pytest.mark.parametrize(
    ("mode_id", "allowed_modes"),
    [("plan", None), ("team", ["team"]), ("agent", ["group"])],
)
def test_saving_agent_preserves_mode_policy_and_team_membership(harness, mode_id: str, allowed_modes: list[str] | None) -> None:
    runner, root, _model = harness(mode_id=mode_id)
    manage = AgentManageTool(owner_agent=root)

    manage.forward("save", WORKER_NAME, config=_worker_config(allowed_modes=allowed_modes))

    # Saving a declaration cannot approve a Plan investigator, add a Team
    # member or override the declaration's own allowed_modes constraint.
    assert runner.agent_registry.load_config(WORKER_NAME).name == WORKER_NAME
    assert WORKER_NAME not in root.available_managed_agent_names
    assert not runner.is_agent_available(WORKER_NAME)
    assert AgentTool(owner_agent=root).forward(WORKER_NAME, "must be blocked")["status"] == "failed"
    with pytest.raises(PermissionError, match="当前 Runner 不允许调用 Agent"):
        runner.invoke_agent({"agent_ref": WORKER_NAME, "task": "must be blocked", "execution": "sync"})


def test_unload_is_local_to_current_runner_and_mode(harness, tmp_path: Path) -> None:
    workspace = tmp_path / "shared"
    runner, root, _model = harness(workspace=workspace)
    other, other_root, _other_model = harness(workspace=workspace)

    AgentManageTool(owner_agent=root).forward("unload", "general")

    assert "general" not in root.available_managed_agent_names
    assert "general" in other_root.available_managed_agent_names
    assert other.is_agent_available("general")
    assert "general" in other.agent_registry.load_config("root").managed_agent_names

    runner.reconfigure(mode_id="plan")
    assert "general" in runner._root().instance.available_managed_agent_names
    runner.reconfigure(mode_id="agent")
    assert "general" not in runner._root().instance.available_managed_agent_names
    AgentManageTool(owner_agent=runner._root().instance).forward("save", "general")
    assert "general" in runner._root().instance.available_managed_agent_names


def test_registry_edits_are_visible_without_an_activation_or_cache_update(harness) -> None:
    """External declaration edits must use the same live discovery as agents_list."""

    runner, root, model = harness()
    runner.agent_registry.save_config(_worker_config(), name=WORKER_NAME)

    listed = {entry["name"] for entry in AgentsListTool(owner_agent=root).forward()["agents"]}
    assert WORKER_NAME in listed
    assert WORKER_NAME in root.available_managed_agent_names
    # Deliberately leave the startup references untouched: changing this cache
    # is unnecessary for either discovery or actual background dispatch.
    assert WORKER_NAME not in root._runtime_managed_agent_refs
    assert AgentTool(owner_agent=root).forward(WORKER_NAME, "run the new declaration")["status"] == "launched"
    _assert_completed_children(runner, 1)

    _round(runner, model, [_call("react", "submit_output", output="done")])
    assert f"{WORKER_NAME}: Workspace worker version 1" in _system_text(model.messages[-1])
    runner.agent_registry.save_config(_worker_config(version=2), name=WORKER_NAME)
    _round(runner, model, [_call("react", "submit_output", output="done")])
    assert f"{WORKER_NAME}: Workspace worker version 2" in _system_text(model.messages[-1])

    runner.agent_registry.delete_config(WORKER_NAME)
    assert WORKER_NAME not in root.available_managed_agent_names
    assert AgentTool(owner_agent=root).forward(WORKER_NAME, "removed declaration")["status"] == "failed"
    _round(runner, model, [_call("react", "submit_output", output="done")])
    assert WORKER_NAME not in _system_text(model.messages[-1])


@pytest.mark.parametrize("start_disabled", [False, True])
def test_child_dynamic_discovery_keeps_its_explicit_relationship_scope(harness, start_disabled: bool) -> None:
    """Root discovery must not grant every child the same workspace authority."""

    runner, root, model = harness()
    runner.agent_registry.save_config({
        "name": "coordinator",
        "tools": [],
        "managed_agent_names": ["explore"],
    })
    if start_disabled:
        runner.disable_agent("explore")
    child = runner.agent_manager.acquire("coordinator", model=model).instance
    if start_disabled:
        assert child.available_managed_agent_names == []
        runner.enable_agent("explore")
    runner.agent_registry.save_config(_worker_config(), name=WORKER_NAME)

    assert WORKER_NAME in root.available_managed_agent_names
    assert child.available_managed_agent_names == ["explore"]
    assert AgentTool(owner_agent=child).forward(WORKER_NAME, "outside relationship")["status"] == "failed"
    runner.disable_agent("explore")
    assert child.available_managed_agent_names == []
    runner.enable_agent("explore")
    assert child.available_managed_agent_names == ["explore"]
