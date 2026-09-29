"""Runner-level goal state, persistence and evaluator helpers.

这里的函数原先都以 `runner: Any` 收整个 Runner（stamp coupling）：签名完全没有
表达它们究竟要用 runner 的什么，读者必须逐行找，测试也只能塞一个完整 Runner。

实际用到的只有下面 `GoalHost` 声明的 8 个成员。改成窄 Protocol 后：
签名即依赖，测试可以用最小 stub，Runner 之外的宿主（如 evaluator 试验台）
也能满足这个协议。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Literal, Protocol, runtime_checkable

from juice_agents.core.utils import parse_model_json

GoalStatus = Literal["active", "paused", "completed", "budget_limited"]


@runtime_checkable
class GoalHost(Protocol):
    """goal 状态机对宿主的最小要求。

    刻意只声明真正被读写的成员：goal 逻辑不应该有能力碰 managed agent、async task
    或 mode-specific runtime。
    """

    # goal.json 的落盘位置来自 layout.goal_path。
    layout: Any
    # goal_ref 与 mode_ref.goal_control_action 都写在 runner state 里。
    state: dict[str, Any]

    def persist_runner_state(self) -> Any:
        """把内存 state 落盘。"""


# evidence 与 evaluator 另外会用 getattr 探测这几个**可选**成员，缺失时有降级路径，
# 因此不进 Protocol 的必需集合：
#   permission_mode / agent_mode / root_agent_name  —— 只用于 evidence 描述，缺失填空串
#   goal_evaluator                                  —— 测试注入的评审替身
#   evaluator_model                                 —— 缺失时回退到文本启发式判定

DEFAULT_MAX_TURNS = 12
DEFAULT_MAX_RUNTIME_SECONDS = 900
CONTINUATION_MESSAGE = "Continue working toward the active goal using the latest evaluator feedback."

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _goal_path(runner: GoalHost):
    return runner.layout.goal_path


def _read_goal(path: Any) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    payload = json.loads(text)
    return dict(payload) if isinstance(payload, dict) else {}


def _write_goal(runner: GoalHost, goal: dict[str, Any]) -> dict[str, Any]:
    path = _goal_path(runner)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(goal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    runner.state["goal_ref"] = goal_summary(goal)
    runner.persist_runner_state()
    return goal


def load_goal(runner: GoalHost) -> dict[str, Any]:
    """Load the current runner goal from its private store."""

    return _read_goal(_goal_path(runner))


def goal_summary(goal: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the public goal summary shape."""

    if not isinstance(goal, dict) or not goal:
        return None
    objective = str(goal.get("objective") or "").strip()
    return {
        "objective": objective,
        "status": str(goal.get("status") or "").strip() or "paused",
        "turns_used": int(goal.get("turns_used") or 0),
        "max_turns": int(goal.get("max_turns") or DEFAULT_MAX_TURNS),
        "elapsed_seconds": float(goal.get("elapsed_seconds") or 0.0),
        "max_runtime_seconds": float(goal.get("max_runtime_seconds") or DEFAULT_MAX_RUNTIME_SECONDS),
        "latest_evaluator_reason": str(goal.get("latest_evaluator_reason") or "").strip(),
        "pause_reason": str(goal.get("pause_reason") or "").strip(),
    }


def current_goal_summary(runner: GoalHost) -> dict[str, Any] | None:
    return goal_summary(load_goal(runner))


def set_goal(
    runner: GoalHost,
    objective: str,
    *,
    max_turns: int | None = None,
    max_runtime_seconds: int | float | None = None,
) -> dict[str, Any]:
    """Create or replace the runner goal."""

    normalized_objective = str(objective or "").strip()
    if not normalized_objective:
        raise ValueError("goal objective cannot be empty")
    now = _now()
    goal = {
        "objective": normalized_objective,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "started_at": now,
        "turns_used": 0,
        "max_turns": int(max_turns or DEFAULT_MAX_TURNS),
        "elapsed_seconds": 0.0,
        "max_runtime_seconds": float(max_runtime_seconds or DEFAULT_MAX_RUNTIME_SECONDS),
        "latest_evaluator_reason": "",
        "latest_evaluator_completed": False,
        "last_progress_fingerprint": "",
        "same_progress_count": 0,
        "pause_reason": "",
        "driver_scope": "all",
        "pending_evaluation_attachment": {},
        "last_evidence_fingerprint": "",
        "control_action": "continue",
    }
    return _write_goal(runner, goal)


def clear_goal(runner: GoalHost) -> dict[str, Any]:
    """Clear the current runner goal."""

    path = _goal_path(runner)
    if path.exists():
        path.unlink()
    runner.state["goal_ref"] = None
    runner.state.setdefault("mode_ref", {}).pop("goal_control_action", None)
    runner.persist_runner_state()
    return {"status": "cleared"}


