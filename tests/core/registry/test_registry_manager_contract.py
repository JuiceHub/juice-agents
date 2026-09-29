"""Static Registry contracts used by the Manager-owned runtime architecture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry import (
    AgentRegistry,
    GraphRegistry,
    PluginRegistry,
    SkillRegistry,
    TeamRegistry,
    ToolRegistry,
)


class _Model:
    """Minimal provider used only to prove fresh Agent construction."""

    def generate(self, _messages: list[dict[str, Any]], stop_sequence: Any = None) -> dict[str, str]:
        del stop_sequence
        return {"role": "assistant", "content": "unused"}


def _agent_declaration(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "agent_type": "react",
        "model_config_name": "default",
        "tools": [],
    }


def test_registries_expose_only_static_resolve_validate_instantiate_contract(tmp_path: Path) -> None:
    """Every Registry supports the common protocol without holding live state."""

    context = ConfigurationContext.from_workspace(tmp_path)
    registries = [
        AgentRegistry(config_context=context),
        ToolRegistry(config_context=context),
        GraphRegistry(config_context=context),
        SkillRegistry(
            local_dir=tmp_path / ".juice" / "skills",
            builtin_dir=tmp_path / "builtin-skills",
            external_dirs=[],
            workspace_dir=tmp_path,
            allow_names=["demo"],
        ),
        PluginRegistry(
            workspace_dir=tmp_path,
            project_dir=tmp_path,
            user_plugins_dir=tmp_path / "user-plugins",
        ),
        TeamRegistry(config_context=context),
    ]

    for registry in registries:
        assert all(callable(getattr(registry, method, None)) for method in ("resolve", "validate", "instantiate"))
        # Registries can keep Stores and immutable directory settings, but no
        # session, pool, Runner or live-object collection is an allowed field.
        assert not any("live" in name or "session" in name or "runner" in name for name in vars(registry))


def test_registry_instantiation_is_fresh_and_manager_independent(tmp_path: Path) -> None:
    """Static descriptors construct fresh domain objects without executing them."""

    context = ConfigurationContext.from_workspace(tmp_path)
    agent_registry = AgentRegistry(config_context=context)
    first_agent = agent_registry.instantiate(_agent_declaration("worker"), model=_Model())
    second_agent = agent_registry.instantiate(_agent_declaration("worker"), model=_Model())
    assert first_agent is not second_agent
    assert not hasattr(first_agent, "runner_context")

    tool_registry = ToolRegistry(config_context=context)
    assert tool_registry.resolve("read") == "read"
    assert tool_registry.instantiate("read") is not tool_registry.instantiate("read")

    graph_registry = GraphRegistry(config_context=context)
    assert graph_registry.validate("deep_research").name == "deep_research"
    assert graph_registry.instantiate("deep_research") is not graph_registry.instantiate("deep_research")

    skill_file = tmp_path / ".juice" / "skills" / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Demo\nA static test skill.\n", encoding="utf-8")
    skill_registry = SkillRegistry(
        local_dir=skill_file.parents[1],
        builtin_dir=tmp_path / "builtin-skills",
        external_dirs=[],
        workspace_dir=tmp_path,
        allow_names=["demo"],
    )
    assert skill_registry.instantiate("demo").name == "demo"

    plugin_manifest = tmp_path / ".juice" / "plugins" / "demo" / ".juice-plugin" / "plugin.json"
    plugin_manifest.parent.mkdir(parents=True)
    plugin_manifest.write_text(json.dumps({"name": "demo", "skills": []}), encoding="utf-8")
    plugin_registry = PluginRegistry(
        workspace_dir=tmp_path,
        project_dir=tmp_path,
        user_plugins_dir=tmp_path / "user-plugins",
    )
    assert plugin_registry.instantiate("demo").name == "demo"

    team_registry = TeamRegistry(config_context=context)
    agent_registry.save_config(_agent_declaration("member"))
    team = team_registry.instantiate(
        {
            "team_name": "review",
            "description": "review task",
            "member_names": ["member"],
            "schema_version": 2,
        }
    )
    assert team.member_names == ("member",)
