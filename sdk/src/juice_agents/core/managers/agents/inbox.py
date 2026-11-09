"""Durable message handoff and per-Agent serial round scheduling.

Only AgentManager uses this mixin. Producers keep their business outbox until
``acknowledge`` succeeds; inbox removal is therefore always the final write.
One running Runner owns these files, so an in-process lock is sufficient.
"""

from __future__ import annotations

from copy import deepcopy
import logging
import asyncio

from ...runner.execution.async_bridge import runtime
from ...runner.execution.async_io import blocking
import time
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class AgentInboxScheduler:
    """Scheduling implementation kept separate from Agent construction."""

    def _load_inbox(self, agent_id: str) -> list[dict[str, Any]]:
        from .manager import JsonAgentSnapshotStore

        if agent_id not in self._inboxes:
            entries = []
            if self._inbox_root is not None:
                path = self._inbox_root / JsonAgentSnapshotStore._directory_name(agent_id) / "inbox.json"
                if path.exists():
                    payload = JsonAgentSnapshotStore._read_json(path)
                    entries = payload.get("messages")
                    if not isinstance(entries, list) or any(
                        not isinstance(entry, dict) or not entry.get("message_id")
                        or not isinstance(entry.get("attachment"), dict) for entry in entries
                    ):
                        raise ValueError(f"agent inbox 损坏: {path}")
            self._inboxes[agent_id] = entries
        return self._inboxes[agent_id]

    def _save_inbox(self, agent_id: str, entries: list[dict[str, Any]]) -> None:
        from .manager import JsonAgentSnapshotStore

        if self._inbox_root is not None:
            path = self._inbox_root / JsonAgentSnapshotStore._directory_name(agent_id) / "inbox.json"
            JsonAgentSnapshotStore._atomic_write(path, {"schema_version": 1, "messages": entries})
        self._inboxes[agent_id] = entries

    def post(self, agent_id: str, attachment: dict[str, Any]) -> dict[str, Any]:
        """Accept a message only after its inbox is atomically persisted.

        An active round owns messages until its final step is committed. That
        decision and post's round reservation use the same Manager lock.
        Stable IDs make retries safe on both sides of the handoff.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("AgentManager 已关闭")
            item = self.get(agent_id)
            if not item.is_live:
                raise ValueError(f"消息目标已释放: {agent_id}")
            if item.lifecycle.value != "persistent":
                raise ValueError("functional Agent 不接受脱离调用的 inbox 消息")
            value = deepcopy(dict(attachment))
            if not value.get("attachment_type") or not isinstance(value.get("payload"), dict):
                raise ValueError("消息必须提供 attachment_type 和 payload object")
            if value["payload"].get("task_images"):
                from ...agent.context.content import image_block

                # Normalize with the same multimodal encoder used by steps;
                # objects such as ObservationImage/Pillow must never enter JSON.
                value["payload"]["task_images"] = [
                    image if isinstance(image, str) else image_block(image)["image_url"]["url"]
                    for image in value["payload"]["task_images"]
                ]
            message_id = str(value.get("message_id") or value["payload"].get("message_id") or uuid4().hex)
            value.update(message_id=message_id, created_at=float(value.get("created_at") or time.time()))
            entries = self._load_inbox(item.agent_id)
            existing = next((entry for entry in entries if entry["message_id"] == message_id), None)
            if existing is not None:
                receipt = {key: existing[key] for key in ("message_id", "round_id", "status")}
            elif message_id in item.committed_message_ids:
                receipt = {"message_id": message_id, "round_id": item.message_round_ids.get(message_id, ""), "status": "committed"}
            else:
                checkpoint = item.round_checkpoint
                pending = next((entry for entry in entries if entry.get("status") == "pending"
                                and entry["message_id"] not in item.committed_message_ids), None)
                round_id = (
                    str(checkpoint["round_id"]) if checkpoint.get("status") == "active" and not checkpoint.get("cancel_requested")
                    else str(pending["round_id"]) if pending is not None
                    else f"round_{uuid4().hex}"
                )
                receipt = {"message_id": message_id, "round_id": round_id, "status": "pending"}
                self._save_inbox(item.agent_id, [*entries, {**receipt, "attachment": value}])
                logger.info("agent_message_received runner_id=%s agent_id=%s round_id=%s message_id=%s",
                            self.runner_id, item.agent_id, round_id, message_id)
                # Record acceptance once. A later context commit acknowledges
                # consumption; it must not duplicate this complete attachment.
                self._log_input(item, "Message received",
                                "Accepted into durable inbox; awaiting context commit.", [value])
                # Acceptance is already durable, even if the Agent is still
                # inside a model/tool call. Let the UI show incoming messages
                # and async completion now; only a later context commit may
                # acknowledge the producer and remove this inbox entry.
                self._emit_round_event(item, {"kind": "message_received", "round_id": round_id,
                                             "data": {"attachments": [deepcopy(value)]}})
            if existing is not None or message_id in item.committed_message_ids:
                logger.info("agent_message_deduplicated runner_id=%s agent_id=%s message_id=%s",
                            self.runner_id, item.agent_id, message_id)
            if self._activated:
                self._ack_inbox(item)
                self._schedule(item)
            return {"agent_id": item.agent_id, **receipt}

    def _pending_attachments(self, item: Any) -> list[dict[str, Any]]:
        entries = [entry for entry in self._load_inbox(item.agent_id)
                   if entry.get("status") == "pending" and entry["message_id"] not in item.committed_message_ids]
        # Reassignment can be durable before the preceding terminal checkpoint.
        # Recovery must not absorb that next round's messages into the old one.
        active = item.round_checkpoint.get("status") == "active"
        round_id = item.round_checkpoint.get("round_id") if active else entries[0]["round_id"] if entries else None
        return [deepcopy(entry["attachment"]) for entry in entries if entry["round_id"] == round_id]

    def _cancelled_inputs(self, item: Any) -> list[dict[str, Any]]:
        return [entry for entry in self._load_inbox(item.agent_id)
                if entry.get("status") == "cancelled" and entry["message_id"] not in item.committed_message_ids]

    def _complete_cancelled_input(self, item: Any, entries: list[dict[str, Any]]) -> None:
        # The inbox retains intent; Manager owns the execution/terminal commit.
        self._complete_cancelled_round(item, entries)

    def _commit_messages(self, item: Any, attachments: list[dict[str, Any]]) -> None:
        """Called before saving the context; acknowledgement happens afterwards."""
        for attachment in attachments:
            message_id = str(attachment.get("message_id") or "")
            if message_id and message_id not in item.committed_message_ids:
                item.committed_message_ids.append(message_id)
                item.message_round_ids[message_id] = str(item.round_checkpoint.get("round_id") or "")
                item.round_checkpoint.setdefault("message_ids", []).append(message_id)

    def _receive_at_boundary(self, item: Any) -> None:
        """Receive messages immediately before the next model/tool step."""
        with self._lock:
            pending = self._pending_attachments(item)
            if not pending:
                return
            from ...agent.context import TaskStep

            leaves = item.context.get_active_leaves()
            if leaves and hasattr(leaves[0].step, "attachments"):
                leaves[0].step.attachments.extend(pending)
            else:
                item.context.append_step(TaskStep(task="", attachments=pending,
                                                 round_id=item.round_checkpoint["round_id"]))
            self._commit_messages(item, pending)
            self.persist(item)
            self._ack_inbox(item)

    def _defer_inbox(self, item: Any) -> None:
        """A terminal step cannot consume input that arrived during that step.

        Reserve a fresh round for those messages and inform request waiters
        before publishing the ending round. The reassignment is durable first.
        """
        entries = deepcopy(self._load_inbox(item.agent_id))
        old_id = item.round_checkpoint.get("round_id")
        deferred = [entry for entry in entries if entry.get("round_id") == old_id
                    and entry.get("status") == "pending" and entry["message_id"] not in item.committed_message_ids]
        if not deferred:
            return
        new_id = f"round_{uuid4().hex}"
        for entry in deferred:
            entry["round_id"] = new_id
        self._save_inbox(item.agent_id, entries)
        for entry in deferred:
            self._emit_round_event(item, {"kind": "message_deferred", "message_id": entry["message_id"],
                                         "previous_round_id": old_id, "round_id": new_id})

    def _ack_inbox(self, item: Any) -> None:
        """Retry producer acknowledgements without injecting committed content."""
        entries = self._load_inbox(item.agent_id)
        removed = set()
        for entry in entries:
            if entry["message_id"] not in item._persisted_message_ids:
                continue
            try:
                if self.callbacks.acknowledge is not None:
                    self.callbacks.acknowledge(item, [deepcopy(entry["attachment"])])
            except Exception:
                logger.exception("agent_message_ack_failed runner_id=%s agent_id=%s message_id=%s",
                                 self.runner_id, item.agent_id, entry["message_id"])
                continue
            removed.add(entry["message_id"])
        if removed:
            # A business acknowledgement can synchronously enqueue more work
            # (for example a newly eligible Team task). Filter the current
            # inbox, not the list captured before that reentrant callback.
            try:
                self._save_inbox(item.agent_id, [entry for entry in self._load_inbox(item.agent_id)
                                                 if entry["message_id"] not in removed])
            except Exception:
                # The session already authorizes the business acknowledgement.
                # Failed inbox cleanup is retried idempotently; it cannot hide
                # a saved step/result or turn it into a new failed round.
                logger.exception("agent_inbox_cleanup_failed runner_id=%s agent_id=%s",
                                 self.runner_id, item.agent_id)
                return
            logger.info("agent_messages_acknowledged runner_id=%s agent_id=%s message_ids=%s",
                        self.runner_id, item.agent_id, sorted(removed))

    def activate(self) -> None:
        """Enable work after listeners, Ask and all restored identities are bound."""
        with self._lock:
            if self._closed:
                raise RuntimeError("AgentManager 已关闭")
            self._activated = True
            for item in list(self._managed_by_id.values()):
                if item.lifecycle.value == "persistent":
                    item._execution_error = None
                    if set(item.committed_message_ids) != item._persisted_message_ids:
                        # A previous storage error may have left a dirty tree.
                        # Explicit activation retries that handoff before ack.
                        self.persist(item)
                    self._ack_inbox(item)
                    self._schedule(item)

    def _schedule(self, item: Any) -> None:
        if not self._activated or self._closed or not item.is_live:
            return
        if item.agent_id in self._workers:
            return
        if item.round_checkpoint.get("status") in {"completed", "failed", "cancelled"}:
            # Recovery may stop between the final session write and inbox
            # reassignment. Never reuse that completed round's identity.
            self._defer_inbox(item)
        if (item.round_checkpoint.get("status") != "active"
                and not self._pending_attachments(item) and not self._cancelled_inputs(item)):
            return
        host = runtime()
        # 调度器只拥有协程任务；模型等待不再为每个 Agent 占用线程。
        self._workers[item.agent_id] = asyncio.run_coroutine_threadsafe(self._drain_agent(item), host.loop)

    async def _drain_agent(self, item: Any) -> None:
        """Choose work only; Manager.stream owns every execution transition."""
        restart = True
        try:
            while True:
                def select_work():
                    with self._lock:
                        if self._closed or not item.is_live:
                            return None
                        resume = item.round_checkpoint.get("status") == "active"
                        cancelled = self._cancelled_inputs(item)
                        if not resume and cancelled:
                            self._complete_cancelled_input(item, cancelled)
                            return (None, None)
                        pending = self._pending_attachments(item)
                        if not resume and not pending:
                            return None
                        round_id = item.round_checkpoint["round_id"] if resume else next(
                            entry["round_id"] for entry in self._load_inbox(item.agent_id)
                            if entry["message_id"] == pending[0]["message_id"]
                        )
                        return resume, round_id
                work = await blocking(select_work)
                if work is None:
                    return
                resume, round_id = work
                if resume is None:
                    continue
                # Manager.run distinguishes an actual storage failure from an
                # execution exception whose failed result was saved already.
                # The latter must not strand later queued inputs.
                await self.arun(item, "", round_id=round_id, resume=resume, scheduled=True)
        except Exception as exc:
            # A failed durable write must require an explicit retry, never a
            # hot restart or a fabricated terminal event.
            restart = False
            item._execution_error = exc
            logger.exception("agent_scheduler_failed runner_id=%s agent_id=%s", self.runner_id, item.agent_id)
        finally:
            def settle() -> None:
                with self._lock:
                    self._workers.pop(item.agent_id, None)
                    if restart:
                        self._schedule(item)
            await blocking(settle)

    def cancel_round(self, round_id: str, *, reason: str = "user_cancelled") -> bool:
        """Persist cancellation before exposing the cooperative cancel signal."""
        with self._lock:
            for item in self._managed_by_id.values():
                checkpoint = item.round_checkpoint
                if checkpoint.get("round_id") != round_id or checkpoint.get("status") != "active":
                    continue
                previous = deepcopy(checkpoint)
                checkpoint.update(cancel_requested=True, cancellation_reason=reason)
                try:
                    self.persist(item)
                except Exception:
                    item.round_checkpoint = previous
                    raise
                self._interrupt_reasons[item.agent_id] = reason
                item.cancel_event.set()
                logger.info("agent_round_cancelled runner_id=%s agent_id=%s round_id=%s",
                            self.runner_id, item.agent_id, round_id)
                return True
            # A caller can cancel the receipt before the scheduler has begun.
            # Persist intent independently from an earlier unwinding round.
            # Its serial handoff never starts a model call, including recovery.
            for item in self._managed_by_id.values():
                pending = [entry for entry in self._load_inbox(item.agent_id)
                           if entry.get("round_id") == round_id and entry.get("status") == "pending"
                           and entry["message_id"] not in item.committed_message_ids]
                if not pending or (item.round_checkpoint.get("status") == "active"
                                   and not item.round_checkpoint.get("cancel_requested")):
                    continue
                entries = deepcopy(self._load_inbox(item.agent_id))
                for entry in entries:
                    if entry["round_id"] == round_id and entry.get("status") == "pending":
                        entry.update(status="cancelled", cancellation_reason=reason)
                self._save_inbox(item.agent_id, entries)
                if item.agent_id not in self._workers:
                    self._complete_cancelled_input(item, self._cancelled_inputs(item))
                logger.info("agent_pending_round_cancelled runner_id=%s agent_id=%s round_id=%s",
                            self.runner_id, item.agent_id, round_id)
                return True
            return False

    def close(self, *, wait: bool = True, timeout_seconds: float | None = None) -> None:
        """Stop scheduling and drain started work before observers/pools close.

        A caller may explicitly bound shutdown; the default preserves every
        started execution's final boundaries, including slow cooperative tools.
        """
        with self._lock:
            self._closed = True
            self._activated = False
            workers = list(self._workers.values())
            items = list(self._managed_by_id.values())
            for item in items:
                if item.round_checkpoint.get("status") == "active":
                    self.cancel_round(item.round_checkpoint["round_id"], reason="manager_closed")
        if wait:
            deadline = None if timeout_seconds is None else time.monotonic() + max(0, timeout_seconds)
            for worker in workers:
                try:
                    worker.result(None if deadline is None else max(0, deadline - time.monotonic()))
                except TimeoutError:
                    logger.warning("agent_close_timeout runner_id=%s", self.runner_id)
            # Direct functional calls are not inbox workers. Their run locks
            # cover model/tool unwinding and must settle before releasing pools.
            for item in items:
                acquired = (item._run_lock.acquire() if deadline is None else
                            item._run_lock.acquire(timeout=max(0, deadline - time.monotonic())))
                if acquired:
                    try:
                        if item.is_live and item.status.value != "running":
                            self.release(item)
                    finally:
                        item._run_lock.release()
            if self._owns_model_manager:
                self.model_manager.close()

    async def aclose(self, **kwargs: Any) -> None:
        await blocking(self.close, **kwargs)

    async def apost(self, agent_id: str, attachment: dict[str, Any]) -> dict[str, Any]:
        return await blocking(self.post, agent_id, attachment)

    def has_queued_input(self, item: Any) -> bool:
        """团队编排查询公开投影，不访问调度器的私有队列格式。"""
        with self._lock:
            return bool(self._pending_attachments(item) or self._cancelled_inputs(item))
