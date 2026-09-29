"""Tests for Runner-only AgentConfig snapshot serialization."""

from __future__ import annotations

from juice_agents.core.registry.agents.runtime_snapshot import build_runtime_agent_config
from juice_agents.core.registry.agents.types import AgentConfig


class _RuntimeAgent:
    """Minimal live Agent shape accepted by the fallback serializer."""

    name = "runtime_agent"
    description = "runtime description"
    instructions = "runtime instructions"
    prompt_language = "zh"
    max_steps = 7
    output_schema = {"type": "string"}
    log_file_path = "old.log"
    authorized_imports = ["math"]
    available_managed_agent_names = ["worker"]
    _agent_lifecycle = "persistent"
    _agent_isolation = "worktree"
    _declared_tool_refs = ({"name": "read"},)


def test_fallback_snapshot_serializes_stable_runtime_declaration() -> None:
    config = build_runtime_agent_config(_RuntimeAgent())

    assert config.name == "runtime_agent"
    assert config.agent_type == "react"
    assert config.description == "runtime description"
    assert config.max_steps == 7
    assert [ref.name for ref in config.tools] == ["read"]
    assert config.managed_agent_names == ("worker",)
    assert config.lifecycle == "persistent"
    assert config.isolation == "worktree"
    assert config.additional_authorized_imports == ("math",)


def test_snapshot_preserves_default_declaration_and_resolved_type_separately() -> None:
    declared = AgentConfig.from_dict(
        {"name": "default_agent", "agent_type": "default", "tools": []}
    )
    agent = _RuntimeAgent()
    agent._declared_agent_config = declared
    agent._resolved_agent_type = "codeact"
    agent.available_managed_agent_names = []

    snapshot = build_runtime_agent_config(agent)

    assert snapshot.agent_type == "default"
    assert agent._resolved_agent_type == "codeact"


def test_snapshot_uses_declared_config_without_mutating_it() -> None:
    declared = AgentConfig.from_dict(
        {
            "name": "declared_agent",
            "agent_type": "codeact",
            "description": "declared description",
            "instructions": "declared instructions",
            "tools": [],
        }
    )
    agent = _RuntimeAgent()
    agent._declared_agent_config = declared
    agent.available_managed_agent_names = []
    agent._agent_lifecycle = "functional"
    agent._agent_isolation = "none"

    snapshot = build_runtime_agent_config(agent)

    assert snapshot is not declared
    assert snapshot.name == "declared_agent"
    assert snapshot.agent_type == "codeact"
    assert snapshot.description == "declared description"
    assert declared.managed_agent_names == ()


def test_explicit_snapshot_overrides_replace_runtime_only_fields() -> None:
    config = build_runtime_agent_config(
        _RuntimeAgent(),
        model_config_name="fast",
        model_effort="high",
        tool_refs=[{"name": "write"}],
        log_file_path="new.log",
    )

    assert config.model_config_name == "fast"
    assert config.model_effort == "high"
    assert [ref.name for ref in config.tools] == ["write"]
    assert config.log_file_path == "new.log"
