"""Agent 附件协议与 `<attachments>` 文本序列化。

这里承接原先 `agents.runtime.attachments/types` 中属于 agent 会话协议的部分：

- RuntimeAttachment / RuntimeEvent 这类“注入给模型”的结构
- runtime events / team inbox messages -> attachments 的合并逻辑
- `<attachments>...</attachments>` 的稳定文本序列化

注意：
- 这是 agent/session 协议层，不负责后台异步任务生命周期和存储。
- runner 只负责把自己的 async task 通知和 team mailbox 事件传进来。
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from typing import Any, Literal, TypedDict

RuntimeEventType = Literal["async_task_notification"]
RuntimeAttachmentType = Literal[
    "async_task_notification",
    "agent_user_message",
    "inbox_message",
    "plan_mode",
    "plan_mode_exit",
    "goal_state",
    "goal_evaluation",
    "explicit_capability",
]


class AsyncTaskNotification(TypedDict, total=False):
    """回合边界回注给 owner agent 的轻量异步任务通知。"""

    async_task_id: str
    status: str
    summary: str
    result: Any
    output_dir: str
    max_observation_chars: int


class RuntimeEvent(TypedDict, total=False):
    """runner 内部投递到 agent 的事件。"""

    event_id: str
    event_type: RuntimeEventType
    created_at: float
    payload: AsyncTaskNotification


class RuntimeAttachment(TypedDict, total=False):
    """最终注入到模型上下文的附件块。"""

    attachment_type: RuntimeAttachmentType
    created_at: float
    payload: dict[str, Any]


_ATTACHMENT_PRIORITY = {
    # Explicit user-selected instructions must be present before the task is
    # interpreted, including when a later attachment has the same timestamp.
    "explicit_capability": -1,
    "async_task_notification": 0,
    "agent_user_message": 1,
    "inbox_message": 2,
    "plan_mode": 2,
    "plan_mode_exit": 2,
    "goal_state": 3,
    "goal_evaluation": 4,
}


def _parse_timestamp(value: Any) -> float:
    """把 inbox timestamp 规范成秒级时间戳，排序失败时回退到 0。"""

    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


def _notification_attachment_type(event: RuntimeEvent) -> str:
    del event
    return "async_task_notification"


def build_async_task_notification_attachment(event: RuntimeEvent) -> RuntimeAttachment:
    """把 runner 异步任务事件转成统一附件结构。"""

    payload = dict(event.get("payload") or {})
    async_task_id = str(payload.get("async_task_id") or "").strip()
    if async_task_id:
        payload["async_task_id"] = async_task_id
    return {
        "attachment_type": _notification_attachment_type(event),
        "created_at": float(event.get("created_at") or 0.0),
        "payload": payload,
    }


def build_inbox_message_attachment(message: dict[str, Any]) -> RuntimeAttachment:
    """把 team inbox message 转成统一附件结构。"""

    payload = dict(message or {})
    return {
        "attachment_type": "inbox_message",
        "created_at": _parse_timestamp(payload.get("timestamp")),
        "payload": payload,
    }


def merge_runtime_attachments(
    runtime_events: list[RuntimeEvent] | None = None,
    inbox_messages: list[dict[str, Any]] | None = None,
) -> list[RuntimeAttachment]:
    """合并 runner 异步任务回执和 team inbox，并按稳定顺序排序。"""

    attachments: list[RuntimeAttachment] = []
    for event in list(runtime_events or []):
        event_type = str(event.get("event_type") or "").strip()
        if isinstance(event, dict) and event_type == "async_task_notification":
            attachments.append(build_async_task_notification_attachment(event))
    for message in list(inbox_messages or []):
        if isinstance(message, dict):
            attachments.append(build_inbox_message_attachment(message))
    return sorted(
        attachments,
        key=lambda item: (
            float(item.get("created_at") or 0.0),
            _ATTACHMENT_PRIORITY.get(str(item.get("attachment_type") or ""), 99),
        ),
    )


def _serialize_payload_body(payload: Any, *, preferred_text_key: str | None = None) -> str:
    """生成 attachment tag 的文本 body。"""

    if isinstance(payload, dict) and preferred_text_key and payload.get(preferred_text_key) not in (None, ""):
        return escape(str(payload.get(preferred_text_key)))
    if isinstance(payload, (dict, list)):
        return escape(json.dumps(payload, ensure_ascii=False))
    if payload in (None, ""):
        return ""
    return escape(str(payload))


def _serialize_async_task_notification_body(payload: dict[str, Any]) -> str:
    """为异步任务通知生成稳定的文本正文。"""

    summary = str(payload.get("summary") or "").strip()
    result = payload.get("result")
    if result in (None, ""):
        text = summary
    elif isinstance(result, (dict, list)):
        result_text = json.dumps(result, ensure_ascii=False)
        text = result_text if not summary else f"{summary}\n{result_text}"
    else:
        result_text = str(result)
        text = result_text if not summary else f"{summary}\n{result_text}"

    raw_limit = payload.get("max_observation_chars", 12_000)
    limit = (
        raw_limit
        if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) and raw_limit > 0
        else 12_000
    )
    # attachment 最终以 HTML-escaped 文本进入模型上下文；引号等字符在转义后
    # 会膨胀，因此必须对最终投影限额，而不是先截 raw 再让 escape 突破预算。
    projected = escape(text)
    if len(projected) > limit:
        hint = (
            f"\n...[truncated original_length={len(projected)} "
            f"omitted_length={len(projected) - limit}]...\n"
        )
        if limit <= len(hint):
            projected = hint[:limit]
        else:
            keep = limit - len(hint)
            head = keep // 2
            projected = projected[:head] + hint + projected[-(keep - head):]
    return projected


def serialize_runtime_attachments_text(attachments: list[RuntimeAttachment] | None) -> str:
    """把附件列表序列化成注入 prompt 的 `<attachments>` 文本。"""

    normalized = merge_runtime_attachments(
        runtime_events=[
            {
                "event_type": str(attachment.get("attachment_type") or ""),
                "created_at": attachment.get("created_at"),
                "payload": attachment.get("payload") or {},
            }
            for attachment in list(attachments or [])
            if isinstance(attachment, dict)
            and str(attachment.get("attachment_type") or "") == "async_task_notification"
        ],
        inbox_messages=[
            dict(attachment.get("payload") or {})
            for attachment in list(attachments or [])
            if isinstance(attachment, dict)
            and str(attachment.get("attachment_type") or "") == "inbox_message"
        ],
    )
    normalized.extend(
        {
            "attachment_type": str(attachment.get("attachment_type") or ""),
            "created_at": float(attachment.get("created_at") or 0.0),
            "payload": dict(attachment.get("payload") or {}),
        }
        for attachment in list(attachments or [])
        if isinstance(attachment, dict)
        and str(attachment.get("attachment_type") or "") in {
            "agent_user_message",
            "plan_mode",
            "plan_mode_exit",
            "goal_state",
            "goal_evaluation",
            "explicit_capability",
        }
    )
    normalized = sorted(
        normalized,
        key=lambda item: (
            float(item.get("created_at") or 0.0),
            _ATTACHMENT_PRIORITY.get(str(item.get("attachment_type") or ""), 99),
        ),
    )
    if not normalized:
        return ""

    lines = ["<attachments>"]
    for attachment in normalized:
        attachment_type = str(attachment.get("attachment_type") or "").strip()
        payload = dict(attachment.get("payload") or {})
        attrs: list[str] = []
        preferred_text_key = None
        if attachment_type == "async_task_notification":
            for key in ("async_task_id", "status", "output_dir"):
                value = payload.get(key)
                if value in (None, ""):
                    continue
                attrs.append(f'{key}="{escape(str(value), quote=True)}"')
        elif attachment_type == "inbox_message":
            preferred_text_key = "text"
            for payload_key, attr_name in (
                ("from", "from"),
                ("message_id", "message_id"),
                ("summary", "summary"),
                ("timestamp", "timestamp"),
                ("color", "color"),
            ):
                value = payload.get(payload_key)
                if value in (None, ""):
                    continue
                attrs.append(f'{attr_name}="{escape(str(value), quote=True)}"')
        elif attachment_type == "agent_user_message":
            preferred_text_key = "text"
            for payload_key, attr_name in (
                ("from", "from"),
                ("agent_name", "agent"),
                ("message_id", "message_id"),
            ):
                value = payload.get(payload_key)
                if value in (None, ""):
                    continue
                attrs.append(f'{attr_name}="{escape(str(value), quote=True)}"')
        elif attachment_type == "plan_mode":
            preferred_text_key = "message"
            for payload_key, attr_name in (
                ("reminder_type", "reminder_type"),
                ("plan_file", "plan_file"),
            ):
                value = payload.get(payload_key)
                if value in (None, ""):
                    continue
                attrs.append(f'{attr_name}="{escape(str(value), quote=True)}"')
        elif attachment_type == "plan_mode_exit":
            for payload_key, attr_name in (("plan_file", "plan_file"),):
                value = payload.get(payload_key)
                if value in (None, ""):
                    continue
                attrs.append(f'{attr_name}="{escape(str(value), quote=True)}"')
        elif attachment_type == "goal_state":
            preferred_text_key = "objective"
            for payload_key, attr_name in (
                ("status", "status"),
                ("turns_used", "turns_used"),
                ("max_turns", "max_turns"),
            ):
                value = payload.get(payload_key)
                if value in (None, ""):
                    continue
                attrs.append(f'{attr_name}="{escape(str(value), quote=True)}"')
        elif attachment_type == "goal_evaluation":
            preferred_text_key = "reason"
            for payload_key, attr_name in (
                ("completed", "completed"),
                ("should_continue", "should_continue"),
                ("progress_fingerprint", "progress_fingerprint"),
            ):
                value = payload.get(payload_key)
                if value in (None, ""):
                    continue
                attrs.append(f'{attr_name}="{escape(str(value), quote=True)}"')
        elif attachment_type == "explicit_capability":
            preferred_text_key = "instructions"
            for payload_key, attr_name in (("kind", "kind"), ("name", "name"), ("plugin", "plugin")):
                value = payload.get(payload_key)
                if value in (None, ""):
                    continue
                attrs.append(f'{attr_name}="{escape(str(value), quote=True)}"')
        else:
            continue

        tag_open = f"<{attachment_type}"
        if attrs:
            tag_open += " " + " ".join(attrs)
        tag_open += ">"
        if attachment_type == "async_task_notification":
            body = _serialize_async_task_notification_body(payload)
        elif attachment_type == "plan_mode_exit":
            approved_plan = str(payload.get("approved_plan") or "").strip()
            body = escape(
                "User approved the plan. You are no longer in Plan Mode.\n\n"
                "## Approved Plan\n"
                f"{approved_plan}"
            )
        else:
            body = _serialize_payload_body(payload, preferred_text_key=preferred_text_key)
        lines.append(f"{tag_open}{body}</{attachment_type}>")
    lines.append("</attachments>")
    return "\n".join(lines)


__all__ = [
    "AsyncTaskNotification",
    "RuntimeAttachment",
    "RuntimeAttachmentType",
    "RuntimeEvent",
    "RuntimeEventType",
    "build_async_task_notification_attachment",
    "build_inbox_message_attachment",
    "merge_runtime_attachments",
    "serialize_runtime_attachments_text",
]
