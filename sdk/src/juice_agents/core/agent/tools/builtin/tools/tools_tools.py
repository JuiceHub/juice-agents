"""声明式 Tool YAML 的只读查询工具。

写入侧是 `builtin/evolution/tool_manage.py`，它继承这里的 `ToolConfigToolBase`
复用同一套 workspace / config_dir / registry 解析，保证读写看到同一份真值源。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.common import normalize_name

from ...runtime.base_tools import CONTENT_OBSERVATION_CHARS, LIST_OBSERVATION_CHARS, Tool
from ...runtime.owner_context import resolve_owner_context

logger = logging.getLogger(__name__)


class ToolConfigToolBase(Tool):
    """Shared workspace/registry resolution for both read and write tools."""

    def __init__(
        self,
        *,
        owner_agent: Any | None = None,
        workspace_dir: str | Path | None = None,
        tool_config_dir: str | Path | None = None,
        **_: Any,
    ) -> None:
        super().__init__()
        self.owner_agent = owner_agent
        self.workspace_dir = None if workspace_dir is None else Path(workspace_dir).resolve()
        self.tool_config_dir = None if tool_config_dir is None else Path(tool_config_dir).resolve()

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _context(self) -> ConfigurationContext:
        return resolve_owner_context(self.owner_agent, explicit_workspace_dir=self.workspace_dir)

    def _config_dir(self) -> Path:
        return self.tool_config_dir or self._context().tools_dir

    def _registry(self) -> ToolRegistry:
        # Imported lazily because ToolRegistry loads the default factory table,
        # which in turn registers these configuration tools.
        from juice_agents.core.registry.tools.registry import ToolRegistry

        return ToolRegistry(config_context=self._context(), config_dir=self._config_dir())


class ToolsListTool(ToolConfigToolBase):
    _execution_mode = "parallel_safe"
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "tools_list"
    is_read_only = True
    description = "列出 .juice/tools 中的声明式 Tool YAML。"
    inputs: dict[str, dict[str, Any]] = {}
    outputs = {"tools": {"type": "list", "description": "Tool 配置摘要"}}

    def forward(self) -> dict[str, Any]:
        return {
            "tools": [
                {"name": name, "source": "workspace", "path": str(self._config_dir() / f"{name}.yaml")}
                for name in self._registry().list_configs()
            ]
        }


class ToolViewTool(ToolConfigToolBase):
    _execution_mode = "parallel_safe"
    max_observation_chars = CONTENT_OBSERVATION_CHARS
    name = "tool_view"
    is_read_only = True
    description = "查看 workspace 声明式 Tool 的完整 YAML 配置。"
    inputs = {"name": {"type": "string", "description": "Tool 名称"}}
    outputs = {"tool": {"type": "object", "description": "来源、路径和完整配置"}}

    def forward(self, name: str) -> dict[str, Any]:
        normalized = normalize_name(name)
        try:
            config = self._registry().load_config(normalized)
        except FileNotFoundError:
            from juice_agents.core.registry.tools.defaults import get_default_tool_factories

            if normalized in get_default_tool_factories():
                return {
                    "tool": {
                        "name": normalized,
                        "source": "builtin",
                        "path": None,
                        "editable": False,
                    }
                }
            raise
        return {
            "tool": {
                "name": config.name,
                "source": "workspace",
                "path": str(self._config_dir() / f"{config.name}.yaml"),
                "editable": True,
                "config": config.to_dict(),
            }
        }


__all__ = ["ToolConfigToolBase", "ToolViewTool", "ToolsListTool"]
