"""Explicit `$` capability activation at the gateway boundary.

Automatic capability discovery follows workspace switches.  A leading `$`
request is different: the user names a capability and it is exposed only to the
current root stream, with its instructions injected as an attachment.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from juice_agents.core.agent.attachments import RuntimeAttachment
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.plugins import PluginRegistry
from juice_agents.core.registry.skills import SkillRegistry

from ..graphs.graphs_tools import GraphTool

logger = logging.getLogger(__name__)
_LEADING_CAPABILITY = re.compile(r"^\s*\$(?P<name>[^\s]+)(?:\s+(?P<task>[\s\S]*))?$")
_MISSING = object()


def feature_enabled(context: ConfigurationContext, section: str) -> bool:
    """Read an opt-out boolean; absent or malformed settings remain enabled."""

    try:
        raw = context.read_merged_config().get(section, {})
    except Exception:
        return True
    if not isinstance(raw, dict):
        return True
    enabled = raw.get("enabled", True)
    return enabled if isinstance(enabled, bool) else True


def graphs_enabled(context: ConfigurationContext) -> bool:
    """Whether Graph tools are automatically available to an Agent."""

    return feature_enabled(context, "graphs")


def self_evolution_enabled(context: ConfigurationContext) -> bool:
    """Whether Agent/Tool/Skill/Graph mutation tools may run at all."""

    return feature_enabled(context, "self_evolution")


class ManualCapabilityError(ValueError):
    """A user-requested leading `$` capability cannot be resolved."""


class ScopedGraphTool(GraphTool):
    """A graph tool that accepts exactly the graph selected by `$graph:<name>`."""

    def __init__(self, graph_name: str) -> None:
        super().__init__()
        self.graph_name = str(graph_name or "").strip()

    def forward(
        self,
        name: str,
        payload: dict[str, Any],
        config: dict[str, Any] | None = None,
        background: bool = False,
    ) -> dict[str, Any]:
        if str(name or "").strip() != self.graph_name:
            return self._failed(
                graph_name=str(name or "").strip(),
                error=f"当前显式 Graph 调用仅授权 {self.graph_name!r}",
            )
        return super().forward(name=name, payload=payload, config=config, background=background)


@dataclass(frozen=True, slots=True)
class ManualCapability:
    """The remaining task, one attachment, and optional transient graph scope."""

    task: str
    attachment: RuntimeAttachment
    graph_name: str | None = None

    def install(self, agent: Any) -> tuple[Any, Any]:
        """Install a scoped graph callable and return exact state for cleanup."""

        if self.graph_name is None:
            return _MISSING, _MISSING
        tools = getattr(agent, "tools", None)
        if not isinstance(tools, dict):
            raise ManualCapabilityError("当前 root agent 不支持临时 Graph 能力")
        previous_tool = tools.get("graph_tool", _MISSING)
        previous_authorization = getattr(agent, "_manual_graph_authorization", _MISSING)
        # GraphTool resolves its GraphRunManager from the owning agent's runner
        # context.  A transient instance therefore needs the same binding as a
        # normal registry-created tool before it becomes callable.
        scoped_tool = ScopedGraphTool(self.graph_name)
        scoped_tool.bind_owner_agent(agent)
        tools["graph_tool"] = scoped_tool
        agent._manual_graph_authorization = self.graph_name
        _refresh_agent_capabilities(agent)
        logger.info("显式 Graph 能力已注入: agent=%s graph=%s", getattr(agent, "name", ""), self.graph_name)
        return previous_tool, previous_authorization

    def uninstall(self, agent: Any, previous_tool: Any, previous_authorization: Any) -> None:
        """Restore the pre-stream callable surface even when stream execution fails."""

        if self.graph_name is None:
            return
        tools = getattr(agent, "tools", None)
        if isinstance(tools, dict):
            if previous_tool is _MISSING:
                tools.pop("graph_tool", None)
            else:
                tools["graph_tool"] = previous_tool
        if previous_authorization is _MISSING:
            try:
                delattr(agent, "_manual_graph_authorization")
            except AttributeError:
                pass
        else:
            agent._manual_graph_authorization = previous_authorization
        _refresh_agent_capabilities(agent)
        logger.info("显式 Graph 能力已回收: agent=%s", getattr(agent, "name", ""))


def _refresh_agent_capabilities(agent: Any) -> None:
    """Synchronize both the prompt and CodeAct executor with a transient tool."""

    refresh_executor = getattr(agent, "refresh_executor_tools", None)
    if callable(refresh_executor):
        refresh_executor()
    refresh_prompt = getattr(agent, "refresh_system_prompt", None)
    if callable(refresh_prompt):
        refresh_prompt()


def _skill_registry(context: ConfigurationContext) -> SkillRegistry:
    return SkillRegistry(
        local_dir=context.juice_root / "skills",
        config_path=context.project_config_path,
        workspace_dir=context.workspace_dir,
    )


def _skill_attachment(view: dict[str, Any], *, requested_name: str, plugin_name: str = "") -> RuntimeAttachment:
    return {
        "attachment_type": "explicit_capability",
        "created_at": 0.0,
        "payload": {
            "kind": "plugin_skill" if plugin_name else "skill",
            "requested_name": requested_name,
            "name": str(view.get("name") or requested_name),
            "plugin": plugin_name,
            "instructions": str(view.get("content") or ""),
            "path": str(view.get("path") or ""),
        },
    }


def _resolve_skill(context: ConfigurationContext, name: str, *, task: str) -> ManualCapability | None:
    try:
        view = _skill_registry(context).view_explicit(name)
    except (FileNotFoundError, ValueError):
        return None
    if not view.get("success"):
        raise ManualCapabilityError(str(view.get("error") or f"无法读取 Skill: {name}"))
    return ManualCapability(task=task, attachment=_skill_attachment(view, requested_name=name))


def _resolve_plugin(context: ConfigurationContext, name: str, *, task: str) -> ManualCapability | None:
    try:
        plugin = PluginRegistry(config_context=context).read(name, require_enabled=True)
    except (FileNotFoundError, ValueError):
        return None
    registry = _skill_registry(context)
    views = [registry.view_explicit(skill_name) for skill_name in plugin.skill_names()]
    successful = [view for view in views if view.get("success")]
    if not successful:
        raise ManualCapabilityError(f"Plugin {name!r} 没有可注入的 Skill 说明")
    instructions = "\n\n".join(
        f"# Plugin Skill: {view.get('qualified_name') or view.get('name')}\n{view.get('content') or ''}"
        for view in successful
    )
    return ManualCapability(
        task=task,
        attachment=_skill_attachment({"name": name, "content": instructions, "path": str(plugin.root)}, requested_name=name, plugin_name=name),
    )


def _resolve_graph(context: ConfigurationContext, name: str, *, task: str) -> ManualCapability:
    from juice_agents.core.registry.graphs import GraphRegistry

    metadata = GraphRegistry(config_context=context).get_metadata(name)
    return ManualCapability(
        task=task,
        graph_name=metadata.name,
        attachment={
            "attachment_type": "explicit_capability",
            "created_at": 0.0,
            "payload": {
                "kind": "graph",
                "name": metadata.name,
                "description": metadata.description,
                "input_schema": metadata.input_schema,
                "output_schema": metadata.output_schema,
                "instructions": (
                    f"The user explicitly selected graph {metadata.name!r}. "
                    "Use the temporarily available graph_tool with this exact name and "
                    "build its payload from the supplied input schema."
                ),
            },
        },
    )


def resolve_manual_capability(message: str, *, context: ConfigurationContext) -> ManualCapability | None:
    """Resolve only the first `$` token; ordinary messages stay unchanged."""

    matched = _LEADING_CAPABILITY.match(str(message or ""))
    if matched is None:
        return None
    requested = str(matched.group("name") or "").strip()
    task = str(matched.group("task") or "").strip()
    if requested.lower().startswith("graph:"):
        graph_name = requested.split(":", 1)[1].strip()
        if not graph_name:
            raise ManualCapabilityError("$graph: 后必须提供 graph 名称")
        return _resolve_graph(context, graph_name, task=task)
    # A Skill intentionally wins over a Plugin with the same `$` alias.
    capability = _resolve_skill(context, requested, task=task)
    if capability is not None:
        return capability
    capability = _resolve_plugin(context, requested, task=task)
    if capability is not None:
        return capability
    raise ManualCapabilityError(f"未知显式能力: ${requested}")


__all__ = [
    "ManualCapability",
    "ManualCapabilityError",
    "ScopedGraphTool",
    "feature_enabled",
    "graphs_enabled",
    "resolve_manual_capability",
    "self_evolution_enabled",
]
