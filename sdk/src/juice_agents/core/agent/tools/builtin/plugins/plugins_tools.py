"""Plugin 的只读发现与查看工具；Plugin V1 只贡献 Skills。

Plugin 没有 manage 工具，修改 workspace-local Plugin 一律复用通用文件工具，
因此本域完全不出现在 `builtin/evolution/` 里。list/view 服务 `$<plugin-name>`
的发现与执行，属于调用面。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.config.runtime_config import DEFAULT_RUNTIME_CONFIG_PATH
from juice_agents.core.registry.plugins import PluginRegistry

from ...runtime.base_tools import CONTENT_OBSERVATION_CHARS, LIST_OBSERVATION_CHARS, Tool

logger = logging.getLogger(__name__)


class PluginsListTool(Tool):
    """List plugin metadata without exposing any mutation path."""

    _execution_mode = "parallel_safe"
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "plugins_list"
    is_read_only = True
    description = "列出 workspace、project 和 user 来源的 Plugins、启用状态及诊断。"
    inputs: dict[str, dict[str, Any]] = {}
    outputs = {
        "plugins": {"type": "list", "description": "Plugin metadata"},
        "diagnostics": {"type": "list", "description": "非法或冲突诊断"},
    }

    def __init__(
        self,
        *,
        owner_agent: Any | None = None,
        workspace_dir: str | Path | None = None,
        config_path: str | Path | None = None,
        max_view_bytes: int = 1_048_576,
        **_: Any,
    ) -> None:
        super().__init__()
        self._view = PluginViewTool(
            owner_agent=owner_agent,
            workspace_dir=workspace_dir,
            config_path=config_path,
            max_view_bytes=max_view_bytes,
        )

    def bind_owner_agent(self, agent: Any) -> None:
        self._view.bind_owner_agent(agent)

    def forward(self) -> dict[str, Any]:
        registry = self._view._registry()
        return {
            "plugins": [item.to_dict() for item in registry.list(include_disabled=True)],
            "diagnostics": registry.diagnostics(),
        }


class PluginViewTool(Tool):
    """Read an enabled plugin manifest or one safe UTF-8 file."""

    _execution_mode = "parallel_safe"
    max_observation_chars = CONTENT_OBSERVATION_CHARS
    name = "plugin_view"
    is_read_only = True
    description = (
        "读取已启用 Plugin 的 manifest metadata 或内部文本文件；Plugin 当前仅加载 Skills，"
        "修改请使用通用文件工具创建 workspace-local 副本。"
    )
    inputs = {
        "name": {"type": "string", "description": "Plugin 名称"},
        "file_path": {"type": "string", "description": "Plugin 内相对路径", "required": False},
    }
    outputs = {"result": {"type": "object", "description": "Plugin metadata 或文件内容"}}

    def __init__(
        self,
        *,
        owner_agent: Any | None = None,
        workspace_dir: str | Path | None = None,
        config_path: str | Path | None = None,
        max_view_bytes: int = 1_048_576,
        **_: Any,
    ) -> None:
        super().__init__()
        self.owner_agent = owner_agent
        self.workspace_dir = None if workspace_dir is None else Path(workspace_dir).resolve()
        self.config_path = Path(config_path or DEFAULT_RUNTIME_CONFIG_PATH).resolve()
        self.max_view_bytes = max_view_bytes

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _workspace(self) -> Path:
        if self.workspace_dir is not None:
            return self.workspace_dir
        runner = getattr(getattr(self.owner_agent, "runner_context", None), "runner", None)
        context = getattr(runner, "config_context", None)
        workspace = getattr(context, "workspace_dir", None)
        if workspace is not None:
            return Path(workspace).resolve()
        declared = getattr(self.owner_agent, "_declared_config_context", None)
        declared_workspace = getattr(declared, "workspace_dir", None)
        if declared_workspace is not None:
            return Path(declared_workspace).resolve()
        return Path(getattr(self.owner_agent, "_runtime_base_dir", None) or Path.cwd()).resolve()

    def _registry(self) -> PluginRegistry:
        workspace = self._workspace()
        runner = getattr(getattr(self.owner_agent, "runner_context", None), "runner", None)
        runner_context = getattr(runner, "config_context", None)
        declared_context = getattr(self.owner_agent, "_declared_config_context", None)
        if isinstance(runner_context, ConfigurationContext):
            context = runner_context
        elif isinstance(declared_context, ConfigurationContext):
            context = declared_context
        else:
            context = ConfigurationContext.from_workspace(
                workspace,
                project_config_path=self.config_path,
            )
        return PluginRegistry(
            workspace_dir=workspace,
            project_dir=workspace,
            config_context=context,
            max_view_bytes=self.max_view_bytes,
        )

    def forward(self, name: str, file_path: str | None = None) -> dict[str, Any]:
        value = self._registry().view(name, file_path=file_path)
        if isinstance(value, str):
            return {"result": {"name": name, "file_path": file_path, "content": value}}
        return {"result": {"plugin": value}}


__all__ = ["PluginViewTool", "PluginsListTool"]
