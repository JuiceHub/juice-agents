"""会话压缩策略。"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from .content import (
    DEFAULT_COMPACT_CONTINUATION_TEMPLATE,
    DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER,
    message_content_to_transcript_parts,
    text_block,
)
from .session import AgentSession, clone_session, clone_session_step, count_action_steps
from .steps import ActionStep, SessionStep, SummaryStep

logger = logging.getLogger(__name__)

_DEFAULT_SUMMARY_SYSTEM_PROMPT = """You are summarizing a conversation transcript. Produce a concise summary that preserves: the user's task, key decisions, current state, and what to do next. Output must be wrapped in a single <summary>...</summary> block. Do not include any text outside that block."""


class CompressStrategy(ABC):
    """会话压缩策略抽象基类：各子类维护自身参数，并实现是否压缩与如何压缩。"""

    @abstractmethod
    def should_compress(self, session: AgentSession) -> bool:
        """判断当前会话是否应触发压缩。"""

    @abstractmethod
    def compress(self, session: AgentSession) -> AgentSession:
        """执行压缩，返回新的 AgentSession（不修改原 session）。"""


class NoCompressStrategy(CompressStrategy):
    """不压缩：始终不触发，compress 返回原会话副本。"""

    def should_compress(self, session: AgentSession) -> bool:
        del session
        return False

    def compress(self, session: AgentSession) -> AgentSession:
        return clone_session(session)


class RemainCompressStrategy(CompressStrategy):
    """
    保留最近 k 轮观测的压缩：超出部分用 placeholder 替换观测内容。
    参数：k（保留的 ActionStep 数量）、placeholder（占位文案）。
    """

    def __init__(self, k: int = 5, placeholder: str | None = None):
        if not isinstance(k, int) or k < 0:
            raise ValueError("k 必须为非负整数")
        self.k = k
        self.placeholder = placeholder or DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER

    def should_compress(self, session: AgentSession) -> bool:
        return count_action_steps(session) > self.k

    def compress(self, session: AgentSession) -> AgentSession:
        logger.info("压缩会话: strategy=remain, k=%s", self.k)
        action_step_indices = [
            idx for idx, step in enumerate(session.steps) if isinstance(step, ActionStep)
        ]
        keep_indices = set(action_step_indices[-self.k:]) if self.k > 0 else set()
        compressed = AgentSession(
            system_prompt=session.system_prompt,
            include_reasoning_in_context=session.include_reasoning_in_context,
            system_prompt_static=session.system_prompt_static,
            system_prompt_dynamic=session.system_prompt_dynamic,
        )
        for idx, step in enumerate(session.steps):
            new_step = clone_session_step(step)
            if isinstance(new_step, ActionStep) and idx not in keep_indices:
                if new_step.observations or new_step.observations_images:
                    new_step.observations = [self.placeholder]
                    new_step.observations_images = []
            compressed.append_step(new_step)
        return compressed


class CompactCompressStrategy(CompressStrategy):
    """
    参考 Claude 风格的 compact：摘要较老前缀，同时保留最近若干 ActionStep 原文。

    参数：
    - step_threshold：达到该 ActionStep 数时允许触发压缩
    - compression_model：必须具备 `generate(messages)` 接口
    - compact_instructions：额外摘要指令
    - continuation_template：压缩后续写提示模板
    - preserve_recent_actions：默认保留最近 1 个 ActionStep；尾部含 error/update/attachments 时自动扩到 2 个
    """

    def __init__(
        self,
        step_threshold: int,
        compression_model: Any,
        compact_instructions: str | None = None,
        continuation_template: str | None = None,
        preserve_recent_actions: int = 1,
    ):
        if not isinstance(step_threshold, int) or step_threshold < 1:
            raise ValueError("step_threshold 必须为正整数")
        if compression_model is None or not callable(getattr(compression_model, "generate", None)):
            raise ValueError("compact 策略必须提供具备 generate(messages) 的 compression_model")
        if not isinstance(preserve_recent_actions, int) or preserve_recent_actions < 0:
            raise ValueError("preserve_recent_actions 必须为非负整数")
        self.step_threshold = step_threshold
        self.compression_model = compression_model
        self.compact_instructions = compact_instructions or ""
        self.continuation_template = continuation_template or DEFAULT_COMPACT_CONTINUATION_TEMPLATE
        self.preserve_recent_actions = preserve_recent_actions

    def should_compress(self, session: AgentSession) -> bool:
        if count_action_steps(session) < self.step_threshold:
            return False
        prefix_steps, _suffix_steps = _split_steps_for_compaction(
            session, preserve_recent_actions=self.preserve_recent_actions
        )
        if not prefix_steps:
            return False
        # 已经 compact 成 `SummaryStep + preserved suffix` 且没有新增历史时，不重复压缩。
        if len(prefix_steps) == 1 and isinstance(prefix_steps[0], SummaryStep):
            return False
        return True

    def compress(self, session: AgentSession) -> AgentSession:
        logger.info(
            "压缩会话: strategy=compact, step_threshold=%s, preserve_recent_actions=%s",
            self.step_threshold,
            self.preserve_recent_actions,
        )
        prefix_steps, suffix_steps = _split_steps_for_compaction(
            session, preserve_recent_actions=self.preserve_recent_actions
        )
        summary_text = _summarize_steps(
            session=session,
            steps=prefix_steps,
            compression_model=self.compression_model,
            compact_instructions=self.compact_instructions,
        )
        continuation_text = self.continuation_template.format(summary=summary_text)
        new_session = AgentSession(
            system_prompt=session.system_prompt,
            include_reasoning_in_context=session.include_reasoning_in_context,
            system_prompt_static=session.system_prompt_static,
            system_prompt_dynamic=session.system_prompt_dynamic,
        )
        new_session.append_step(SummaryStep(content=continuation_text))
        for step in suffix_steps:
            new_session.append_step(step)
        return new_session


