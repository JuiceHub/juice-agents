"""
Agent 声明态配置导出入口。

运行时对象到声明态配置的导出仍属于 serialization；声明态配置到运行时对象的
创建统一由 AgentRegistry.instantiate(...) 承担。
"""

from __future__ import annotations

from typing import Any

from .types import AgentConfig


def agent_to_config(agent: Any) -> AgentConfig:
    """
    从受支持的运行时 Agent 严格导出声明态 AgentConfig。

    只有通过 AgentRegistry 构建的实例才会带有 `_declared_agent_config`
    快照；没有快照时直接报错，避免猜测。
    """
    declared = getattr(agent, "_declared_agent_config", None)
    if not isinstance(declared, AgentConfig):
        raise ValueError(
            "当前 agent 不是由受支持的配置化入口构建，无法做严格导出。"
            "请使用 AgentRegistry 从声明态配置构建后再导出。"
        )
    return AgentConfig.from_dict(declared.to_dict())


def agent_to_dict(agent: Any) -> dict[str, Any]:
    """便捷导出 dict 形式的声明态配置。"""
    return agent_to_config(agent).to_dict()


__all__ = [
    "agent_to_config",
    "agent_to_dict",
]
