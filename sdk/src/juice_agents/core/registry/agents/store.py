"""AgentConfigStore：Agent 配置持久化存储。"""

from __future__ import annotations

import logging
from typing import Any

from juice_agents.core.registry.base import _BaseConfigStore

from .types import AgentConfig

logger = logging.getLogger(__name__)

class AgentConfigStore(_BaseConfigStore):
    """
    Agent 配置存储。

    用法::

        context = ConfigurationContext.from_workspace(workspace_dir)
        store = AgentConfigStore(context.agents_dir)
        store.save({"name": "researcher", "agent_type": "react", ...})
        config = store.load("researcher")
    """

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(
        self,
        config: dict[str, Any] | AgentConfig,
        *,
        name: str | None = None,
    ) -> AgentConfig:
        """
        解析 + 持久化 agent 配置，返回标准化后的 AgentConfig。
        name 参数可选：若 config 中已含 name 则以其为准，否则用此参数。
        """
        agent_config = AgentConfig.from_dict(config, config_name=name)
        self._save(agent_config.name, agent_config.to_persisted_dict())
        return agent_config

    def load(self, name: str) -> AgentConfig:
        """从 YAML 加载并返回 AgentConfig。"""
        data = self._load(name)
        return AgentConfig.from_dict(data, config_name=name)

    def list(self) -> list[str]:
        """列出所有已保存的 agent 配置名。"""
        return self._list()

    def delete(self, name: str) -> None:
        """删除指定 agent 配置。"""
        self._delete(name)

__all__ = [
    "AgentConfigStore",
]
