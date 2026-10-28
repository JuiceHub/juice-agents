"""预置声明按需落盘的功能回归；使用真实 Registry、Runner 和临时工作区。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.prebuilt import create_prebuilt_runner
from juice_agents.core.registry import AgentRegistry, TeamRegistry
from juice_agents.core.registry.agents import registry as agent_registry_module
from juice_agents.core.registry.agents.defaults import build_builtin_agent_configs
from juice_agents.core.registry.agents.store import AgentConfigStore
from juice_agents.core.registry.teams.store import TeamConfigStore
from juice_agents.core.runner import Runner


class _SubmitModel:
    """只提交结果的模型桩，保留真实 Agent 构造、执行与会话持久化路径。"""

    def generate(self, _messages: Any, **_kwargs: Any) -> dict[str, str]:
        actions = [{"name": "submit_output", "args": {"output": "done"}}]
        return {"role": "assistant", "content": f"<thought>done</thought><actions>{json.dumps(actions)}</actions>"}


def _install_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: _SubmitModel())


def test_complete_builtin_catalog_is_effective_while_stores_remain_empty(tmp_path: Path) -> None:
    """冷读取与实例构造直接使用内置数据，不将有效声明伪装成磁盘文件。"""

    context = ConfigurationContext.from_workspace(tmp_path)
    agents = AgentRegistry(config_context=context)
    teams = TeamRegistry(config_context=context)
    catalog = build_builtin_agent_configs()

    assert set(agents.list_configs()) == {config.name for config in catalog}
    assert {"root", "general", "explore", "general_worker", "researcher", "developer"} <= set(agents.list_configs())
    for declaration in catalog:
        assert agents.load_config(declaration.name) == declaration
        assert agents.validate(declaration.name) == declaration
        assert agents.get_source(declaration.name) == "builtin"
        assert agents.config_path(declaration.name) is None
        instance = agents.instantiate(declaration.name, model=_SubmitModel())
        assert instance.name == declaration.name
        assert instance._declared_agent_config == declaration

    assert teams.get_manifest("default").member_names == ()
    assert teams.get_source("default") == "builtin"
    assert not teams.manifest_path("default").exists()
    assert AgentConfigStore(context.agents_dir).list() == []
    assert TeamConfigStore(tmp_path).list() == []
    assert not context.juice_root.exists()


@pytest.mark.parametrize("mode_id", ["agent", "plan", "group", "team"])
def test_each_builtin_mode_runs_and_saves_state_without_saving_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode_id: str
) -> None:
    """模型请求走真实 Agent 路径；运行记录可写，默认资源声明仍不落盘。"""

    _install_model(monkeypatch)
    runner = create_prebuilt_runner(runner_config=mode_id, base_dir=tmp_path)
    try:
        assert not (tmp_path / ".juice" / "agents").exists()
        assert not (tmp_path / ".juice" / "teams").exists()
        events = list(runner.stream("Return done."))
        assert events[-1]["kind"] == "round_end"
        assert events[-1]["output"] == "done"
        assert runner.layout.manifest_path.is_file()
        assert runner.agent_manager.get_by_name("root").instance.name == "root"
        assert not (tmp_path / ".juice" / "agents").exists()
        assert not (tmp_path / ".juice" / "teams").exists()
    finally:
        runner.close()


def test_agent_override_survives_reload_and_runner_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """会话恢复重新解析本地覆盖，不依赖创建 Runner 时的默认初始化文件。"""

    _install_model(monkeypatch)
    context = ConfigurationContext.from_workspace(tmp_path)
    registry = AgentRegistry(config_context=context)
    builtin_root = registry.load_config("root")
    edited_root = builtin_root.copy_with(instructions="Workspace root instructions.", tools=[])
    registry.save_config(edited_root)
    fresh = AgentRegistry(config_context=context)
    assert fresh.load_config("root") == edited_root
    assert fresh.get_source("root") == "local"
    assert fresh.load_config("general").instructions != edited_root.instructions

    runner = create_prebuilt_runner(runner_config="agent", base_dir=tmp_path)
    runner_id = runner.runner_id
    try:
        assert runner.root_config.instructions == edited_root.instructions
        assert list(runner.stream("First turn."))[-1]["output"] == "done"
    finally:
        runner.close()

    resumed = Runner.resume(runner_id=runner_id, base_dir=tmp_path)
    try:
        assert resumed.root_config.instructions == edited_root.instructions
        assert list(resumed.stream("Second turn."))[-1]["output"] == "done"
        # 全局资源目录只包含用户显式保存的 root，运行状态位于 runners 下。
        assert sorted(path.name for path in context.agents_dir.iterdir()) == ["root.yaml"]
    finally:
        resumed.close()

    registry.delete_config("root")
    assert AgentRegistry(config_context=context).load_config("root") == builtin_root
    assert registry.get_source("root") == "builtin"


def test_invalid_local_override_is_not_hidden_by_builtin_fallback(tmp_path: Path) -> None:
    """只有缺失文件才能回退；用户已有 YAML 的错误必须可见。"""

    context = ConfigurationContext.from_workspace(tmp_path)
    context.agents_dir.mkdir(parents=True)
    local = context.agents_dir / "general.yaml"
    local.write_text("name: general\nallowed_modes: []\n", encoding="utf-8")
    before = local.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="allowed_modes"):
        AgentRegistry(config_context=context).load_config("general")

    assert local.read_text(encoding="utf-8") == before
    assert sorted(path.name for path in context.agents_dir.iterdir()) == ["general.yaml"]


def test_private_agent_scope_does_not_fallback_to_global_builtin(tmp_path: Path) -> None:
    """与内置成员同名的私有目录缺项，应报缺失而非借用全局 Agent。"""

    context = ConfigurationContext.from_workspace(tmp_path)
    private_dir = context.juice_root / "teams" / "review" / "members"
    private = AgentRegistry(config_context=context, config_dir=private_dir)

    assert private.list_configs() == []
    with pytest.raises(FileNotFoundError):
        private.load_config("researcher")
    assert not context.juice_root.exists()


def test_unknown_agent_and_invalid_first_edit_do_not_create_files(tmp_path: Path) -> None:
    registry = AgentRegistry(config_context=ConfigurationContext.from_workspace(tmp_path))
    with pytest.raises(FileNotFoundError):
        registry.load_config("missing")
    assert registry.list_available_agents(name="missing", mode_id="agent")["agents"] == []
    with pytest.raises(ValueError):
        registry.save_config(registry.load_config("general").copy_with(tools=["missing_tool"]))
    assert not (tmp_path / ".juice").exists()


@pytest.mark.parametrize("has_override", [False, True])
def test_failed_agent_edit_preserves_effective_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_override: bool
) -> None:
    """原子提交失败保留原覆盖，首次保存失败仍解析为内置，且无临时文件。"""

    from juice_agents.core.registry.common import store

    registry = AgentRegistry(config_context=ConfigurationContext.from_workspace(tmp_path))
    builtin = registry.load_config("general")
    if has_override:
        registry.save_config(builtin.copy_with(instructions="Existing local instructions."))
    original = registry.load_config("general")
    original_path = registry.config_path("general")
    original_bytes = original_path.read_bytes() if original_path else None

    def deny_replace(*_args: Any) -> None:
        raise PermissionError("replace denied")

    monkeypatch.setattr(store.os, "replace", deny_replace)
    with pytest.raises(PermissionError, match="replace denied"):
        registry.save_config(original.copy_with(instructions="Failed edit."))

    assert registry.load_config("general") == original
    assert registry.get_source("general") == ("local" if has_override else "builtin")
    if original_path:
        assert original_path.read_bytes() == original_bytes
    files = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    assert files == ([original_path] if has_override else [])
