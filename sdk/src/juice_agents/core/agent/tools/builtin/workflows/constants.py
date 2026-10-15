"""Workflow capability surface shared by assembly and permission layers.

`workflows.enabled` 与 `AgentConfig.enable_workflow_tools` 都以这一个名字集合为作用域，
两层共用同一份常量，避免一边新增工具另一边漏改。

`workflow_manage` 虽然物理上位于 `builtin/evolution/`，但它属于 Workflow 能力面，
所以同时受 `workflows.enabled`（这里）与 `self_evolution.enabled`（evolution 常量）约束。
"""

from __future__ import annotations

WORKFLOW_TOOL_NAMES = frozenset({"workflow_list", "workflow_view", "workflow_manage", "workflow_run"})

__all__ = ["WORKFLOW_TOOL_NAMES"]