def _split_steps_for_compaction(
    session: AgentSession,
    *,
    preserve_recent_actions: int,
) -> tuple[list[SessionStep], list[SessionStep]]:
    """
    按 ActionStep 粒度切分前缀与 recent suffix。

    这里不按单条 message 切，避免把一个 ActionStep 内部 assistant/user 成对消息拆开。
    """
    action_indices = [idx for idx, step in enumerate(session.steps) if isinstance(step, ActionStep)]
    if not action_indices:
        return [clone_session_step(step) for step in session.steps], []

    suffix_action_count = min(
        len(action_indices),
        _determine_suffix_action_count(session, preserve_recent_actions),
    )
    keep_indices = set(action_indices[-suffix_action_count:]) if suffix_action_count > 0 else set()

    prefix_steps: list[SessionStep] = []
    suffix_steps: list[SessionStep] = []
    for idx, step in enumerate(session.steps):
        cloned = clone_session_step(step)
        if idx in keep_indices and isinstance(cloned, ActionStep):
            suffix_steps.append(cloned)
            continue
        prefix_steps.append(cloned)
    return prefix_steps, suffix_steps


def _determine_suffix_action_count(session: AgentSession, preserve_recent_actions: int) -> int:
    """计算要保留多少个 recent ActionStep。"""
    base_count = max(0, preserve_recent_actions)
    if base_count == 0:
        return 0

    action_steps = [step for step in session.steps if isinstance(step, ActionStep)]
    if not action_steps:
        return 0
    last_action = action_steps[-1]
    if _tail_has_context_activity(last_action) and base_count < 2:
        return 2
    return base_count


def _tail_has_context_activity(step: ActionStep) -> bool:
    """尾部若还带 error/update/attachment，往前多保留一轮，避免续写丢失最近上下文。"""
    return bool(step.error or step.attachments)


def _summarize_steps(
    *,
    session: AgentSession,
    steps: list[SessionStep],
    compression_model: Any,
    compact_instructions: str,
) -> str:
    transcript = _build_transcript_for_steps(session, steps)
    system_for_compress = _DEFAULT_SUMMARY_SYSTEM_PROMPT
    if compact_instructions:
        system_for_compress = system_for_compress.rstrip() + "\n\n" + compact_instructions.strip()

    summarizer_messages = [
        {"role": "system", "content": [text_block(system_for_compress)]},
        {"role": "user", "content": [text_block("Summarize the following conversation transcript:\n\n" + transcript)]},
    ]

    try:
        response = compression_model.generate(summarizer_messages)
    except Exception as exc:  # pragma: no cover - 运行时保护
        logger.exception("压缩模型生成摘要失败")
        raise RuntimeError("compact 压缩模型调用失败") from exc

    response_content = response.get("content", "") if isinstance(response, dict) else str(response)
    summary_text = _extract_summary_from_content(response_content)
    if not summary_text:
        return response_content.strip() or "(summary unavailable)"
    return summary_text


def _build_transcript_for_steps(session: AgentSession, steps: list[SessionStep]) -> str:
    """只为被压缩前缀构建 transcript，不把 session 的 system prompt 混进摘要输入。"""
    transcript_parts: list[str] = []
    # 压缩模型也是模型输入，必须复用与普通会话完全相同的 observation 投影预算。
    # 临时 session 仅持有 step 引用；to_messages() 不修改 raw observation。
    projected = AgentSession(
        system_prompt="",
        include_reasoning_in_context=session.include_reasoning_in_context,
        steps=list(steps),
    )
    for message in projected.to_messages()[1:]:
        transcript_parts.extend(message_content_to_transcript_parts(message.get("content")))
    transcript = "\n\n".join(part for part in transcript_parts if str(part).strip()).strip()
    return transcript or "(no content)"


def _extract_summary_from_content(content: str) -> str:
    """从模型输出中提取 <summary>...</summary> 块内容。"""
    if not content:
        return ""
    match = re.search(r"<summary>\s*([\s\S]*?)</summary>", content, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return content.strip()


__all__ = [
    "CompressStrategy",
    "NoCompressStrategy",
    "RemainCompressStrategy",
    "CompactCompressStrategy",
]
