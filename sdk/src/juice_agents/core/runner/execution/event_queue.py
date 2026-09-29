"""按 managed ``agent_id`` 路由的 Runner 事件队列。"""

from __future__ import annotations

import copy
import threading
import time
from collections import defaultdict
from typing import DefaultDict
from uuid import uuid4

from juice_agents.core.agent.attachments import RuntimeEvent


class RunnerEventQueue:
    """Thread-safe per-managed-Agent event queue."""

    def __init__(self) -> None:
        self._queues: DefaultDict[str, list[RuntimeEvent]] = defaultdict(list)
        self._queued_event_ids: set[str] = set()
        self._condition = threading.Condition(threading.RLock())

    def enqueue(self, recipient_agent_id: str, event: RuntimeEvent) -> RuntimeEvent:
        normalized_recipient = str(recipient_agent_id or "").strip()
        if not normalized_recipient:
            raise ValueError("recipient_agent_id 不能为空")
        normalized_event = copy.deepcopy(dict(event or {}))
        if not normalized_event.get("event_id"):
            normalized_event["event_id"] = f"evt_{uuid4().hex}"
        if "created_at" not in normalized_event or normalized_event.get("created_at") is None:
            normalized_event["created_at"] = time.time()
        if not normalized_event.get("event_type"):
            raise ValueError("runner event 缺少 event_type")
        with self._condition:
            event_id = str(normalized_event.get("event_id") or "").strip()
            if event_id in self._queued_event_ids:
                return copy.deepcopy(normalized_event)
            self._queues[normalized_recipient].append(normalized_event)
            self._queued_event_ids.add(event_id)
            self._condition.notify_all()
        return normalized_event

    def drain(self, recipient_agent_id: str) -> list[RuntimeEvent]:
        normalized_recipient = str(recipient_agent_id or "").strip()
        if not normalized_recipient:
            return []
        with self._condition:
            events = list(self._queues.get(normalized_recipient) or [])
            self._queues[normalized_recipient] = []
            self._queued_event_ids.difference_update(
                str(event.get("event_id") or "") for event in events
            )
        return events

    def has_events(self, recipient_agent_id: str) -> bool:
        normalized_recipient = str(recipient_agent_id or "").strip()
        if not normalized_recipient:
            return False
        with self._condition:
            return bool(self._queues.get(normalized_recipient))

    def wait_drain(self, recipient_agent_id: str, timeout: float | None = None) -> list[RuntimeEvent]:
        normalized_recipient = str(recipient_agent_id or "").strip()
        if not normalized_recipient:
            return []
        with self._condition:
            if not self._queues.get(normalized_recipient):
                self._condition.wait_for(
                    lambda: bool(self._queues.get(normalized_recipient)),
                    timeout=timeout,
                )
            events = list(self._queues.get(normalized_recipient) or [])
            self._queues[normalized_recipient] = []
            self._queued_event_ids.difference_update(
                str(event.get("event_id") or "") for event in events
            )
            return events

    def wait_for_events(self, recipient_agent_id: str, timeout: float | None = None) -> bool:
        """等待事件到达（不drain），返回是否有事件。"""
        normalized_recipient = str(recipient_agent_id or "").strip()
        if not normalized_recipient:
            return False
        with self._condition:
            if not self._queues.get(normalized_recipient):
                self._condition.wait_for(
                    lambda: bool(self._queues.get(normalized_recipient)),
                    timeout=timeout,
                )
            return bool(self._queues.get(normalized_recipient))


__all__ = ["RunnerEventQueue"]
