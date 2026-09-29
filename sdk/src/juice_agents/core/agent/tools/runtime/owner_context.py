"""Shared context resolution for owner-bound builtin tools.

五个配置工具基类 (AgentConfigToolBase, ToolConfigToolBase, SkillToolBase,
GraphToolBase, 以及 evolution/ 里四个 manage) 继承自 Tool，都需要从
owner_agent 推导 workspace context 以解析 `.juice` 配置位置。

降级链（按优先级）：
1. 构造参数 workspace_dir（显式覆盖，测试常用）
2. runner.config_context（运行时权威 context，Group/Team 注入可信值）
3. owner_agent._declared_config_context（Agent 配置声明的 context）
4. owner_agent._runtime_base_dir（Manager 绑定的 workspace）
5. Path.cwd()（最后兜底，非隔离 import 时可能落这里）

旧实现在五个基类各写一遍且 SkillToolBase 跳过了第 3 级。现抽取为单一函数，
复用逻辑并修正 skills 的不一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from juice_agents.core.config.context import ConfigurationContext


def resolve_owner_context(
    owner_agent: Any,
    *,
    explicit_workspace_dir: Path | None = None,
) -> ConfigurationContext:
    """从 owner_agent 解析 workspace ConfigurationContext，按五级降级链。

    Args:
        owner_agent: 绑定的 Agent 实例（可能附带 runner_context）
        explicit_workspace_dir: 构造参数显式传入的 workspace（优先级最高）

    Returns:
        ConfigurationContext 实例（.juice_root 等配置目录的根）
    """
    from juice_agents.core.config.context import ConfigurationContext

    # 1. 构造参数显式传入（测试常用）
    if explicit_workspace_dir is not None:
        return ConfigurationContext.from_workspace(explicit_workspace_dir)

    # 2. runner.config_context（权威，Group/Team 注入）
    runner_context = getattr(owner_agent, "runner_context", None)
    runner = getattr(runner_context, "runner", None)
    config_context = getattr(runner, "config_context", None)
    if isinstance(config_context, ConfigurationContext):
        return config_context

    # 3. owner_agent._declared_config_context（Agent 配置声明）
    declared = getattr(owner_agent, "_declared_config_context", None)
    if isinstance(declared, ConfigurationContext):
        return declared

    # 4. Manager-bound workspace base directory
    base_dir = getattr(owner_agent, "_runtime_base_dir", None)

    # 5. Path.cwd()（最后兜底）
    return ConfigurationContext.from_workspace(base_dir or Path.cwd())


__all__ = ["resolve_owner_context"]
