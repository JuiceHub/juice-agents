"""Runner 会话标题的规范化与历史 transcript 提取。"""

from __future__ import annotations

from typing import Any


FIRST_USER_REQUEST_PREVIEW_LENGTH = 80


def normalize_first_user_request_preview(value: Any) -> str:
    """把首条用户请求压成适合 CLI/Web 单行展示的稳定短标题。

    ``split``/``join`` 同时折叠换行、制表符和连续空格；统一在核心层
    截断，保证 manifest、历史会话回退和各 adapter 不会产生不同标题。
    """

    normalized = " ".join(str(value or "").split())
    return normalized[:FIRST_USER_REQUEST_PREVIEW_LENGTH]


def extract_first_user_request_preview(session_payload: Any) -> str:
    """从序列化 AgentSession 中提取第一条非空用户 TaskStep。"""

    if not isinstance(session_payload, dict):
        return ""
    steps = session_payload.get("steps")
    if not isinstance(steps, list):
        return ""
    for step in steps:
        if not isinstance(step, dict) or str(step.get("type") or "").strip() != "task":
            continue
        preview = normalize_first_user_request_preview(step.get("task"))
        if preview:
            return preview
    return ""


__all__ = [
    "FIRST_USER_REQUEST_PREVIEW_LENGTH",
    "extract_first_user_request_preview",
    "normalize_first_user_request_preview",
]
