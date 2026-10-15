"""Workflow：agent 可编写、可运行的确定性编排脚本。

脚本只决定调用哪些临时 Agent、怎么并行、怎么汇总；按函数调用：
不保存运行态，内部 agent 不持久化，只有 ``ctx.artifact`` 写出的产物落盘。详见同目录 README。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

# 惰性导出：避免 `import juice_agents.core.workflow` 时连带加载 Runner/Registry。
_EXPORTS = {
    "WorkflowContext": ("juice_agents.core.workflow.context", "WorkflowContext"),
    "WorkflowConfig": ("juice_agents.core.workflow.types", "WorkflowConfig"),
    "WorkflowBuildContext": ("juice_agents.core.workflow.types", "WorkflowBuildContext"),
    "WorkflowAgentDispatcher": ("juice_agents.core.workflow.types", "WorkflowAgentDispatcher"),
    "WorkflowError": ("juice_agents.core.workflow.types", "WorkflowError"),
    "WorkflowCancelledError": ("juice_agents.core.workflow.types", "WorkflowCancelledError"),
    "WorkflowTimeoutError": ("juice_agents.core.workflow.types", "WorkflowTimeoutError"),
    "FORBIDDEN_INNER_TOOLS": ("juice_agents.core.workflow.types", "FORBIDDEN_INNER_TOOLS"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


__all__ = list(_EXPORTS.keys())
