"""Built-in resources resolve without materialization across SDK entry points."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from juice_agents.core.agent.builtin.builder import build_general_agent
from juice_agents.core.agent.builtin.configs import get_general_subagent_config
from juice_agents.core.agent.tools.builtin.agents.agents_tools import AgentsListTool, AgentViewTool
from juice_agents.core.agent.tools.builtin.evolution.agent_manage import AgentManageTool
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.prebuilt import build_prebuilt_agent_declarations, create_prebuilt_runner
from juice_agents.core.registry import AgentRegistry
from juice_agents.core.registry.agents import registry as registry_module
from juice_agents.core.runner import Runner


class _SubmitModel:
    """Exercise actual execution while replacing only provider generation."""

    def __init__(self, output="done"):
        self.output = output

    def generate(self, *_args, **_kwargs):
        actions = json.dumps([{"name": "submit_output", "args": {"output": self.output}}])
        return {"role": "assistant", "content": f"<thought>done</thought><actions>{actions}</actions>"}


def _registry(workspace: Path) -> AgentRegistry:
    return AgentRegistry(config_context=ConfigurationContext.from_workspace(workspace))


@pytest.mark.parametrize("mode_id", ["agent", "plan", "group", "team"])
def test_cold_declarations_and_runner_creation_do_not_save_resources(tmp_path: Path, mode_id: str) -> None:
    declarations = build_prebuilt_agent_declarations(base_dir=tmp_path, runner_config=mode_id)
    assert "root" in {config.name for config in declarations}
    assert not (tmp_path / ".juice").exists()

    runner = create_prebuilt_runner(base_dir=tmp_path, runner_config=mode_id)
    try:
        assert runner.agent_registry.get_source("root") == "builtin"
        assert runner.agent_registry.load_config("root").name == "root"
        assert not runner.agent_manager.live_agents
        assert not (tmp_path / ".juice" / "agents").exists()
        assert not (tmp_path / ".juice" / "teams").exists()
        assert runner.layout.manifest_path.exists()
        if mode_id in {"agent", "plan"}:
            assert {item["name"] for item in runner.list_available_agents()["agents"]} >= {"general", "explore"}
        elif mode_id == "group":
            assert runner.list_available_agents()["agents"]
        else:
            assert runner.list_available_agents()["agents"] == []
    finally:
        runner.close()


def test_general_builder_uses_root_override_and_keeps_general_a_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: object())
    registry = _registry(tmp_path)
    # The general subagent may have its own edit; root must resolve root.yaml.
    registry.save_config(get_general_subagent_config().copy_with(instructions="child override"))
    registry.save_config(registry.load_config("root").copy_with(instructions="root override"))
    root_path = registry.config_path("root")
    original_bytes = root_path.read_bytes()

    root = build_general_agent(workspace_dir=tmp_path, agent_type="codeact")

    assert root.name == "root"
    assert root._declared_agent_config.instructions == "root override"
    assert root._declared_agent_config.agent_type == "default"
    assert set(root._runtime_managed_agent_refs) == {"general", "explore"}
    assert root_path.read_bytes() == original_bytes
    assert not (tmp_path / ".juice" / "agents" / "explore.yaml").exists()
    assert registry.load_config("general").instructions == "child override"


def test_general_builder_without_overrides_does_not_save_agent_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: object())

    root = build_general_agent(workspace_dir=tmp_path)

    assert root.name == "root"
    assert not (tmp_path / ".juice" / "agents").exists()
    assert all(not ref.params for ref in root._declared_agent_config.tools)


def test_agent_tools_use_registry_source_and_first_save_materializes_override(tmp_path: Path) -> None:
    owner = SimpleNamespace(name="root")
    kwargs = {"owner_agent": owner, "workspace_dir": tmp_path}
    view = AgentViewTool(**kwargs)
    listing = AgentsListTool(**kwargs).forward()["agents"]
    assert len({item["name"] for item in listing}) == len(listing)
    assert {item["name"] for item in listing} >= {"root", "general", "explore", "researcher"}
    assert all(item["source"] == "builtin" and item["path"] is None for item in listing)
    config = view.forward("root")["agent"]["config"]
    config["instructions"] = "local root instructions"
    tool = AgentManageTool(**kwargs)
    assert tool.forward("validate", "root", config=config)["result"]["valid"]
    assert not (tmp_path / ".juice").exists()
    with pytest.raises(ValueError):
        tool.forward("save", "root", config={**config, "managed_agent_names": ["unknown"]})
    assert not (tmp_path / ".juice").exists()

    tool.forward("save", "root", config=config)

    viewed = view.forward("root")["agent"]
    assert viewed["source"] == "local"
    assert viewed["path"] == str(tmp_path / ".juice" / "agents" / "root.yaml")
    assert viewed["config"]["instructions"] == "local root instructions"
    assert not (tmp_path / ".juice" / "agents" / "general.yaml").exists()


def test_saved_root_override_is_used_when_runner_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: object())
    runner = create_prebuilt_runner(base_dir=tmp_path, runner_config="agent")
    runner_id = runner.runner_id
    override = runner.agent_registry.load_config("root").copy_with(instructions="persisted root override")
    runner.agent_registry.save_config(override)
    runner.close()

    restored = Runner.resume(runner_id=runner_id, base_dir=tmp_path)
    try:
        assert restored.root_config.instructions == "persisted root override"
        assert restored._root().instance._declared_agent_config.instructions == "persisted root override"
        assert not (tmp_path / ".juice" / "agents" / "general.yaml").exists()
    finally:
        restored.close()


@pytest.mark.parametrize("member_source", ["shared", "private"])
def test_failed_first_team_member_creation_restores_builtin_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member_source: str) -> None:
    runner = create_prebuilt_runner(base_dir=tmp_path, runner_config="team")
    try:
        def fail_create(*_args, **_kwargs):
            raise ValueError("runtime member failed")

        monkeypatch.setattr(runner.team_manager, "create_member", fail_create)
        kwargs = {"agent_name": "researcher"} if member_source == "shared" else {"config": {"instructions": "research"}}
        with pytest.raises(ValueError, match="runtime member failed"):
            runner.create_team_member("research", **kwargs)
        assert not (tmp_path / ".juice" / "teams" / "default").exists()
        assert runner.available_agent_names() == frozenset()
    finally:
        runner.close()


@pytest.mark.parametrize("member_source", ["shared_alias", "private"])
def test_team_dispatch_resolves_unsaved_shared_and_private_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member_source: str) -> None:
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: _SubmitModel())
    runner = create_prebuilt_runner(base_dir=tmp_path, runner_config="team")
    try:
        member_name = "general" if member_source == "private" else "reviewer"
        kwargs = {"config": {"instructions": "private general instructions"}} if member_source == "private" else {"agent_name": "researcher"}
        runner.create_team_member(member_name, **kwargs)

        assert runner._run_team_member(member_name, None) == "done"
        owner = runner.agent_manager.get_by_name(member_name).instance
        declaration = owner._declared_agent_config
        assert declaration.name == ("general" if member_source == "private" else "researcher")
        viewed = AgentViewTool(owner_agent=owner).forward(declaration.name)["agent"]
        assert viewed["source"] == ("local" if member_source == "private" else "builtin")
        assert not (tmp_path / ".juice" / "agents").exists()
        if member_source == "private":
            assert declaration.instructions == "private general instructions"
            assert "team_task_complete" not in {ref.name for ref in declaration.tools}
            Path(viewed["path"]).unlink()
            # A missing private general cannot silently select the built-in
            # global general Agent of the same name.
            with pytest.raises(FileNotFoundError):
                AgentViewTool(owner_agent=owner).forward(declaration.name)
        else:
            assert viewed["path"] is None
    finally:
        runner.close()


@pytest.mark.parametrize("mode_id", ["agent", "plan", "group"])
def test_builtin_workers_execute_without_saved_declarations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode_id: str) -> None:
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: _SubmitModel())
    runner = create_prebuilt_runner(base_dir=tmp_path, runner_config=mode_id)
    try:
        assert list(runner.stream("initialize root"))[-1]["output"] == "done"
        available = runner.list_available_agents()["agents"]
        worker_name = "general_worker" if mode_id == "group" else available[0]["name"]
        output = {"result": "done"} if mode_id == "group" else "done"
        result = runner.invoke_agent({
            "agent_ref": worker_name, "task": "submit done", "execution": "sync", "model": _SubmitModel(output),
        })
        assert result["output"] == output
        assert not (tmp_path / ".juice" / "agents").exists()
        assert not (tmp_path / ".juice" / "teams").exists()
        assert runner.layout.manifest_path.exists()
    finally:
        runner.close()
