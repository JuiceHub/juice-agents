"""Self-evolution write surface shared by assembly and permission layers.

这个集合恰好等于 `builtin/evolution/` 目录下的四个 manage 工具，也恰好等于
`self_evolution.enabled` 的作用域 —— 目录边界、权限边界与语义边界三者重合，
新增写工具时只要放进该目录并登记到这里，两层就不会分叉。

只读的 list/view 不在此列：它们随各自领域目录发布，受领域能力开关
（如 `skills_enabled`、`graphs.enabled`）约束，不受 `self_evolution` 约束。
"""

from __future__ import annotations

SELF_EVOLUTION_TOOL_NAMES = frozenset({
    "agent_manage",
    "tool_manage",
    "skill_manage",
    "graph_manage",
})

__all__ = ["SELF_EVOLUTION_TOOL_NAMES"]
