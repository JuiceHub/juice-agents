"""会话步骤数据结构与消息序列化。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal

from ..agent_type import ObservationImage
from .content import (
    DEFAULT_OBSERVATION_PROJECTION_CHARS,
    ImageType,
    attachments_content_blocks,
    image_block,
    normalize_observations,
    text_block,
    truncate_text,
)
from .errors import AgentError


def normalize_tool_calls(value: Any) -> list[dict[str, Any]]:
    """把外部输入规整为 JSON 友好的工具调用展示记录。"""
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


class SessionStep:
    """单轮对话步骤抽象类。"""

    def to_messages(self) -> List[dict[str, Any]]:
        raise NotImplementedError


@dataclass
class SummaryStep(SessionStep):
    """
    compact 压缩后的摘要步骤：仅生成一条 user 消息，承载摘要与续写说明。
    用于替代被压缩的历史步骤序列。
    """

    content: str

    def to_messages(self) -> List[dict[str, Any]]:
        return [{"role": "user", "content": [text_block(self.content)]}]


@dataclass
class TaskStep(SessionStep):
    """用户任务描述步骤。"""

    task: str
    task_images: List[ImageType] = field(default_factory=list)
    attachments: List[dict[str, Any]] = field(default_factory=list)

    def to_messages(self) -> List[dict[str, Any]]:
        content = [text_block(f"<task>{self.task}</task>")]
        for idx, img in enumerate(self.task_images, start=1):
            content.append(text_block(f"<task_image_{idx}>"))
            content.append(image_block(img))
            content.append(text_block(f"</task_image_{idx}>"))
        content.extend(attachments_content_blocks(self.attachments))
        return [{"role": "user", "content": content}]


@dataclass
class ActionStep(SessionStep):
    """一次工具调用与观测的完整记录。"""

    step_num: int
    model_output: str
    thought: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    code_action: str = ""
    reasoning_content: str = ""
    observations: List[str] = field(default_factory=list)
    observations_images: List[ObservationImage] = field(default_factory=list)
    attachments: List[dict[str, Any]] = field(default_factory=list)
    error: AgentError | None = None
    # canonical observation 始终保留完整文本；该列表只记录逐条模型投影上限。
    # 与 observations 按索引对应，旧记录缺失时使用平衡档 12K。
    observation_limits: List[int] = field(default_factory=list)
    # 保存本轮真实模型请求的 UTF-8 字节数与 provider usage，供下一轮校准
    # context token 估算；usage 保留 provider 返回的完整归一化结构。
    request_bytes: int = 0
    usage: dict[str, Any] | None = None
    # step 只描述当前 round 的推进结果，不再承担 Runner 生命周期语义。
    # submitted/yielded/failed 都会结束当前 round；continue 才允许下一次模型调用。
    round_outcome: Literal["continue", "submitted", "yielded", "failed"] = "continue"
    output: Any = None

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "model_output":
            object.__setattr__(self, name, "" if value is None else str(value))
            return

        if name in {"thought", "code_action", "reasoning_content"}:
            object.__setattr__(self, name, "" if value is None else str(value))
            return

        if name == "tool_calls":
            object.__setattr__(self, name, normalize_tool_calls(value))
            return

        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_output", "" if self.model_output is None else str(self.model_output))
        self.thought = "" if self.thought is None else str(self.thought)
        self.tool_calls = normalize_tool_calls(self.tool_calls)
        self.code_action = "" if self.code_action is None else str(self.code_action)
        self.reasoning_content = "" if self.reasoning_content is None else str(self.reasoning_content)
        self.observations = normalize_observations(self.observations)
        normalized_limits: list[int] = []
        for raw_limit in list(self.observation_limits or []):
            if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit <= 0:
                # 保留列表位置，避免损坏的第一项被删除后让第二项限额错配到
                # observation[0]；旧/坏数据按正文工具平衡档安全回退。
                normalized_limits.append(DEFAULT_OBSERVATION_PROJECTION_CHARS)
            else:
                normalized_limits.append(raw_limit)
        self.observation_limits = normalized_limits
        if isinstance(self.request_bytes, bool) or not isinstance(self.request_bytes, int):
            self.request_bytes = 0
        self.request_bytes = max(0, self.request_bytes)
        if self.usage is not None and not isinstance(self.usage, dict):
            self.usage = None
        if self.round_outcome not in {"continue", "submitted", "yielded", "failed"}:
            raise ValueError(f"非法 round_outcome: {self.round_outcome!r}")

    def to_messages(
        self,
        *,
        include_reasoning_in_context: bool = False,
    ) -> List[dict[str, Any]]:
        messages: List[dict[str, Any]] = []

        assistant_content: List[dict[str, Any]] = []
        if include_reasoning_in_context and self.reasoning_content:
            assistant_content.append(text_block(f"<think>{self.reasoning_content}</think>"))
        if self.model_output:
            assistant_content.append(text_block(self.model_output))
        if assistant_content:
            messages.append({"role": "assistant", "content": assistant_content})

        feedback_content: List[dict[str, Any]] = []
        observations = normalize_observations(self.observations)
        if observations or self.observations_images:
            feedback_content.append(text_block("\n<observations>\n"))
            for idx, part in enumerate(observations):
                allowed = (
                    self.observation_limits[idx]
                    if idx < len(self.observation_limits)
                    else DEFAULT_OBSERVATION_PROJECTION_CHARS
                )
                feedback_content.append(text_block(f"<result_of_action_{idx}>\n"))
                projected_part = truncate_text(part, allowed)
                feedback_content.append(text_block(projected_part))
                # 分隔换行并入闭合标签，避免产生只有 "\n" 的空白 text block；
                # observation 正文仍严格不超过它自己的有效字符限额。
                close_prefix = "" if projected_part.endswith("\n") else "\n"
                feedback_content.append(
                    text_block(f"{close_prefix}</result_of_action_{idx}>\n")
                )
            for idx, img in enumerate(self.observations_images, start=1):
                desc = img.description if isinstance(img, ObservationImage) else ""
                feedback_content.append(
                    text_block(f"<observation_image_{idx}>\n<description>{desc}</description>\n")
                )
                feedback_content.append(image_block(img))
                feedback_content.append(text_block(f"\n</observation_image_{idx}>\n"))
            feedback_content.append(text_block("\n</observations>\n"))

        feedback_content.extend(attachments_content_blocks(self.attachments))

        if self.error is not None:
            feedback_content.append(text_block(f"\n<error>\n{self.error}\n</error>\n"))

        if feedback_content:
            messages.append({"role": "user", "content": feedback_content})

        return messages


__all__ = [
    "SessionStep",
    "SummaryStep",
    "TaskStep",
    "ActionStep",
]
