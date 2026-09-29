"""轨迹导出过滤与内容控制配置。

`ExportConfig` 把「保留哪些会话」与「会话内保留哪些内容」两类决策集中到一个
不可变配置对象里，避免转换器和导出器各自散落判断逻辑。默认值是「完整保留」：
所有会话、所有内容都导出，调用方按需收紧。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from juice_agents.core.agent.sessions import ActionStep, AgentSession, SummaryStep, TaskStep


@dataclass(frozen=True, slots=True)
class ExportConfig:
    """轨迹导出配置。

    分为三层：

    1. 会话级过滤（决定整段会话是否导出）：
       - ``success_only``：只导出「最后一个 ActionStep 是 terminal 且无 error」的会话。
       - ``min_action_steps`` / ``max_action_steps``：按 ActionStep 数量过滤会话。

    2. 内容级开关（决定会话内每个 step 贡献哪些消息）：
       - ``include_system_prompt``：是否输出 system 消息。
       - ``include_observations``：是否把观测结果作为 user 消息回填。
       - ``include_reasoning``：assistant 消息是否前置 ``<think>`` 推理块。
       - ``include_summary_steps``：是否保留 compact 压缩产生的 SummaryStep。

    3. 清理选项（裁剪噪声）：
       - ``truncate_observations``：单条观测最大字符数，``None`` 表示不截断。
       - ``drop_error_steps``：丢弃带 error 的 ActionStep（不影响会话整体保留判断）。
    """

    # —— 会话级过滤 ——
    success_only: bool = False
    min_action_steps: int = 0
    max_action_steps: int | None = None

    # —— 内容级开关 ——
    include_system_prompt: bool = True
    include_observations: bool = True
    include_reasoning: bool = False
    include_summary_steps: bool = True

    # —— 清理选项 ——
    truncate_observations: int | None = None
    drop_error_steps: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ExportConfig":
        """从普通 dict 构造配置，忽略未知字段，方便外部 JSON/YAML 传参。"""
        data = dict(payload or {})
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        filtered = {key: value for key, value in data.items() if key in known}
        return cls(**filtered)


def _count_action_steps(session: AgentSession) -> int:
    """统计会话中的 ActionStep 数量。"""
    return sum(1 for step in session.steps if isinstance(step, ActionStep))


def _session_is_successful(session: AgentSession) -> bool:
    """判断会话是否成功结束。

    成功定义：存在至少一个 ActionStep，且最后一个 ActionStep 已提交且无 error。
    没有 ActionStep（纯 task 占位）视为未成功。
    """
    last_action: ActionStep | None = None
    for step in session.steps:
        if isinstance(step, ActionStep):
            last_action = step
    if last_action is None:
        return False
    return last_action.round_outcome == "submitted" and last_action.error is None


def should_export_session(session: AgentSession, config: ExportConfig) -> bool:
    """会话级过滤：根据配置判断整段会话是否应被导出。"""
    if config.success_only and not _session_is_successful(session):
        return False

    action_count = _count_action_steps(session)
    if action_count < config.min_action_steps:
        return False
    if config.max_action_steps is not None and action_count > config.max_action_steps:
        return False
    return True


def should_keep_step(step: Any, config: ExportConfig) -> bool:
    """step 级过滤：根据配置判断单个 step 是否应贡献消息。"""
    if isinstance(step, SummaryStep) and not config.include_summary_steps:
        return False
    if isinstance(step, ActionStep) and config.drop_error_steps and step.error is not None:
        return False
    return True


__all__ = [
    "ExportConfig",
    "should_export_session",
    "should_keep_step",
]
