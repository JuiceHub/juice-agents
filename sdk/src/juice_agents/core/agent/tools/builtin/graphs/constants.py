"""Graph capability surface shared by assembly and permission layers.

`graphs.enabled` 与 `AgentConfig.enable_graph_tools` 都以这一个名字集合为作用域，
避免注册表、Group profile 与权限引擎各自维护一份会逐渐分叉的拷贝。

`graph_manage` 虽然物理上位于 `builtin/evolution/`，但它属于 Graph 能力面，
所以名字留在这里；它同时受 `self_evolution.enabled` 约束，见
`builtin/evolution/constants.py`。
"""

from __future__ import annotations

GRAPH_TOOL_NAMES = frozenset({"graph_list", "graph_view", "graph_manage", "graph_tool"})

__all__ = ["GRAPH_TOOL_NAMES"]
