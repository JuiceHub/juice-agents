"""
Runner / prebuilt 共享的 workspace 辅助工具。

该模块统一收口 workspace 目录推导和运行时路径计算。
配置管理逻辑已移至 ConfigurationContext，绑定逻辑已移至 workspace_binding.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.registry.tools.types import ToolRef
from juice_agents.core.registry import AgentRegistry, ToolRegistry
from juice_agents.core.graph.types import GraphConfig


@dataclass(frozen=True, slots=True)
class RuntimeWorkspace:
    """
    运行时 workspace 描述。

    - `workspace_dir is None`：不启用 workspace 限制，沿用默认开放行为。
    - `workspace_dir` 存在：运行文件和文件工具根目录都收敛到该目录。
    """

    workspace_dir: Path | None

    @property
    def enabled(self) -> bool:
        return self.workspace_dir is not None

    def writable_base_dir(self) -> Path:
        """运行时可写状态的根目录。

        ``workspace_dir`` 显式提供时即为真相源；未提供时回退到调用方的当前工作
        目录，与 ``ConfigurationContext`` 的回退口径保持一致。

        绝不能回退到 ``runtime_config_path`` 的父目录：默认配置来自 SDK 包内的
        ``juice_agents._assets``，wheel 安装后位于 site-packages，属于只读包数据。
        往那里写 team/runner 状态会污染安装目录（源码布局下则污染源码树）。
        """

        if self.workspace_dir is not None:
            return self.workspace_dir
        return Path.cwd().resolve()

    def juice_root(self, *, default_base_dir: str | Path) -> Path:
        base_dir = self.workspace_dir if self.workspace_dir is not None else Path(default_base_dir).resolve()
        return base_dir / ".juice"

    def todos_file(self, agent_name: str, *, default_base_dir: str | Path) -> Path:
        return self.juice_root(default_base_dir=default_base_dir) / "todos" / f"{agent_name}.md"

    def plan_file(self, agent_name: str, *, default_base_dir: str | Path) -> Path:
        return self.juice_root(default_base_dir=default_base_dir) / "plans" / f"{agent_name}.md"

    def runtime_async_tasks_dir(self, agent_name: str, *, default_base_dir: str | Path) -> Path:
        """Return the generic runtime task namespace for one managed Agent."""

        return self.juice_root(default_base_dir=default_base_dir) / "runtime" / "async_tasks" / agent_name

    def skills_dir(self, *, default_base_dir: str | Path) -> Path:
        return self.juice_root(default_base_dir=default_base_dir) / "skills"


def resolve_runtime_workspace(config: Any) -> RuntimeWorkspace:
    """
    从运行时配置中解析 workspace。

    `workspace_dir` 一旦显式提供，就在这里预先创建目录，避免文件工具在实例化时
    因根目录不存在而直接失败。
    """

    runtime_cfg = GraphConfig.from_any(config)
    runtime_cfg.validate()
    raw_workspace_dir = runtime_cfg.workspace_dir
    if raw_workspace_dir is None:
        return RuntimeWorkspace(workspace_dir=None)
    workspace_dir = Path(raw_workspace_dir).expanduser().resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return RuntimeWorkspace(workspace_dir=workspace_dir)


def build_runtime_agent_factory(
    runtime_workspace: RuntimeWorkspace,
    *,
    config_context: ConfigurationContext | None = None,
) -> AgentRegistry:
    ctx = config_context or ConfigurationContext.from_workspace(
        runtime_workspace.workspace_dir
    )
    return AgentRegistry(config_context=ctx)


def build_runtime_tool_factory(
    runtime_workspace: RuntimeWorkspace,
    *,
    config_context: ConfigurationContext | None = None,
) -> ToolRegistry:
    ctx = config_context or ConfigurationContext.from_workspace(
        runtime_workspace.workspace_dir
    )
    return ToolRegistry(config_context=ctx)


# Re-export bind_workspace_to_agent_config for backward compatibility
from juice_agents.core.runner.workspace_binding import (  # noqa: E402
    FILE_TOOL_NAMES,
    bind_workspace_to_agent_config,
)


__all__ = [
    "FILE_TOOL_NAMES",
    "RuntimeWorkspace",
    "resolve_runtime_workspace",
    "bind_workspace_to_agent_config",
    "build_runtime_agent_factory",
    "build_runtime_tool_factory",
]
