"""Regression coverage for global AgentConfig availability declarations."""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.agent.builtin.configs import get_explore_config, get_general_subagent_config
from juice_agents.core.group.builtin.configs import build_default_group_worker_configs
from juice_agents.core.registry.agents.availability import is_agent_available
from juice_agents.core.registry.agents.registry import AgentRegistry
from juice_agents.core.registry.agents.types import AgentConfig, AgentRef


def _registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(config_context=ConfigurationContext.from_workspace(tmp_path))


def _config(name: str, *, allowed_modes: list[str] | None = None) -> AgentConfig:
    return AgentConfig.from_dict(
        {
            "name": name,
            "description": f"{name} description",
            "allowed_modes": allowed_modes,
            "tools": [],
        }
    )


def test_allowed_modes_preserves_unrestricted_custom_modes_and_rejects_empty() -> None:
    unrestricted = _config("portable")
    restricted = _config("group_worker", allowed_modes=["group", "custom", "group"])

    assert unrestricted.allowed_modes is None
    assert restricted.allowed_modes == ("group", "custom")
    assert unrestricted.to_persisted_dict().get("allowed_modes") is None
    assert restricted.to_persisted_dict()["allowed_modes"] == ["group", "custom"]
    assert is_agent_available(unrestricted, mode_id="an-unregistered-custom-mode")
    assert not is_agent_available(restricted, mode_id="plan")

    with pytest.raises(ValueError, match="allowed_modes 不能为空"):
        _config("hidden", allowed_modes=[])
    with pytest.raises(ValueError, match="allowed_modes 必须为 list"):
        AgentConfig.from_dict({"name": "invalid", "allowed_modes": "group", "tools": []})


def test_registry_resolves_agentconfig_as_detached_declaration_and_rejects_live_agents(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    original = AgentConfig.from_dict(
        {
            "name": "worker",
            "allowed_modes": ["agent"],
            "output_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
            "tools": [],
        }
    )

    direct = registry.resolve(original)
    serialized = registry.resolve(original.to_dict())
    overridden = registry.resolve(AgentRef("ignored", declaration_override=original))

    assert direct == original
    assert direct is not original
    assert direct.output_schema is not original.output_schema
    assert serialized == original
    assert overridden == original
    assert registry.validate(original) is not original

    with pytest.raises(TypeError, match="AgentRef / name / config object"):
        registry.resolve(SimpleNamespace(name="live-agent"))


def test_registry_instantiates_a_fresh_agent_from_an_agentconfig(tmp_path: Path) -> None:
    class _Model:
        def generate(self, _messages: list[dict[str, object]], stop_sequence: object = None) -> dict[str, str]:
            del stop_sequence
            return {"role": "assistant", "content": "unused"}

    registry = _registry(tmp_path)
    declaration = _config("worker", allowed_modes=["agent"])

    first = registry.instantiate(declaration, model=_Model())
    second = registry.instantiate(declaration, model=_Model())

    assert first is not second
    assert first._declared_agent_config == declaration
    assert first._declared_agent_config is not declaration


def test_root_config_can_be_resolved_in_memory_but_never_saved_as_a_declaration(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    root = _config("root", allowed_modes=["agent", "plan"])

    assert registry.resolve(root).name == "root"
    with pytest.raises(ValueError, match="root 是 Runner"):
        registry.save_config(root)
    with pytest.raises(ValueError, match="root 是 Runner"):
        registry.seed_config(root)
    assert registry.list_available_agents(name="root", mode_id="agent") == {
        "mode_id": "agent",
        "agents": [],
    }


def test_list_available_agents_uses_mode_policy_and_runner_scoped_disables(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.save_config(_config("general", allowed_modes=["agent", "plan"]))
    registry.save_config(_config("explore", allowed_modes=["agent", "plan"]))
    registry.save_config(_config("group_worker", allowed_modes=["group"]))
    registry.save_config(_config("portable"))

    runner = SimpleNamespace(
        mode_id="plan",
        config=SimpleNamespace(
            tool_policy=SimpleNamespace(allowed_agent_names=frozenset({"general", "explore"}))
        ),
        state={"disabled_agent_names_by_mode": {"plan": ["explore"]}},
        agent_manager=SimpleNamespace(get_by_name=lambda _name: (_ for _ in ()).throw(KeyError(_name))),
        root_agent_name="root",
    )

    result = registry.list_available_agents(mode_id="plan", runner=runner)

    assert result["mode_id"] == "plan"
    assert [entry["name"] for entry in result["agents"]] == ["general"]
    assert result["agents"][0]["allowed_modes"] == ["agent", "plan"]
    assert result["agents"][0]["config"]["name"] == "general"
    assert registry.list_available_agents(name="group_worker", mode_id="plan") == {
        "mode_id": "plan",
        "agents": [],
    }


def test_shared_predicate_enforces_managed_references_for_non_root_dispatch() -> None:
    worker = _config("worker", allowed_modes=["agent"])

    assert not is_agent_available(worker, mode_id="agent", allowed_agent_names=())
    assert is_agent_available(worker, mode_id="agent", allowed_agent_names=["worker"])
    assert not is_agent_available(
        worker,
        mode_id="agent",
        allowed_agent_names=["worker"],
        disabled_agent_names=["worker"],
    )


def test_cold_availability_query_never_materializes_defaults_or_runtime_state(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    assert registry.list_available_agents(mode_id="agent") == {"mode_id": "agent", "agents": []}
    assert not (tmp_path / ".juice").exists()


def test_available_agent_fallbacks_are_read_only_and_workspace_declarations_win(tmp_path: Path) -> None:
    """A transport can preview defaults without shadowing an Agent YAML edit."""

    registry = _registry(tmp_path)
    registry.save_config(_config("general", allowed_modes=["agent"]))

    result = registry.list_available_agents(
        mode_id="agent",
        fallback_configs=(get_general_subagent_config(), get_explore_config()),
    )

    entries = {entry["name"]: entry for entry in result["agents"]}
    assert entries["general"]["source"] == "workspace"
    assert entries["general"]["description"] == "general description"
    assert entries["explore"]["source"] == "builtin"
    assert registry.list_configs() == ["general"]


def test_default_group_workers_are_limited_to_group_mode() -> None:
    workers = build_default_group_worker_configs()

    assert workers
    assert {worker.allowed_modes for worker in workers} == {("group",)}