def pause_goal(
    runner: GoalHost,
    *,
    reason: str = "user_paused",
    error: str = "",
    status: GoalStatus = "paused",
) -> dict[str, Any]:
    """Pause or terminally stop the current goal."""

    goal = load_goal(runner)
    if not goal:
        return {}
    goal["status"] = status
    goal["pause_reason"] = str(reason or "").strip()
    if error:
        goal["latest_evaluator_reason"] = str(error)
    goal["updated_at"] = _now()
    goal["control_action"] = status if status in {"completed", "budget_limited"} else "paused"
    return _write_goal(runner, goal)


def resume_goal(runner: GoalHost) -> dict[str, Any]:
    """Resume a paused goal."""

    goal = load_goal(runner)
    if not goal:
        raise ValueError("no goal to resume")
    goal["status"] = "active"
    goal["pause_reason"] = ""
    goal["updated_at"] = _now()
    goal["control_action"] = "continue"
    return _write_goal(runner, goal)


def consume_goal_control_action(runner: GoalHost) -> str:
    """Consume the latest runtime control action emitted by the goal hook."""

    mode_ref = runner.state.setdefault("mode_ref", {})
    action = str(mode_ref.pop("goal_control_action", "") or "").strip()
    runner.persist_runner_state()
    return action or "none"


def set_goal_control_action(runner: GoalHost, action: str) -> None:
    runner.state.setdefault("mode_ref", {})["goal_control_action"] = str(action or "none")
    runner.persist_runner_state()


def build_goal_state_attachment(runner: GoalHost) -> dict[str, Any] | None:
    goal = load_goal(runner)
    if str(goal.get("status") or "") != "active":
        return None
    return {
        "attachment_type": "goal_state",
        "created_at": _now(),
        "payload": {
            "objective": str(goal.get("objective") or ""),
            "status": str(goal.get("status") or ""),
            "turns_used": int(goal.get("turns_used") or 0),
            "max_turns": int(goal.get("max_turns") or DEFAULT_MAX_TURNS),
            "elapsed_seconds": float(goal.get("elapsed_seconds") or 0.0),
            "max_runtime_seconds": float(goal.get("max_runtime_seconds") or DEFAULT_MAX_RUNTIME_SECONDS),
        },
    }


def consume_goal_runtime_attachments(runner: GoalHost, *, step_idx: int) -> list[dict[str, Any]]:
    """Return goal attachments for step 0 without mutating project files."""

    if step_idx != 0:
        return []
    attachments: list[dict[str, Any]] = []
    goal = load_goal(runner)
    if not goal or str(goal.get("status") or "") != "active":
        return []
    pending = dict(goal.get("pending_evaluation_attachment") or {})
    if pending:
        attachments.append(
            {
                "attachment_type": "goal_evaluation",
                "created_at": _now(),
                "payload": pending,
            }
        )
        goal["pending_evaluation_attachment"] = {}
        _write_goal(runner, goal)
    state_attachment = build_goal_state_attachment(runner)
    if state_attachment:
        attachments.append(state_attachment)
    return attachments


def _hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_goal_evidence(runner: GoalHost, ctx: dict[str, Any]) -> dict[str, Any]:
    """Build a read-only evidence bundle for the hidden goal evaluator."""

    stream_events = list(ctx.get("stream_events") or [])
    agent_sessions_report = dict(ctx.get("agent_sessions_report") or {})
    public_async_tasks = list(ctx.get("public_async_tasks") or [])
    final_output = str(ctx.get("final_output") or "")
    return {
        "objective": str(load_goal(runner).get("objective") or ""),
        "permission_mode": str(getattr(runner, "permission_mode", "") or ""),
        "agent_mode": str(getattr(runner, "agent_mode", "") or ""),
        "root_agent_name": str(getattr(runner, "root_agent_name", "") or ""),
        "final_output": final_output,
        "stream_event_count": len(stream_events),
        "agent_sessions_report": agent_sessions_report,
        "public_async_tasks": public_async_tasks,
        "budget": goal_summary(load_goal(runner)) or {},
    }


def _model_content(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("content") or response.get("output") or "")
    return str(response or "")


def _default_verdict_from_text(evidence: dict[str, Any]) -> dict[str, Any]:
    """Fallback verdict when no model is available; it keeps the goal active."""

    fingerprint = _hash_payload(
        {
            "final_output": evidence.get("final_output"),
            "tasks": evidence.get("public_async_tasks"),
            "agents": evidence.get("agent_sessions_report"),
        }
    )
    return {
        "completed": False,
        "reason": "goal evaluator model is unavailable",
        "should_continue": True,
        "progress_fingerprint": fingerprint,
    }


