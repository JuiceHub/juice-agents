"""Explicit Skill, Plugin, and Graph capability activation contracts."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from adapters.stdio_gateway.runtime import DirectRunnerRuntime
from juice_agents.core.agent import ReActAgent
from juice_agents.core.agent.attachments import serialize_runtime_attachments_text
from juice_agents.core.agent.tools.builtin.capabilities.manual_capabilities import (
    ScopedGraphTool,
    feature_enabled,
    resolve_manual_capability,
)
from juice_agents.core.config.context import ConfigurationContext


def _context(workspace: Path, *, skills_config: str = "enabled: false\n  disabled: [manual-demo]") -> ConfigurationContext:
    config = workspace / "runtime.yaml"
    config.write_text(
        f"skills:\n  {skills_config}\ngraphs:\n  enabled: false\n",
        encoding="utf-8",
    )
    return ConfigurationContext.from_workspace(workspace, project_config_path=config)


@pytest.mark.parametrize(
    "skills_config",
    [
        "enabled: false\n  disabled: []",
        "enabled: true\n  disabled: [manual-demo]",
    ],
    ids=["global-disabled", "individual-disabled"],
)
def test_explicit_skill_bypasses_auto_disable_and_injects_full_skill_markdown(skills_config: str) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        skill_file = workspace / ".juice" / "skills" / "manual-demo" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# Manual Demo\n\nUse the full explicit instruction.", encoding="utf-8")

        capability = resolve_manual_capability(
            "$manual-demo summarize this",
            context=_context(workspace, skills_config=skills_config),
        )

        assert capability is not None
        assert capability.task == "summarize this"
        rendered = serialize_runtime_attachments_text([capability.attachment])
        assert '<explicit_capability kind="skill" name="manual-demo">' in rendered
        assert "Use the full explicit instruction." in rendered


def test_explicit_graph_resolves_when_automatic_graph_tools_are_disabled() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        context = _context(Path(temp_dir))
        capability = resolve_manual_capability("$graph:deep_research compare two APIs", context=context)

        assert capability is not None
        assert capability.graph_name == "deep_research"
        assert capability.task == "compare two APIs"
        assert "input_schema" in capability.attachment["payload"]
        assert ScopedGraphTool("deep_research").forward("other", {})["status"] == "failed"


def test_invalid_feature_values_default_to_enabled() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        config = workspace / "runtime.yaml"
        config.write_text("graphs:\n  enabled: 'false'\n", encoding="utf-8")
        assert feature_enabled(
            ConfigurationContext.from_workspace(workspace, project_config_path=config),
            "graphs",
        )


def test_gateway_forwards_explicit_capability_text_to_sdk_runner() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        config = workspace / "runtime.yaml"
        config.write_text("graphs:\n  enabled: false\n", encoding="utf-8")
        runtime = DirectRunnerRuntime(base_dir=workspace, runtime_config_path=config)
        runner = MagicMock()
        runner.root_agent_name = "root_agent"
        runner.stream.return_value = iter(())
        runtime._runner = runner
        list(runtime.stream_message("$graph:deep_research compare APIs"))

        runner.stream.assert_called_once_with("$graph:deep_research compare APIs")
