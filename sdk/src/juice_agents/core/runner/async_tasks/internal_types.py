"""Runner 内核使用的低层类型。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from juice_agents.core.agent.attachments import AsyncTaskNotification, RuntimeEvent

AsyncTaskKind = Literal["agent_dispatch", "shell_command", "graph_run", "teammate"]
AsyncTaskStatus = Literal["pending", "running", "completed", "failed", "killed"]
# Scope kinds are declarative RunnerConfig identifiers, including registered
# custom configuration templates.
RunnerScopeKind = str
RunnerScopeStatus = Literal["running", "finalizing", "finalized"]
RunnerAgentKind = Literal[
    "teamlead",
    "teammate",
    "group_manager",
    "group_worker",
    "agent_owner",
]
RunnerAgentStatus = Literal["active", "idle", "shutdown_requested", "shutdown"]


class RunnerAgentState(TypedDict, total=False):
    """共享 agent 控制态。"""

    agent_id: str
    agent_name: str
    agent_kind: RunnerAgentKind
    status: RunnerAgentStatus
    current_async_task_id: str
    last_error: str
    metadata: dict[str, Any]


class AsyncTaskStateBase(TypedDict, total=False):
    """后台异步任务的公共字段。"""

    async_task_id: str
    kind: AsyncTaskKind
    owner_agent_id: str
    owner_agent_name: str
    status: AsyncTaskStatus
    description: str
    output_dir: str
    created_at: float
    started_at: float | None
    finished_at: float | None
    closed_reason: str
    error: str
    metadata: dict[str, Any]
    notification: dict[str, Any] | None


class AgentDispatchAsyncTaskState(AsyncTaskStateBase, total=False):
    kind: Literal["agent_dispatch"]


class ShellCommandAsyncTaskState(AsyncTaskStateBase, total=False):
    kind: Literal["shell_command"]


class TeammateAsyncTaskState(AsyncTaskStateBase, total=False):
    kind: Literal["teammate"]


class GraphRunAsyncTaskState(AsyncTaskStateBase, total=False):
    kind: Literal["graph_run"]


AsyncTaskState = (
    AgentDispatchAsyncTaskState
    | ShellCommandAsyncTaskState
    | GraphRunAsyncTaskState
    | TeammateAsyncTaskState
)


class AsyncTaskRuntimeState(TypedDict, total=False):
    """Runner 内核使用的 scoped async task registry。"""

    scope_id: str
    scope_kind: RunnerScopeKind
    scope_status: RunnerScopeStatus
    scope_meta: dict[str, Any]
    agents: dict[str, RunnerAgentState]
    async_tasks: list[AsyncTaskState]


class AsyncTaskLaunchReceipt(TypedDict, total=False):
    """后台异步任务启动回执。"""

    status: Literal["launched", "failed"]
    async_task_id: str
    kind: AsyncTaskKind
    summary: str
    output_dir: str
    error: str

__all__ = [
    "AgentDispatchAsyncTaskState",
    "AsyncTaskKind",
    "AsyncTaskLaunchReceipt",
    "AsyncTaskState",
    "AsyncTaskStateBase",
    "AsyncTaskStatus",
    "AsyncTaskNotification",
    "RunnerAgentKind",
    "RunnerAgentState",
    "RunnerAgentStatus",
    "AsyncTaskRuntimeState",
    "RunnerScopeKind",
    "RunnerScopeStatus",
    "RuntimeEvent",
    "GraphRunAsyncTaskState",
    "ShellCommandAsyncTaskState",
    "TeammateAsyncTaskState",
]
