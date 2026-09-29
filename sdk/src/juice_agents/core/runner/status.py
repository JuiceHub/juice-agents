"""Runner 状态迁移的唯一写入口。

`RunnerState` 里 `status` / `status_reason` / `status_changed_at` / `active_round`
四个字段必须一起变更：只改 `status` 不更新 `status_changed_at` 会让审计时间线错乱，
漏清 `active_round` 会让已结束的回合看起来仍在运行。

此前 6 处调用点各自手写这四行赋值（`runner.py` 的 632/710/822/1106/1148/1208），
任何一处漏写一个字段都不会被类型检查发现。这里收敛为一个函数，让"四个字段同时
落定"成为结构上的保证，而不是靠调用方记得。

注意：本模块只改内存中的 state dict，**不负责落盘**。调用方在需要持久化时自行
调用 `persist_runner_state()`，以便把多次状态变更合并成一次写盘。
"""

from __future__ import annotations

import time
from typing import Any

from .types.definitions import RunnerState, RunnerStatus


def settle_status(
    state: RunnerState,
    *,
    status: RunnerStatus,
    reason: str,
    active_round: dict[str, Any] | None = None,
    at: float | None = None,
) -> None:
    """一次写齐四个状态字段。

    `active_round` 默认为 None，对应"回合已结束"这个绝大多数场景；只有回合开始时
    才显式传入 round 描述。

    `at` 允许调用方传入已持有的权威时间戳。回合开始时同一个 `turn_started_at`
    要同时出现在 `status_changed_at`、`active_round.started_at` 和 hook 载荷里，
    如果这里再取一次 `time.time()`，三者就会出现毫秒级漂移，turn 耗时统计也会偏。
    """

    state["status"] = status
    state["status_reason"] = str(reason or "").strip()
    state["status_changed_at"] = float(at) if at is not None else time.time()
    state["active_round"] = dict(active_round) if isinstance(active_round, dict) else None


def settle_async_pending_status(
    state: RunnerState,
    *,
    has_async: bool,
    waiting_reason: str = "async_pending",
    idle_reason: str = "round_ended",
) -> None:
    """按是否存在未完成后台工作，在 `waiting` 与 `idle` 之间收敛。

    reason 刻意保留为参数：resume 用 `pending_notification`、回合结束用
    `round_ended`、模式切换用 `reconfigured`，这些措辞是对外可见的审计信息，
    不能为了"统一"而抹平。
    """

    settle_status(
        state,
        status="waiting" if has_async else "idle",
        reason=waiting_reason if has_async else idle_reason,
    )


__all__ = ["settle_async_pending_status", "settle_status"]
