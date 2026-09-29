"""会话容器与 clone 辅助函数。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List

from .content import normalize_observations, system_content_blocks, text_block
from .steps import ActionStep, SessionStep, SummaryStep, TaskStep

logger = logging.getLogger(__name__)


@dataclass
class AgentSession:
    """一次多轮对话的上下文。"""

    system_prompt: str
    include_reasoning_in_context: bool = False
    steps: List[SessionStep] = field(default_factory=list)
    # 分段 system prompt：静态前缀 + 动态后缀。两者均为 None 时退回单段 system_prompt，
    # 保持与旧行为完全一致（自定义模板、历史会话反序列化等场景）。
    system_prompt_static: str | None = None
    system_prompt_dynamic: str | None = None

    def append_step(self, step: SessionStep) -> None:
        logger.debug("追加会话步骤: %s", type(step).__name__)
        if isinstance(step, ActionStep):
            step.observations = normalize_observations(step.observations)
        self.steps.append(step)

    def _system_message(self) -> dict[str, Any]:
        """构造 system 消息：有分段则走缓存友好的双 block，否则退回单段。"""
        if self.system_prompt_static is not None:
            content = system_content_blocks(self.system_prompt_static, self.system_prompt_dynamic)
        else:
            content = [text_block(self.system_prompt)]
        return {"role": "system", "content": content}

    def to_messages(self) -> List[dict[str, Any]]:
        messages: List[dict[str, Any]] = [self._system_message()]
        for step in self.steps:
            if isinstance(step, ActionStep):
                messages.extend(
                    step.to_messages(
                        include_reasoning_in_context=self.include_reasoning_in_context,
                    )
                )
            else:
                messages.extend(step.to_messages())
        return messages

    def clone(self) -> "AgentSession":
        """
        复制会话历史。

        这里只复制“可继续推理”的上下文，不共享底层 step 列表引用，
        让 fresh agent 可以继承历史但不会反向污染原 session。
        """
        return clone_session(self)


def count_action_steps(session: AgentSession) -> int:
    """会话中 ActionStep 的数量。"""
    return sum(1 for step in session.steps if isinstance(step, ActionStep))


def clone_session_step(step: SessionStep) -> SessionStep:
    """复制单个 step，避免压缩或 clone 时共享可变引用。"""
    if isinstance(step, TaskStep):
        return TaskStep(
            task=step.task,
            task_images=list(step.task_images),
            attachments=[dict(item) for item in step.attachments if isinstance(item, dict)],
        )
    if isinstance(step, ActionStep):
        return ActionStep(
            step_num=step.step_num,
            model_output=step.model_output,
            thought=step.thought,
            tool_calls=[dict(item) for item in step.tool_calls if isinstance(item, dict)],
            code_action=step.code_action,
            reasoning_content=step.reasoning_content,
            observations=list(step.observations),
            observations_images=list(step.observations_images),
            attachments=[dict(item) for item in step.attachments if isinstance(item, dict)],
            error=step.error,
            observation_limits=list(step.observation_limits),
            request_bytes=step.request_bytes,
            usage=None if step.usage is None else dict(step.usage),
            round_outcome=step.round_outcome,
            output=step.output,
        )
    if isinstance(step, SummaryStep):
        return SummaryStep(content=step.content)
    return step


def clone_session(session: AgentSession) -> AgentSession:
    """深拷贝 AgentSession（仅 steps 内步进可写）。"""
    out = AgentSession(
        system_prompt=session.system_prompt,
        include_reasoning_in_context=session.include_reasoning_in_context,
        system_prompt_static=session.system_prompt_static,
        system_prompt_dynamic=session.system_prompt_dynamic,
    )
    for step in session.steps:
        out.append_step(clone_session_step(step))
    return out


class CompressibleAgentSession:
    """包装原始会话，按策略在 to_messages 前自动判断并执行压缩。"""

    def __init__(self, session: AgentSession, strategy: Any):
        self.session = session
        self.strategy = strategy

    def to_messages(self) -> List[dict[str, Any]]:
        if self.strategy.should_compress(self.session):
            projected = self.strategy.compress(self.session)
            # remain 永远只是请求投影，不能回写 canonical；compact 等持久化
            # 策略成功后才替换 canonical session。
            from .compression import RemainCompressStrategy

            if isinstance(self.strategy, RemainCompressStrategy):
                return projected.to_messages()
            self.session = projected
        return self.session.to_messages()


__all__ = [
    "AgentSession",
    "CompressibleAgentSession",
    "count_action_steps",
    "clone_session_step",
    "clone_session",
]
