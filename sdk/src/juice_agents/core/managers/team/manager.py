"""Persistent task board, inboxes, and member lifecycle for one Team run."""

from __future__ import annotations

import fcntl
import json
import logging
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterator

from juice_agents.core.registry.common.store import atomic_write_text, normalize_name

logger = logging.getLogger(__name__)


class TeamManager:
    """Own one Team's durable coordination state.

    Every operation rereads the JSON document while holding an OS file lock.
    The lock covers validation and atomic replacement together, so two Manager
    instances cannot claim the same task or overwrite each other's messages.
    Callbacks run only after the lock is released and the replacement succeeds.
    """

    SCHEMA_VERSION = 2

    def __init__(
        self,
        state_dir: str | Path,
        member_names: tuple[str, ...] | list[str] = (),
        *,
        root_name: str = "root",
        team_name: str = "default",
        on_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_path = self.state_dir / "team.json"
        self._lock_path = self.state_dir / ".team.lock"
        self.root_name = normalize_name(root_name)
        self.team_name = normalize_name(team_name)
        self._on_change = on_change
        names = [normalize_name(name) for name in member_names]
        if len(set(names)) != len(names) or self.root_name in names:
            raise ValueError("Team member names must be unique and exclude root")
        with self._locked():
            if not self.state_path.exists():
                now = time.time()
                state = {
                    "schema_version": self.SCHEMA_VERSION,
                    "root_name": self.root_name,
                    "team_name": self.team_name,
                    "finished": False,
                    "finished_at": None,
                    "tasks": [],
                    "messages": [],
                    "members": [self._new_member(self.root_name, role="root", now=now)]
                    + [self._new_member(name, role="member", now=now) for name in names],
                    "created_at": now,
                    "updated_at": now,
                }
                self._write(state)
            else:
                state = self._read()
                if state["root_name"] != self.root_name or state["team_name"] != self.team_name:
                    raise ValueError("Team identity differs from persisted state")

    @staticmethod
    def _new_member(name: str, *, role: str, now: float, agent_name: str = "") -> dict[str, Any]:
        return {
            "name": name,
            "agent_name": agent_name or name,
            "role": role,
            "status": "active",
            "busy": False,
            "generation": 1,
            "created_at": now,
            "updated_at": now,
        }

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != self.SCHEMA_VERSION:
            raise ValueError("Unsupported Team state schema")
        if not isinstance(state.get("tasks"), list) or not isinstance(state.get("messages"), list):
            raise ValueError("Invalid Team state lists")
        if not isinstance(state.get("members"), list):
            raise ValueError("Invalid Team members")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        state["updated_at"] = time.time()
        atomic_write_text(self.state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    def _mutate(self, update: dict[str, Any], operation: Callable[[dict[str, Any]], Any]) -> Any:
        with self._locked():
            state = self._read()
            result = operation(state)
            self._write(state)
            snapshot = deepcopy(state)
        self._emit(update, snapshot)
        return deepcopy(result)

    def _emit(self, update: dict[str, Any], snapshot: dict[str, Any]) -> None:
        """A failed observer cannot undo an already persisted Team transition."""
        if self._on_change is not None:
            try:
                self._on_change({"update": update, "snapshot": snapshot})
            except Exception:
                logger.exception("Team state observer failed")

    @staticmethod
    def _find(items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
        found = next((item for item in items if item.get(key) == value), None)
        if found is None:
            raise ValueError(f"Unknown {key}: {value}")
        return found

    @staticmethod
    def _require_open(state: dict[str, Any]) -> None:
        if state["finished"]:
            raise ValueError("Team is finished")

    def _member(self, state: dict[str, Any], name: str, *, active: bool = False) -> dict[str, Any]:
        member = self._find(state["members"], "name", normalize_name(name))
        if active and member["status"] != "active":
            raise ValueError(f"Team member is closed: {name}")
        return member

    def snapshot(self) -> dict[str, Any]:
        """Return a detached projection suitable for tools and team_update."""
        with self._locked():
            return deepcopy(self._read())

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.snapshot()["tasks"]

    def has_pending_work(self) -> bool:
        state = self.snapshot()
        return not state["finished"] and (
            any(task["status"] != "completed" for task in state["tasks"])
            or any(message["delivered_at"] is None for message in state["messages"])
            or any(member["busy"] for member in state["members"])
        )

    def add_task(
        self,
        title: str,
        description: str = "",
        eligible_members: str | list[str] | tuple[str, ...] = "all",
        dependencies: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise ValueError("Task title is required")
        dependency_ids = [str(item).strip() for item in dependencies]
        if any(not item for item in dependency_ids) or len(set(dependency_ids)) != len(dependency_ids):
            raise ValueError("Task dependencies must be unique nonempty IDs")
        task_id = uuid.uuid4().hex

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            members = self._eligible_members(state, eligible_members)
            existing = {item["task_id"] for item in state["tasks"]}
            if not set(dependency_ids).issubset(existing):
                raise ValueError("Task dependency does not exist")
            now = time.time()
            task = {
                "task_id": task_id,
                "title": title,
                "description": str(description or ""),
                "eligible_members": members,
                "dependencies": dependency_ids,
                "status": "pending",
                "claimed_by": "",
                "error": "",
                "result": "",
                "created_at": now,
                "updated_at": now,
            }
            state["tasks"].append(task)
            logger.info("Team task added: task_id=%s eligible=%s", task_id, members)
            return task

        return self._mutate({"type": "task_added", "task_id": task_id}, operation)

    def _eligible_members(
        self, state: dict[str, Any], value: str | list[str] | tuple[str, ...]
    ) -> list[str]:
        """Expand ``all`` once, under the same lock used to write the task.

        An explicit list is checked against the active Team roster. A future
        member never gains access to an existing task by joining later.
        """
        if value == "all":
            names = [item["name"] for item in state["members"] if item["role"] == "member" and item["status"] == "active"]
        elif isinstance(value, (list, tuple)) and value:
            names = [normalize_name(item) for item in value]
            if len(set(names)) != len(names):
                raise ValueError("eligible_members must be unique")
            for name in names:
                member = self._member(state, name, active=True)
                if member["role"] != "member":
                    raise ValueError("Team root cannot execute tasks")
        else:
            raise ValueError("eligible_members must be 'all' or a nonempty member list")
        if not names:
            raise ValueError("A Team task requires at least one active eligible member")
        return names

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        eligible_members: str | list[str] | tuple[str, ...] | None = None,
        dependencies: list[str] | tuple[str, ...] | None = None,
        status: str | None = None,
        result: str | None = None,
        delete: bool = False,
    ) -> dict[str, Any]:
        """Apply a root decision after checking the claim and dependency gates.

        A running member owns its claim. Once the call is idle, root can put
        failed work back to pending and change its eligible roster atomically.
        """
        task_id = str(task_id or "").strip()

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            task = self._find(state["tasks"], "task_id", task_id)
            claimant = task["claimed_by"]
            running = bool(claimant and self._member(state, claimant)["busy"])
            if running and (delete or eligible_members is not None or status is not None):
                raise ValueError("Cannot reassign or remove a task while its member is running")
            if delete:
                if any(task_id in item["dependencies"] for item in state["tasks"]):
                    raise ValueError("Cannot delete a task used by dependencies")
                state["tasks"].remove(task)
                return task
            if title is not None:
                normalized = str(title).strip()
                if not normalized:
                    raise ValueError("Task title is required")
                task["title"] = normalized
            if description is not None:
                task["description"] = str(description)
            if eligible_members is not None:
                task["eligible_members"] = self._eligible_members(state, eligible_members)
                if task["status"] == "in_progress" and claimant not in task["eligible_members"]:
                    task.update(status="pending", claimed_by="", error="")
            if dependencies is not None:
                ids = [str(item).strip() for item in dependencies]
                existing = {item["task_id"] for item in state["tasks"]} - {task_id}
                if any(not item for item in ids) or len(set(ids)) != len(ids) or not set(ids).issubset(existing):
                    raise ValueError("Task dependencies must be unique existing tasks other than itself")
                # A task may only depend on nodes that cannot reach it. This
                # protects the board from a root edit that would deadlock all
                # descendants despite each referenced ID being valid.
                by_id = {item["task_id"]: item for item in state["tasks"]}
                def reaches_self(dependency_id: str, visited: set[str]) -> bool:
                    if dependency_id == task_id:
                        return True
                    if dependency_id in visited:
                        return False
                    visited.add(dependency_id)
                    return any(reaches_self(parent, visited) for parent in by_id[dependency_id]["dependencies"])
                if any(reaches_self(dependency_id, set()) for dependency_id in ids):
                    raise ValueError("Task dependencies would create a cycle")
                task["dependencies"] = ids
            if status is not None:
                if status not in {"pending", "in_progress", "completed"}:
                    raise ValueError("Invalid Team task status")
                if status == "in_progress" and not claimant:
                    raise ValueError("Cannot set in_progress without a claiming member")
                if status == "completed" and task["status"] == "pending":
                    raise ValueError("Cannot complete an unclaimed task")
                task["status"] = status
                if status == "pending":
                    task.update(claimed_by="", error="")
            if result is not None:
                task["result"] = str(result)
            task["updated_at"] = time.time()
            return task

        kind = "task_deleted" if delete else "task_updated"
        return self._mutate({"type": kind, "task_id": task_id}, operation)

    def claim_task(self, task_id: str, member_name: str) -> dict[str, Any]:
        """Claim one ready task; validation and claim are one disk transaction."""
        task_id = str(task_id or "").strip()
        member_name = normalize_name(member_name)

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            member = self._member(state, member_name, active=True)
            if member["role"] != "member":
                raise ValueError("Team root cannot execute tasks")
            task = self._find(state["tasks"], "task_id", task_id)
            if task["status"] != "pending":
                raise ValueError("Task is already claimed or completed")
            if member_name not in task["eligible_members"]:
                raise ValueError("Member is not eligible for this task")
            completed = {item["task_id"] for item in state["tasks"] if item["status"] == "completed"}
            if not set(task["dependencies"]).issubset(completed):
                raise ValueError("Task dependencies are incomplete")
            task.update(status="in_progress", claimed_by=member_name, error="", updated_at=time.time())
            logger.info("Team task claimed: task_id=%s member=%s", task_id, member_name)
            return task

        return self._mutate({"type": "task_claimed", "task_id": task_id, "member": member_name}, operation)

    def claim_next(self, member_name: str) -> dict[str, Any] | None:
        """Claim the oldest ready task for this member."""
        member_name = normalize_name(member_name)
        with self._locked():
            state = self._read()
            self._require_open(state)
            member = self._member(state, member_name, active=True)
            if member["role"] != "member":
                raise ValueError("Team root cannot execute tasks")
            completed = {item["task_id"] for item in state["tasks"] if item["status"] == "completed"}
            task = next(
                (item for item in state["tasks"] if item["status"] == "pending"
                 and member_name in item["eligible_members"]
                 and set(item["dependencies"]).issubset(completed)),
                None,
            )
            if task is None:
                return None
            task.update(status="in_progress", claimed_by=member_name, error="", updated_at=time.time())
            self._write(state)
            result, snapshot = deepcopy(task), deepcopy(state)
        self._emit({"type": "task_claimed", "task_id": result["task_id"], "member": member_name}, snapshot)
        return result

    def complete_task(self, task_id: str, member_name: str, result: str = "") -> dict[str, Any]:
        task_id, member_name = str(task_id or "").strip(), normalize_name(member_name)

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            task = self._find(state["tasks"], "task_id", task_id)
            if task["status"] != "in_progress" or task["claimed_by"] != member_name:
                raise ValueError("Only the claiming member can complete this task")
            task.update(status="completed", result=str(result or ""), error="", updated_at=time.time())
            logger.info("Team task completed: task_id=%s member=%s", task_id, member_name)
            return task

        return self._mutate({"type": "task_completed", "task_id": task_id, "member": member_name}, operation)

    def fail_task(self, task_id: str, member_name: str, *, error: str) -> dict[str, Any]:
        """Keep the failed claim for root inspection; scheduling never retries it."""
        task_id = str(task_id or "").strip()
        member_name = normalize_name(member_name)

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            task = self._find(state["tasks"], "task_id", task_id)
            if task["status"] != "in_progress" or task["claimed_by"] != member_name:
                raise ValueError("Only the claiming member can fail this task")
            if task["error"]:
                return task
            task.update(error=str(error or "failed"), updated_at=time.time())
            state["messages"].append(self._new_message(
                member_name, state["root_name"],
                f"Task {task_id} failed for {member_name}: {task['error']}. Review with team_task_update.",
            ))
            logger.warning("Team task failed: task_id=%s member=%s error=%s", task_id, member_name, error)
            return task

        return self._mutate({"type": "task_failed", "task_id": task_id, "member": member_name}, operation)

    def recover(self) -> dict[str, Any]:
        """Clear stale busy flags but retain claims for manual root review."""
        def operation(state: dict[str, Any]) -> dict[str, Any]:
            for member in state["members"]:
                member["busy"] = False
            for task in state["tasks"]:
                if task["status"] == "in_progress" and not task["error"]:
                    task.update(
                        error="stale_on_recovery",
                        updated_at=time.time(),
                    )
                    state["messages"].append(self._new_message(
                        task["claimed_by"], state["root_name"],
                        f"Task {task['task_id']} was active during recovery. Review with team_task_update.",
                    ))
            return state

        return self._mutate({"type": "team_recovered"}, operation)

    def send_message(self, sender: str, recipient: str, text: str) -> dict[str, Any]:
        sender, recipient = normalize_name(sender), normalize_name(recipient)
        content = str(text or "").strip()
        if not content:
            raise ValueError("Message text is required")
        message_id = uuid.uuid4().hex

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            self._member(state, sender, active=True)
            self._member(state, recipient, active=True)
            message = self._new_message(sender, recipient, content, message_id=message_id)
            state["messages"].append(message)
            logger.info("Team message queued: message_id=%s from=%s to=%s", message_id, sender, recipient)
            return message

        return self._mutate({"type": "message_sent", "message_id": message_id, "recipient": recipient}, operation)

    def request_member_stop(self, member_name: str, reason: str = "") -> dict[str, Any]:
        """Queue a structured request for root; ordinary messages have no stop effect."""
        member_name = normalize_name(member_name)
        detail = str(reason or "").strip()
        if not detail:
            raise ValueError("Stop request reason is required")
        message_id = uuid.uuid4().hex

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            member = self._member(state, member_name, active=True)
            if member["role"] != "member":
                raise ValueError("Only a Team member can request its own stop")
            message = self._new_message(member_name, state["root_name"], detail, message_id=message_id)
            message.update(kind="member_stop_request", member_name=member_name)
            state["messages"].append(message)
            logger.info("Team member stop requested: member=%s message_id=%s", member_name, message_id)
            return message

        return self._mutate({"type": "member_stop_requested", "member": member_name, "message_id": message_id}, operation)

    @staticmethod
    def _new_message(sender: str, recipient: str, content: str, *, message_id: str | None = None) -> dict[str, Any]:
        now = time.time()
        return {
            "message_id": message_id or uuid.uuid4().hex,
            "from": sender,
            "to": recipient,
            "text": content,
            "created_at": now,
            "timestamp": now,
            "delivered_at": None,
        }

    def pending_messages(self, member_name: str) -> list[dict[str, Any]]:
        member_name = normalize_name(member_name)
        state = self.snapshot()
        self._member(state, member_name)
        return [item for item in state["messages"] if item["to"] == member_name and item["delivered_at"] is None]

    def ack_messages(self, member_name: str, message_ids: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
        """Confirm delivery after the caller has saved the receiving session step."""
        member_name = normalize_name(member_name)
        ids = {str(item).strip() for item in message_ids}
        if not ids or "" in ids:
            return []

        with self._locked():
            state = self._read()
            self._member(state, member_name)
            owned = {item["message_id"]: item for item in state["messages"] if item["to"] == member_name}
            if not ids.issubset(owned):
                raise ValueError("Message does not belong to member")
            now = time.time()
            changed = []
            for message_id in ids:
                message = owned[message_id]
                if message["delivered_at"] is None:
                    message["delivered_at"] = now
                    changed.append(deepcopy(message))
            if not changed:
                return []
            self._write(state)
            snapshot = deepcopy(state)
        self._emit(
            {"type": "messages_acknowledged", "member": member_name, "message_ids": sorted(ids)},
            snapshot,
        )
        return changed

    def reconcile_messages(self, member_name: str, saved_message_ids: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
        """On recovery, mark IDs already present in the durable Agent session."""
        pending_ids = {item["message_id"] for item in self.pending_messages(member_name)}
        matched = pending_ids.intersection(saved_message_ids)
        return self.ack_messages(member_name, sorted(matched)) if matched else []

    def create_member(self, name: str, agent_name: str = "") -> dict[str, Any]:
        name = normalize_name(name)
        agent_name = normalize_name(agent_name) if agent_name else name

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            if any(item["name"] == name for item in state["members"]):
                raise ValueError("Team member name already exists")
            member = self._new_member(name, role="member", now=time.time(), agent_name=agent_name)
            state["members"].append(member)
            logger.info("Team member created: member=%s agent=%s", name, agent_name)
            return member

        return self._mutate({"type": "member_created", "member": name}, operation)

    def restart_member(self, name: str) -> dict[str, Any]:
        name = normalize_name(name)

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            member = self._member(state, name)
            if member["role"] == "root":
                raise ValueError("Cannot restart Team root")
            if member["busy"]:
                raise ValueError("Cannot restart a busy Team member")
            member.update(status="active", busy=False, generation=member["generation"] + 1, updated_at=time.time())
            logger.info("Team member restarted: member=%s generation=%d", name, member["generation"])
            return member

        return self._mutate({"type": "member_restarted", "member": name}, operation)

    def close_member(self, name: str) -> dict[str, Any]:
        name = normalize_name(name)

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            member = self._member(state, name, active=True)
            if member["role"] == "root":
                raise ValueError("Cannot close Team root")
            if member["busy"]:
                raise ValueError("Cannot close a busy Team member")
            member.update(status="closed", updated_at=time.time())
            # Preserve undelivered content, but make it actionable after the
            # recipient is closed. Root can inspect and acknowledge redirects.
            for message in state["messages"]:
                if message["to"] == name and message["delivered_at"] is None:
                    message.update(
                        to=state["root_name"],
                        original_recipient=name,
                        redirected_at=time.time(),
                    )
            # A closed member's unfinished claims remain visible for root
            # review. Pending tasks keep their frozen eligibility list.
            for task in state["tasks"]:
                if task["status"] == "in_progress" and task["claimed_by"] == name and not task["error"]:
                    task.update(error="member_closed", updated_at=time.time())
            logger.info("Team member closed: member=%s", name)
            return member

        return self._mutate({"type": "member_closed", "member": name}, operation)

    def set_member_busy(self, name: str, busy: bool) -> dict[str, Any]:
        name = normalize_name(name)

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            member = self._member(state, name, active=True)
            member.update(busy=bool(busy), updated_at=time.time())
            return member

        return self._mutate({"type": "member_busy", "member": name, "busy": bool(busy)}, operation)

    def finish(self, requester: str = "root") -> dict[str, Any]:
        requester = normalize_name(requester)

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            self._require_open(state)
            if requester != state["root_name"]:
                raise ValueError("Only Team root can finish the Team")
            if any(item["status"] != "completed" for item in state["tasks"]):
                raise ValueError("Team has unfinished tasks")
            if any(item["delivered_at"] is None for item in state["messages"]):
                raise ValueError("Team has pending messages")
            if any(item["busy"] for item in state["members"] if item["name"] != requester):
                raise ValueError("Team has active member calls")
            state.update(finished=True, finished_at=time.time())
            logger.info("Team finished by %s", requester)
            return state

        return self._mutate({"type": "team_finished", "requester": requester}, operation)