def run_goal_evaluator(runner: GoalHost, evidence: dict[str, Any]) -> dict[str, Any]:
    """Run the hidden evaluator using an independent lightweight model or a test override."""

    override = getattr(runner, "goal_evaluator", None)
    if callable(override):
        verdict = override(evidence)
        return dict(verdict or {})

    # Use the independent evaluator model (lightweight, avoids self-evaluation bias)
    model = getattr(runner, "evaluator_model", None)
    if model is None or not callable(getattr(model, "generate", None)):
        return _default_verdict_from_text(evidence)

    system = (
        "You are the hidden Juice goal evaluator. Decide whether the active goal is complete "
        "using only the provided evidence. Return only JSON with keys completed, reason, "
        "should_continue, progress_fingerprint."
    )
    user = json.dumps(evidence, ensure_ascii=False, default=str)
    response = model.generate(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    parsed = parse_model_json(_model_content(response), expected_type=dict, field_name="goal_evaluator")
    return dict(parsed)


def _normalize_verdict(verdict: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    completed = bool(verdict.get("completed"))
    reason = str(verdict.get("reason") or "").strip()
    if not reason:
        reason = "goal completed" if completed else "goal not completed"
    fingerprint = str(verdict.get("progress_fingerprint") or "").strip()
    if not fingerprint:
        fingerprint = _hash_payload(evidence)
    return {
        "completed": completed,
        "reason": reason,
        "should_continue": bool(verdict.get("should_continue", not completed)),
        "progress_fingerprint": fingerprint,
    }


def evaluate_goal_after_turn(runner: GoalHost, ctx: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the active goal and persist the next control action."""

    goal = load_goal(runner)
    if str(goal.get("status") or "") != "active":
        set_goal_control_action(runner, "none")
        return {"goal_action": "none", "summary": "no active goal"}

    now = _now()
    goal["turns_used"] = int(goal.get("turns_used") or 0) + 1
    started_at = float(goal.get("started_at") or goal.get("created_at") or now)
    goal["elapsed_seconds"] = max(0.0, now - started_at)

    evidence = build_goal_evidence(runner, ctx)
    evidence_fingerprint = _hash_payload(evidence)
    verdict = _normalize_verdict(run_goal_evaluator(runner, evidence), evidence)

    previous_progress = str(goal.get("last_progress_fingerprint") or "")
    same_progress_count = int(goal.get("same_progress_count") or 0)
    if previous_progress and previous_progress == verdict["progress_fingerprint"]:
        same_progress_count += 1
    else:
        same_progress_count = 0

    goal["latest_evaluator_completed"] = bool(verdict["completed"])
    goal["latest_evaluator_reason"] = str(verdict["reason"])
    goal["last_progress_fingerprint"] = str(verdict["progress_fingerprint"])
    goal["same_progress_count"] = same_progress_count
    goal["last_evidence_fingerprint"] = evidence_fingerprint
    goal["pending_evaluation_attachment"] = {
        "completed": bool(verdict["completed"]),
        "reason": str(verdict["reason"]),
        "should_continue": bool(verdict["should_continue"]),
        "progress_fingerprint": str(verdict["progress_fingerprint"]),
    }
    goal["updated_at"] = now

    action = "continue"
    if verdict["completed"]:
        goal["status"] = "completed"
        action = "completed"
    elif int(goal.get("turns_used") or 0) >= int(goal.get("max_turns") or DEFAULT_MAX_TURNS):
        goal["status"] = "budget_limited"
        goal["pause_reason"] = "max_turns"
        action = "budget_limited"
    elif float(goal.get("elapsed_seconds") or 0.0) >= float(goal.get("max_runtime_seconds") or DEFAULT_MAX_RUNTIME_SECONDS):
        goal["status"] = "budget_limited"
        goal["pause_reason"] = "max_runtime_seconds"
        action = "budget_limited"
    elif same_progress_count >= 2:
        goal["status"] = "paused"
        goal["pause_reason"] = "stalled"
        action = "paused"
    elif not verdict["should_continue"]:
        goal["status"] = "paused"
        goal["pause_reason"] = "evaluator_requested_stop"
        action = "paused"

    goal["control_action"] = action
    _write_goal(runner, goal)
    set_goal_control_action(runner, action)
    return {"goal_action": action, "summary": str(goal.get("latest_evaluator_reason") or "")}


__all__ = [
    "CONTINUATION_MESSAGE",
    "DEFAULT_MAX_RUNTIME_SECONDS",
    "DEFAULT_MAX_TURNS",
    "GoalHost",
    "GoalStatus",
    "build_goal_evidence",
    "build_goal_state_attachment",
    "clear_goal",
    "consume_goal_control_action",
    "consume_goal_runtime_attachments",
    "current_goal_summary",
    "evaluate_goal_after_turn",
    "goal_summary",
    "load_goal",
    "pause_goal",
    "resume_goal",
    "run_goal_evaluator",
    "set_goal_control_action",
    "set_goal",
]
