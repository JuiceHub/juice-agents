"""ToolConfigStore：Tool 配置持久化存储。"""

from __future__ import annotations

import logging
from typing import Any

from juice_agents.core.registry.base import _BaseConfigStore
from .types import ToolConfig

logger = logging.getLogger(__name__)

class ToolConfigStore(_BaseConfigStore):
    """
    Tool 配置存储。

    用法::

        context = ConfigurationContext.from_workspace(workspace_dir)
        store = ToolConfigStore(context.tools_dir)
        store.save(tool_config_dict)
    """

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(
        self,
        config: dict[str, Any] | ToolConfig,
        *,
        name: str | None = None,
    ) -> ToolConfig:
        """解析 + 持久化 tool 配置，返回标准化后的 ToolConfig。"""
        tool_config = ToolConfig.from_dict(config, config_name=name)
        self._save(tool_config.name, tool_config.to_dict())
        return tool_config

    def load(self, name: str) -> ToolConfig:
        """从 YAML 加载并返回 ToolConfig。"""
        data = self._load(name)
        return ToolConfig.from_dict(data, config_name=name)

    def list(self) -> list[str]:
        """列出所有已保存的 tool 配置名。"""
        return self._list()

    def delete(self, name: str) -> None:
        """删除指定 tool 配置。"""
        self._delete(name)

__all__ = ["ToolConfigStore"]
