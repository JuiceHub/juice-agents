"""Team 专属编排：分派、投递、对账和成员收尾。

这里只通过显式的 AgentManager/声明加载器接口使用外部服务。业务状态仍由
TeamManager 原子提交；Agent 实例、inbox 和 round 始终归 AgentManager。
"""

from __future__ import annotations

from contextlib import contextmanager
import logging
import threading
import time
from typing import Any, Callable, TYPE_CHECKING

from ...agent.attachments import build_inbox_message_attachment
from ...runner.execution.async_io import blocking

if TYPE_CHECKING:
    from ..agents import AgentManager, ManagedAgent
    from ...registry.agents.types import AgentConfig

logger = logging.getLogger(__name__)


class TeamRuntime:
    """同步部分只处理完整文件事务；异步宿主通过 a* 接口等待。"""

    def bind_runtime(self, *, agents: AgentManager, root_resolver: Callable[[], ManagedAgent],
                     member_config_loader: Callable[[str], AgentConfig], auto_dispatch: bool = True) -> None:
        self._agents = agents
        self._root_resolver = root_resolver
        self._member_config_loader = member_config_loader
        self.auto_dispatch = auto_dispatch

    def _init_runtime(self) -> None:
        self._agents = None
        self._dispatch_lock = threading.RLock()
        self._dispatching = False
        self._wake_pending = False
        self.stopping = False
        self._runtime_closed = False
        self.auto_dispatch = False

    @contextmanager
    def suspend_dispatch(self):
        """恢复/成员生命周期变更期间暂停分派，退出后补做一次扫描。"""
        with self._dispatch_lock:
            previous = self.stopping
            self.stopping = True
        try:
            yield
        finally:
            self.stopping = previous
            self.wake()

    def has_active_calls(self) -> bool:
        return any(member.get("busy") for member in self.snapshot()["members"]
                   if member["name"] != self.root_name)

    def wake(self) -> None:
        """Replay business notifications through the one Agent inbox scheduler.

        Team owns assignment and delivery records. It never owns worker futures;
        a message already in an Agent inbox is harmless to replay by stable ID.
        """

        manager = self
        if self._agents is None or not self.auto_dispatch:
            return
        # Business acknowledgements may call wake_team while AgentManager
        # holds its durability lock. Never wait here behind a dispatcher that
        # could itself be waiting to post into that same Manager.
        if not self._dispatch_lock.acquire(blocking=False):
            self._wake_pending = True
            return
        try:
            if self._dispatching:
                self._wake_pending = True
                return
            if self.stopping or self._runtime_closed:
                return
            self._dispatching = True
            try:
                while True:
                    self._wake_pending = False
                    snapshot = manager.snapshot()
                    if snapshot["finished"]:
                        return
                    for member in snapshot["members"]:
                        name = str(member.get("name") or "")
                        if member.get("status") == "closed":
                            continue
                        messages = manager.pending_messages(name)
                        task = None
                        if name != self.root_name and not member.get("busy"):
                            # A crash may occur after the board claim but before
                            # inbox acceptance. Reuse that business record and ID.
                            task = next((
                                item for item in manager.list_tasks()
                                if item.get("status") == "in_progress" and item.get("claimed_by") == name and not item.get("error")
                            ), None)
                            if task is None:
                                task = manager.claim_next(name)
                        if task is None and not messages:
                            continue
                        try:
                            managed = self._root_resolver() if name == self.root_name else self.acquire_member(name)
                        except Exception as exc:
                            for message in messages:
                                manager.mark_message_failed(str(message["message_id"]), str(exc))
                            logger.exception("team_delivery_target_failed runner_id=%s member=%s", self.team_run_id, name)
                            if task is not None:
                                manager.fail_task(str(task["task_id"]), name, error="dispatch_failed")
                            continue
                        if task is not None:
                            task_id = str(task["task_id"])
                            assignment_id = f"team-task:{self.team_run_id}:{task_id}:{member.get('generation', 0)}"
                            checkpoint = managed.round_checkpoint
                            if (
                                managed.message_round_ids.get(assignment_id) == checkpoint.get("round_id")
                                and checkpoint.get("status") in {"completed", "failed", "cancelled"}
                            ):
                                # The final session commit can precede the Team
                                # observer's update. Reconcile the business claim
                                # from that committed result without another call.
                                if checkpoint["status"] == "completed":
                                    from juice_agents.core.agent.context import ActionStep
                                    final = next((step for step in reversed(managed.context.steps)
                                                  if isinstance(step, ActionStep)), None)
                                    manager.complete_task(task_id, name, result=str(getattr(final, "output", "") or ""))
                                else:
                                    manager.fail_task(task_id, name, error=str(checkpoint.get("reason") or checkpoint["status"]))
                                manager.set_member_busy(name, False)
                                task = None
                        if task is not None:
                            task_id = str(task["task_id"])
                            managed.metadata["team_task_id"] = task_id
                            manager.set_member_busy(name, True)
                            try:
                                self._agents.post(managed.agent_id, {
                                    "attachment_type": "task_assignment",
                                    "message_id": assignment_id,
                                    "created_at": time.time(),
                                    "payload": {
                                        "task_id": task_id,
                                        "text": f"{task_id}: {task.get('title', '')}\n{task.get('description', '')}",
                                        "team_run_id": self.team_run_id,
                                        "generation": int(member.get("generation") or 0),
                                    },
                                })
                            except Exception:
                                manager.set_member_busy(name, False)
                                manager.fail_task(task_id, name, error="dispatch_failed")
                                logger.exception("team_assignment_delivery_failed runner_id=%s task_id=%s", self.team_run_id, task_id)
                        for message in messages:
                            message_id = str(message["message_id"])
                            if not manager.message_target_valid(message):
                                manager.mark_message_failed(message_id, "target_identity_changed")
                                continue
                            try:
                                self._agents.post(managed.agent_id, build_inbox_message_attachment(message))
                            except Exception as exc:
                                manager.mark_message_failed(message_id, str(exc))
                                logger.exception("team_message_delivery_failed runner_id=%s message_id=%s", self.team_run_id, message_id)
                    if not self._wake_pending:
                        break
            finally:
                self._dispatching = False
        finally:
            self._dispatch_lock.release()
        # Cover an event arriving between the last scan and releasing the lock.
        if self._wake_pending and not self._runtime_closed and not self.stopping:
            self.wake()

    def acquire_member(self, member_name: str) -> ManagedAgent:
        """Resolve a generation-bound Team identity without starting a round."""

        manager = self
        if self._agents is None:
            raise RuntimeError("Team runtime is not bound")
        member = next(item for item in manager.snapshot()["members"] if item["name"] == member_name)
        if member.get("status") == "closed":
            raise ValueError(f"Team member {member_name!r} is closed")
        from juice_agents.core.registry.tools.types import ToolRef
        from juice_agents.core.team.builtin.configs import DEFAULT_TEAMMATE_INSTRUCTIONS

        declaration = self._member_config_loader(member_name)
        # A shared or private Agent need not duplicate Team workflow tools in
        # its YAML. Build a detached runtime config and remove root-only Team
        # references that older shared declarations may still contain.
        root_only = {
            "team_task_create", "team_task_update", "team_create", "team_use",
            "team_member_create", "team_member_restart", "team_member_close", "team_finish",
        }
        required = (
            "team_task_list", "team_task_claim", "team_task_complete",
            "send_message", "team_member_stop_request",
        )
        refs = {ref.name: ref for ref in declaration.tools if ref.name not in root_only}
        for name in required:
            refs.setdefault(name, ToolRef(name=name))
        role_instructions = (declaration.instructions or "").strip()
        workflow = DEFAULT_TEAMMATE_INSTRUCTIONS
        if workflow not in role_instructions:
            role_instructions = f"{workflow}\n\n{role_instructions}".strip()
        custom_prompt = declaration.system_prompt
        if custom_prompt:
            # A custom system_prompt can replace the normal Agent template
            # entirely, so append the fixed Team workflow explicitly there.
            custom_prompt = f"{custom_prompt.rstrip()}\n\n{role_instructions}"
        declaration = declaration.copy_with(
            tools=[ref.to_dict() for ref in refs.values()],
            instructions=role_instructions,
            system_prompt=custom_prompt,
        )
        generation = int(member.get("generation") or 0)
        # AgentManager accepts a detached AgentConfig directly. This keeps
        # Team-local YAML outside the shared Agent registry while the runtime
        # identity remains the Team member alias supplied below.
        binding = declaration
        return self._agents.acquire(
            binding, agent_name=member_name, role="teammate", lifecycle="persistent",
            agent_id=f"team--{self.team_run_id}--{member_name}--{generation}",
            metadata={"team_member": member_name, "team_run_id": self.team_run_id, "generation": generation},
        )

    def _settle_team_member_call(self, name: str) -> None:
        """Wait for the old call before changing a member's session identity."""

        try:
            managed = self._agents.get_by_name(name)
        except KeyError:
            managed = None
        if managed is not None:
            if managed.status.value == "running":
                self._agents.cancel_round(str(managed.round_checkpoint.get("round_id") or ""), reason="team_member_lifecycle_change")
                with managed._run_lock:
                    pass
            self._agents.release(managed)
        if self.stopping:
            # An accepted assignment may still be waiting in the inbox.
            # Preserve its claim for root review when this identity closes.
            claimed = [
                task for task in self.list_tasks()
                if task.get("status") == "in_progress" and task.get("claimed_by") == name
            ]
            for task in claimed:
                self.fail_task(str(task["task_id"]), name, error="member_stopped")
            self.set_member_busy(name, False)
        # The worker clears TeamManager.busy in its own finally block, after
        # AgentManager.run has released the per-agent run lock.
        deadline = time.monotonic() + 30.0
        while True:
            member = next((item for item in self.snapshot()["members"] if item["name"] == name), None)
            if member is None or not member.get("busy"):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Team member {name} did not stop")
            time.sleep(0.01)

    def restart_runtime_member(self, name: str) -> dict[str, Any]:
        manager = self
        if self._agents is None:
            raise RuntimeError("Team runtime is not bound")
        with self._dispatch_lock:
            previously_stopping = self.stopping
            self.stopping = True
        try:
            self._settle_team_member_call(name)
            result = manager.restart_member(name)
        finally:
            self.stopping = previously_stopping
        self.wake()
        return result

    def close_runtime_member(self, name: str) -> dict[str, Any]:
        manager = self
        if self._agents is None:
            raise RuntimeError("Team runtime is not bound")
        with self._dispatch_lock:
            previously_stopping = self.stopping
            self.stopping = True
        try:
            self._settle_team_member_call(name)
            result = manager.close_member(name)
        finally:
            self.stopping = previously_stopping
        self.wake()
        return result

    def finish_runtime(self) -> dict[str, Any]:
        manager = self
        if self._agents is None:
            raise RuntimeError("Team runtime is not bound")
        if self.has_active_calls():
            raise ValueError("Team has active member calls")
        snapshot = manager.finish(requester=self.root_name)
        for member in snapshot["members"]:
            if member["name"] == self.root_name:
                continue
            try:
                self._agents.release(self._agents.get_by_name(member["name"]))
            except KeyError:
                pass
        return snapshot

    def on_agent_event(self, managed: ManagedAgent, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if managed.role == "teammate":
            manager = self
            if kind == "round_start":
                manager.set_member_busy(managed.agent_name, True)
            elif kind == "round_end":
                task_id = str(managed.metadata.pop("team_task_id", ""))
                task = next((item for item in manager.list_tasks() if item["task_id"] == task_id), None)
                if task is not None and task.get("status") == "in_progress":
                    if event["data"]["result"]["status"] == "completed":
                        manager.complete_task(task_id, managed.agent_name, result=str(event["data"]["result"].get("output") or ""))
                    else:
                        manager.fail_task(task_id, managed.agent_name, error=str(event["data"]["result"].get("reason") or "member_did_not_submit"))
                queued = self._agents.has_queued_input(managed)
                manager.set_member_busy(managed.agent_name, queued and not self.stopping and not self._runtime_closed)
                self.wake()

    def acknowledge(self, managed: ManagedAgent, message_ids: list[str]) -> None:
        """仅在 AgentManager 保存接收方会话后确认业务消息。"""
        if managed.role == "teammate" and managed.round_checkpoint.get("status") == "active":
            member = next((item for item in self.snapshot()["members"] if item["name"] == managed.agent_name), None)
            if member and member["status"] == "active" and not member["busy"]:
                self.set_member_busy(managed.agent_name, True)
        self.ack_messages(managed.agent_name, message_ids)

    async def awake(self) -> None:
        await blocking(self.wake)

    async def arestart_member(self, name: str) -> dict[str, Any]:
        return await blocking(self.restart_runtime_member, name)

    async def aclose_member(self, name: str) -> dict[str, Any]:
        return await blocking(self.close_runtime_member, name)

    async def afinish(self) -> dict[str, Any]:
        return await blocking(self.finish_runtime)

    async def asend_message(self, sender: str, recipient: str, text: str) -> dict[str, Any]:
        return await blocking(self.send_message, sender, recipient, text)

    async def asnapshot(self) -> dict[str, Any]:
        return await blocking(self.snapshot)

    async def aadd_task(self, title: str, **kwargs: Any) -> dict[str, Any]:
        return await blocking(self.add_task, title, **kwargs)

    async def aupdate_task(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        return await blocking(self.update_task, task_id, **kwargs)

    async def aclaim_task(self, task_id: str, member_name: str) -> dict[str, Any]:
        return await blocking(self.claim_task, task_id, member_name)

    async def acomplete_task(self, task_id: str, member_name: str, result: str = "") -> dict[str, Any]:
        return await blocking(self.complete_task, task_id, member_name, result)

    async def arecover(self, **kwargs: Any) -> dict[str, Any]:
        def recover():
            with self.suspend_dispatch():
                return self.recover(**kwargs)
        return await blocking(recover)

    async def aclose(self) -> None:
        # AgentManager 负责取消和等待 Agent；这里只停止团队业务分派。
        def stop_dispatch():
            with self._dispatch_lock:
                self._runtime_closed = True
                self.stopping = True
        await blocking(stop_dispatch)
