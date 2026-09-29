"""Registry 统一导出入口。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_ATTR_TO_MODULE = {
    "AgentRegistry": ".agents.registry",
    "ToolRegistry": ".tools.registry",
    "GraphRegistry": ".graphs.registry",
    "SkillRegistry": ".skills.registry",
    "PluginRegistry": ".plugins.registry",
    "TeamRegistry": ".teams.registry",
    "AgentConfig": ".agents.types",
    "AgentRef": ".agents.types",
    "RemainCompressionConfig": ".agents.types",
    "CompactCompressionConfig": ".agents.types",
    "SessionCompressionConfig": ".agents.types",
    "SkillMetadata": ".skills.types",
    "ToolConfig": ".tools.types",
    "ToolRef": ".tools.types",
    "PluginManifest": ".plugins.types",
    "PluginMetadata": ".plugins.types",
    "PluginRecord": ".plugins.types",
    "PluginConflict": ".plugins.types",
    "PluginConflictError": ".plugins.types",
    "TeamConfig": ".teams.types",
    "TeamManifest": ".teams.types",
    "TeamConfigStore": ".teams.store",
    "agent_to_config": ".agents.serialization",
    "agent_to_dict": ".agents.serialization",
}

__all__ = list(_ATTR_TO_MODULE.keys())


def __getattr__(name: str) -> Any:
    module_name = _ATTR_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    return getattr(module, name)
