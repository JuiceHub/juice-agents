"""Workflow 运行时类型：错误、运行配置、构建上下文与 Agent 调度协议。

Workflow 是确定性编排脚本：脚本只决定调用哪些临时 Agent、怎么并行、怎么汇总，
真正的读写/搜索/联网都发生在受权限控制的 Agent 内部。本模块只放跨层共享的
纯数据类型，不包含任何执行逻辑。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.agents.types import AgentConfig


class WorkflowError(Exception):
    """Workflow 相关异常基类。"""


class WorkflowCancelledError(WorkflowError):
    """Workflow 被调用方协作取消（stop / Esc / Runner 释放）。"""


class WorkflowTimeoutError(WorkflowError):
    """Workflow 超出 ``timeout_seconds``。"""


# 内部临时 Agent 永远不能获得的工具。
#
# 内部 Agent 以 accept 权限运行（不逐次询问），所以凡是能「逃出 workflow 边界」
# 的工具都必须在这里硬禁止，不能依赖 metadata 声明：
# - 自进化写工具：会永久改变后续 Agent/Tool/Skill/Workflow 行为
# - workflow_* / agent_tool：递归编排，绕过本 run 的取消与并发边界
# - team_* / send_message：跨 Agent 通信，影响 run 之外的状态
# - ask / plan / exit_plan：需要用户交互或改变 Runner 模式，后台运行时没有交互通道
# - async_task_stop / worktree：操作 run 之外的运行时资源
FORBIDDEN_INNER_TOOLS: frozenset[str] = frozenset(
    {
        "agent_manage",
        "tool_manage",
        "skill_manage",
        "workflow_manage",
        "workflow_run",
        "workflow_list",
        "workflow_view",
        "agent_tool",
        "ask",
        "plan",
        "exit_plan",
        "async_task_stop",
        "send_message",
        "enter_worktree",
        "exit_worktree",
    }
)


def is_forbidden_inner_tool(name: str) -> bool:
    """团队工具以前缀判断，新增 team_* 工具时无需同步修改这里。"""

    normalized = str(name or "").strip()
    return normalized in FORBIDDEN_INNER_TOOLS or normalized.startswith("team_")


@dataclass(slots=True)
class WorkflowConfig:
    """单次 run 的运行限制。

    - max_workers: ``ctx.parallel`` / ``ctx.pipeline`` 的并发上限
    - timeout_seconds: 整个 run 的总超时；None 表示不限制
    - workspace_dir: 运行时 workspace（Runner 构建 RuntimeWorkspace 时也复用此字段）
    """

    max_workers: int = 8
    timeout_seconds: float | None = None
    workspace_dir: str | None = None

    @classmethod
    def from_any(cls, config: Any) -> "WorkflowConfig":
        """接受 None / dict / WorkflowConfig；dict 中的未知键直接忽略。"""

        if config is None:
            return cls()
        if isinstance(config, cls):
            return config
        if not isinstance(config, dict):
            raise TypeError("config 必须为 dict/WorkflowConfig/None")
        known = {key: config[key] for key in ("max_workers", "timeout_seconds", "workspace_dir") if key in config}
        return cls(**known)

    def validate(self) -> None:
        if isinstance(self.max_workers, bool) or not isinstance(self.max_workers, int) or self.max_workers <= 0:
            raise ValueError("max_workers 必须为正整数")
        if self.timeout_seconds is not None:
            if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
                raise ValueError("timeout_seconds 必须为正数或 None")
        if self.workspace_dir is not None:
            if not isinstance(self.workspace_dir, str) or not self.workspace_dir.strip():
                raise ValueError("workspace_dir 必须为非空 string 或 None")


class WorkflowAgentDispatcher(Protocol):
    """Workflow 能拿到的全部 Runner 能力。

    Workflow 不持有 Runner、AgentManager 或活 Agent；Runner 组合时提供这座窄桥：
    - ``resolve_agent``：按名字取已注册声明（含 mode/禁用可用性检查）
    - ``tool_is_read_only``：查询工具只读属性，用于只读 workflow 的约束
    - ``invoke_agent``：经 ``Runner.invoke_agent`` 调用，固定 functional + ephemeral + accept
    """

    @property
    def runner_id(self) -> str: ...

    def resolve_agent(self, agent_name: str) -> AgentConfig: ...

    def tool_is_read_only(self, tool_name: str) -> bool: ...

    def invoke_agent(self, spec: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class WorkflowBuildContext:
    """一次 run 显式可用的运行时服务。"""

    config_context: ConfigurationContext
    workspace_dir: Path | None
    runner_id: str = ""
    artifacts_dir: Path | None = None
    agent_dispatcher: WorkflowAgentDispatcher | None = None
    owner_agent_name: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)


__all__ = [
    "FORBIDDEN_INNER_TOOLS",
    "WorkflowAgentDispatcher",
    "WorkflowBuildContext",
    "WorkflowCancelledError",
    "WorkflowConfig",
    "WorkflowError",
    "WorkflowTimeoutError",
    "is_forbidden_inner_tool",
]
