"""Small public data contracts for the declarative Runner boundary.

Runtime object state intentionally does not live here. ``ManagedAgent``,
``AsyncTaskManager`` and ``GraphRunManager`` each expose their own projection;
the Runner manifest below contains only request information.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from juice_agents.core.agent.sessions import ActionStep

PermissionMode = Literal["default", "accept"]
AgentMode = str
RunnerStatus = Literal["idle", "running", "waiting", "paused", "failed"]
AsyncTaskStatus = Literal["pending", "running", "completed", "failed", "killed"]
AsyncTaskType = Literal["local_agent", "local_bash", "local_graph"]

PERMISSION_MODES: frozenset[str] = frozenset({"default", "accept"})


def normalize_permission_mode(mode: Any) -> PermissionMode:
    """Validate the execution approval mode at the Runner boundary."""

    normalized = str(mode or "").strip() or "default"
    if normalized not in PERMISSION_MODES:
        raise ValueError(
            f"未知 permission mode: {normalized}; expected one of "
            f"{', '.join(sorted(PERMISSION_MODES))}"
        )
    return normalized  # type: ignore[return-value]


def normalize_agent_mode(mode: Any) -> AgentMode:
    """Validate a registered declarative ``RunnerConfig`` identifier."""

    normalized = str(mode or "").strip() or "agent"
    # Lazy import avoids config/type initialization cycles.
    from juice_agents.core.runner.config import mode_registry

    return mode_registry.resolve(normalized).mode_id


class AsyncTaskState(TypedDict, total=False):
    """Serializable external projection of one manager-owned task."""

    async_task_id: str
    type: AsyncTaskType
    status: AsyncTaskStatus
    owner_agent_id: str
    owner_agent_name: str
    description: str
    output_dir: str
    created_at: float
    started_at: float | None
    finished_at: float | None
    closed_reason: str
    error: str
    metadata: dict[str, Any]


class RunnerState(TypedDict, total=False):
    """Schema-5 Runner manifest, excluding all Manager internals."""

    schema_version: int
    runner_id: str
    permission_mode: PermissionMode
    agent_mode: AgentMode
    runner_config: dict[str, Any]
    status: RunnerStatus
    status_reason: str
    status_changed_at: float
    active_round: dict[str, Any] | None
    last_stop: dict[str, Any] | None
    root_agent_id: str
    root_agent_name: str
    team_name: str
    team_run_id: str
    disabled_agent_names_by_mode: dict[str, list[str]]
    plan_return_mode: str
    pending_plan_exit: dict[str, Any] | None
    first_user_request_preview: str
    created_at: float
    updated_at: float


class Attachment(TypedDict, total=False):
    """A plain request attachment delivered through ``RunnerContext``."""

    attachment_type: str
    created_at: float
    payload: dict[str, Any]


class RunnerStreamEvent(TypedDict, total=False):
    """The unified event emitted by every declarative Runner config."""

    kind: Literal["action_step", "runner_lifecycle", "round_end", "stream_cancelled"]
    permission_mode: PermissionMode
    mode_id: AgentMode
    runner_id: str
    agent_name: str | None
    agent_id: str | None
    agent_role: str | None
    step_num: int | None
    action_step: ActionStep | None
    lifecycle_event: dict[str, Any] | None
    round_id: str
    outcome: Literal["submitted", "yielded", "failed"]
    output: Any
    reason: str
    stop_reason: str


__all__ = [
    "PERMISSION_MODES",
    "AgentMode",
    "AsyncTaskState",
    "AsyncTaskStatus",
    "AsyncTaskType",
    "Attachment",
    "PermissionMode",
    "RunnerState",
    "RunnerStatus",
    "RunnerStreamEvent",
    "normalize_agent_mode",
    "normalize_permission_mode",
]
