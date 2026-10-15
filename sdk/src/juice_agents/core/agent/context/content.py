"""消息块与 observation 文本处理辅助函数。"""

from __future__ import annotations

import logging
from typing import Any

from ..agent_type import ObservationImage
from ..attachments import RuntimeAttachment, serialize_runtime_attachments_text

logger = logging.getLogger(__name__)

ImageType = Any  # 避免强依赖 Pillow，按需传入对象

DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER = (
    "<observation_omitted>Earlier observations/images are omitted due to compression.</observation_omitted>"
)

DEFAULT_COMPACT_CONTINUATION_TEMPLATE = """This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

<summary>
{summary}
</summary>

Please continue the conversation from where it left off without asking the user any further questions. Continue with the last task that you were asked to work on."""

# 旧 session 没有逐 observation 限额。读取这类记录时使用正文工具的平衡档，
# 既避免无界回放，也不恢复已经删除的 session 级总预算。
DEFAULT_OBSERVATION_PROJECTION_CHARS = 12_000
CODEACT_OBSERVATION_PROJECTION_CHARS = 20_000


def normalize_observations(observations: Any) -> list[str]:
    """把 observation/update 输入统一归一化为字符串列表。"""
    if observations is None:
        return []
    if isinstance(observations, (list, tuple)):
        parts: list[str] = []
        for part in observations:
            if part is None:
                continue
            text = str(part)
            if not text:
                continue
            parts.append(text)
        return parts
    if isinstance(observations, str):
        return [observations] if observations else []
    return [str(observations)]


def observations_to_text(observations: Any) -> str:
    """把多段 observation 拼成可读文本。"""
    parts = normalize_observations(observations)
    return "\n".join(parts).strip()


def truncate_text(text: str, max_length: int | None) -> str:
    """在 observation 过长时保留前后片段，避免把上下文窗口打满。"""
    if max_length is None:
        return text
    if max_length <= 0:
        return ""
    if len(text) <= max_length:
        return text

    omitted = len(text) - max_length
    hint = (
        f"\n...[truncated original_length={len(text)} "
        f"omitted_length={omitted}]...\n"
    )
    if max_length <= len(hint):
        # 单次调用允许把限额调得很小；即使诊断标记放不下，也必须严格遵守
        # 调用者声明的硬上限，不能为了元数据反向突破预算。
        truncated = hint[:max_length]
    else:
        keep = max_length - len(hint)
        head = keep // 2
        tail = keep - head
        truncated = text[:head] + hint + text[-tail:]
    logger.debug("Observation文本长度超限，已截断: 原=%d, max=%d", len(text), max_length)
    return truncated


def encode_image(image: ImageType) -> str:
    """将图片对象转为可传入 image_url 的字符串（网络 URL 或本地路径）。"""
    if image is None:
        return ""
    if isinstance(image, ObservationImage):
        return image.ensure_image_url()
    wrapped = ObservationImage.from_image(image=image, description="")
    return wrapped.ensure_image_url()


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


# 框架内部字段：标记此 content block 之后是一个 prompt 缓存边界。
# 模型层（如 AnthropicModel）据此翻译成各 provider 的缓存协议；
# 出站前必须由模型层剥离，避免污染 OpenAI/Ark 等不识别该字段的接口。
CACHE_BOUNDARY_KEY = "_cache_boundary"


def system_content_blocks(static_text: str, dynamic_text: str | None) -> list[dict[str, Any]]:
    """构造 system 消息的 content blocks（静态前缀 + 动态后缀）。

    - 静态段始终作为第一个 block，并打上 CACHE_BOUNDARY_KEY 标记，作为稳定缓存前缀；
    - 动态段非空时追加为第二个 block（不标记，因为它可能随运行时变化）；
    - 动态段为空时只返回单个静态 block，行为与旧的单段 system 等价。
    """
    static_block = text_block(static_text)
    static_block[CACHE_BOUNDARY_KEY] = True
    blocks = [static_block]
    if dynamic_text and dynamic_text.strip():
        blocks.append(text_block(dynamic_text))
    return blocks


def image_block(image: ImageType) -> dict[str, Any]:
    image_url = None
    if isinstance(image, ObservationImage):
        try:
            image_url = image.ensure_image_url()
        except Exception as exc:  # pragma: no cover - 运行时保护
            logger.warning("ObservationImage 无法生成 image_url: %s", exc)
            image_url = getattr(image, "image_url", None)

    if not image_url:
        try:
            image_url = encode_image(image)
        except Exception as exc:  # pragma: no cover - 运行时保护
            logger.warning("图片编码为 image_url 失败: %s", exc)
            image_url = ""
    if not image_url:
        logger.warning("图片内容为空，image_url 为空，消息可能被拒绝")

    # 火山方舟/Ark 多模态消息协议：不应在 image block 上额外携带自定义字段
    return {"type": "image_url", "image_url": {"url": image_url}}


def attachments_content_blocks(attachments: list[RuntimeAttachment] | None) -> list[dict[str, Any]]:
    """将 runtime attachments 序列化为可拼接到同一条 user content 的文本块。"""
    serialized = serialize_runtime_attachments_text(attachments)
    if not serialized:
        return []
    return [text_block(serialized)]


def message_content_to_transcript_parts(content: Any) -> list[str]:
    """
    把 message content 提取为摘要模型可消费的纯文本片段。

    image block 不直接塞 URL，而是降级为稳定占位符，避免把二进制/缓存路径污染摘要。
    """
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip()
            if block_type == "text":
                text = str(block.get("text") or "")
                if text:
                    parts.append(text)
                continue
            if block_type == "image_url":
                parts.append("[image]")
        return parts
    if isinstance(content, str):
        return [content] if content else []
    if content is None:
        return []
    return [str(content)]


__all__ = [
    "ImageType",
    "CACHE_BOUNDARY_KEY",
    "DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER",
    "DEFAULT_COMPACT_CONTINUATION_TEMPLATE",
    "DEFAULT_OBSERVATION_PROJECTION_CHARS",
    "CODEACT_OBSERVATION_PROJECTION_CHARS",
    "normalize_observations",
    "observations_to_text",
    "truncate_text",
    "encode_image",
    "text_block",
    "system_content_blocks",
    "image_block",
    "attachments_content_blocks",
    "message_content_to_transcript_parts",
]
