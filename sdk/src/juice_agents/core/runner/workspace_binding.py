"""Workspace 绑定逻辑（纯函数）。

保留声明规范化入口；路径只在 ToolRegistry 创建运行时实例时绑定。
这样可移植声明不会混入当前机器的 workspace 绝对路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.registry.tools.registry import FILE_TOOL_NAMES
from juice_agents.core.runner.workspace import RuntimeWorkspace


def bind_workspace_to_agent_config(
    agent_config: dict[str, Any] | AgentConfig,
    *,
    runtime_workspace: RuntimeWorkspace,
    default_plan_base_dir: str | Path,
) -> dict[str, Any]:
    """
    规范化声明，工作区路径通过 Registry 的 ConfigurationContext 绑定。

    历史参数保留给现有组合入口使用；本函数不再复制工具路径推导逻辑。
    """

    normalized = AgentConfig.from_dict(agent_config).to_dict()

    for key in ("system_prompt", "log_file_path"):
        if normalized.get(key) is None:
            normalized.pop(key, None)
    if not normalized.get("additional_authorized_imports"):
        normalized.pop("additional_authorized_imports", None)

    return normalized


__all__ = [
    "FILE_TOOL_NAMES",
    "bind_workspace_to_agent_config",
]
