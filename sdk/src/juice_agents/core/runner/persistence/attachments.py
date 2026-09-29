"""Runner 附件构造。"""

from __future__ import annotations

import time
from typing import Any

from juice_agents.core.agent.attachments import RuntimeAttachment, RuntimeEvent, merge_runtime_attachments


def build_async_task_notification_attachments(
    *,
    runtime_events: list[RuntimeEvent] | None = None,
    inbox_messages: list[dict[str, Any]] | None = None,
) -> list[RuntimeAttachment]:
    """把 runtime events 和可选 inbox 消息转成统一附件列表。"""

    normalized_events: list[RuntimeEvent] = []
    for event in list(runtime_events or []):
        normalized = dict(event)
        payload = dict(normalized.get("payload") or {})
        event_id = str(normalized.get("event_id") or "").strip()
        if event_id:
            payload["event_id"] = event_id
        normalized["payload"] = payload
        normalized_events.append(normalized)  # type: ignore[arg-type]
    return merge_runtime_attachments(
        runtime_events=normalized_events,
        inbox_messages=list(inbox_messages or []),
    )


def build_runtime_message(summary: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """给 team mode 生成一条标准 inbox 消息。"""

    return {
        "from": "runtime",
        "text": "",
        "summary": str(summary or "").strip(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "color": "yellow",
        **dict(payload or {}),
    }


__all__ = [
    "build_async_task_notification_attachments",
    "build_runtime_message",
]
