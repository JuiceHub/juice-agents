"""会话到 post-training 数据格式的转换器。

当前实现 OpenAI Chat Completions 格式：每条样本是 ``{"messages": [...], "metadata": {...}}``，
``messages`` 是严格 user/assistant 交替的纯文本序列，适用于所有支持 chat 模板的模型。

转换核心是把 juice-agents 的 ReAct/CodeAct 会话语义映射为 chat 消息：

```text
AgentSession.system_prompt   -> {"role": "system",    "content": <system_prompt>}
TaskStep                     -> {"role": "user",      "content": "<task>...</task>"}
ActionStep.model_output      -> {"role": "assistant", "content": <model_output>}
ActionStep.observations      -> {"role": "user",      "content": "<observations>...</observations>"}
SummaryStep                  -> {"role": "user",      "content": <summary>}
```

为保证 user/assistant 严格交替（post-training 框架的硬要求），转换后会合并相邻同 role
消息：例如「assistant(无观测) 紧跟下一轮 assistant」会被拼成一条 assistant 消息。
"""

from __future__ import annotations

from typing import Any

from juice_agents.core.agent.agent_type import ObservationImage
from juice_agents.core.agent.sessions import ActionStep, AgentSession, SummaryStep, TaskStep
from juice_agents.core.agent.sessions.content import truncate_text

from .filters import ExportConfig, should_keep_step


def _format_observations(step: ActionStep, config: ExportConfig) -> str:
    """把 ActionStep 的观测结果（文本 + 图片描述 + 错误）渲染为单条 user 文本。

    与 ``ActionStep.to_messages`` 的回填语义保持一致，但输出纯文本，并按
    ``truncate_observations`` 截断，避免单条观测把训练样本撑爆。图片无法进入纯文本
    序列，降级为 ``[image: <description>]`` 占位，保留语义线索。
    """
    parts: list[str] = []

    for idx, observation in enumerate(step.observations or []):
        text = truncate_text(str(observation), config.truncate_observations)
        parts.append(f"<result_of_action_{idx}>\n{text}\n</result_of_action_{idx}>")

    for img in step.observations_images or []:
        description = img.description if isinstance(img, ObservationImage) else ""
        parts.append(f"<observation_image>[image: {description}]</observation_image>")

    if step.error is not None:
        parts.append(f"<error>\n{step.error}\n</error>")

    if not parts:
        return ""
    return "<observations>\n" + "\n".join(parts) + "\n</observations>"


def _format_assistant(step: ActionStep, config: ExportConfig) -> str:
    """渲染 assistant 消息：可选前置 reasoning 块 + 模型原始输出。"""
    segments: list[str] = []
    if config.include_reasoning and step.reasoning_content:
        segments.append(f"<think>{step.reasoning_content}</think>")
    if step.model_output:
        segments.append(step.model_output)
    return "\n".join(segments).strip()


def _merge_adjacent_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """合并相邻同 role 消息，保证 user/assistant 严格交替。

    system 消息不参与合并（只应出现在序列头部）。合并时用换行拼接 content，
    丢弃合并后仍为空的消息。
    """
    merged: list[dict[str, str]] = []
    for message in messages:
        if not message.get("content", "").strip():
            continue
        if merged and merged[-1]["role"] == message["role"] and message["role"] != "system":
            merged[-1]["content"] = (merged[-1]["content"] + "\n" + message["content"]).strip()
        else:
            merged.append(dict(message))
    return merged


def session_to_messages(session: AgentSession, config: ExportConfig) -> list[dict[str, str]]:
    """把 AgentSession 转换为 OpenAI chat messages（已合并、严格交替）。"""
    raw: list[dict[str, str]] = []

    if config.include_system_prompt and session.system_prompt:
        raw.append({"role": "system", "content": session.system_prompt})

    for step in session.steps:
        if not should_keep_step(step, config):
            continue
        if isinstance(step, TaskStep):
            raw.append({"role": "user", "content": f"<task>{step.task}</task>"})
        elif isinstance(step, ActionStep):
            assistant_text = _format_assistant(step, config)
            if assistant_text:
                raw.append({"role": "assistant", "content": assistant_text})
            if config.include_observations:
                observations_text = _format_observations(step, config)
                if observations_text:
                    raw.append({"role": "user", "content": observations_text})
        elif isinstance(step, SummaryStep):
            raw.append({"role": "user", "content": step.content})

    return _merge_adjacent_messages(raw)


def session_to_sample(
    session: AgentSession,
    config: ExportConfig,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """把单个会话转换为一条训练样本。

    若转换后没有任何对话消息（只剩 system 或全空），返回 None 表示无可导出内容。
    """
    messages = session_to_messages(session, config)

    # 只有 system 或完全为空都视为无效样本。
    has_dialogue = any(message["role"] != "system" for message in messages)
    if not has_dialogue:
        return None

    sample: dict[str, Any] = {"messages": messages}
    if metadata:
        sample["metadata"] = dict(metadata)
    return sample


__all__ = [
    "session_to_messages",
    "session_to_sample",
]
