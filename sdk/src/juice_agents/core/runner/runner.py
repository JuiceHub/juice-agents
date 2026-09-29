"""Declarative Runner façade.

The Runner owns only request-scoped coordination.  Static definitions belong
to Registries; mutable objects, sessions, pools and checkpoints belong to
Managers.  No mode is allowed to inject an execution callback into this class.
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterator
from uuid import uuid4
import weakref

from juice_agents.core.agent import AgentExecutionInterrupted, AgentManager, AgentManagerCallbacks, AgentStatus, ManagedAgent
from juice_agents.core.agent.attachments import RuntimeAttachment, merge_runtime_attachments
from juice_agents.core.agent.root_config import build_root_config
from juice_agents.core.agent.tools.runtime import ToolExecutionContext
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.config.model_catalog import DEFAULT_RUNTIME_CONFIG_PATH
from juice_agents.core.graph.runs import GraphRunStore
from juice_agents.core.managers import AsyncTaskManager, GraphRunManager, ToolManager
from juice_agents.core.registry import AgentRegistry, GraphRegistry

from .config import AgentBinding, Capability, RunnerConfig, mode_registry
from .errors import RunnerBusyError
from .execution.context import RunnerContext
from .execution.event_queue import RunnerEventQueue
from .persistence.store import blank_runner_state, build_layout, load_runner_state, write_runner_state
from .status import settle_status
from .types.definitions import PermissionMode, RunnerState, normalize_permission_mode
from .types.identity import new_runner_id
from .workspace import RuntimeWorkspace

logger = logging.getLogger(__name__)
AskHandler = Callable[[dict[str, Any]], dict[str, Any]]


class _GraphAgentDispatcher:
    """Weak, narrow bridge from graph work to Runner-owned AgentManager.

    GraphRunManager needs to request structured Agent work, but it must not
    own the Runner or a live Agent.  The bridge exposes only graph-required
    methods and uses a weak reference, so a manager can never extend a closed
    Runner's lifetime.
    """

    def __init__(self, runner: "Runner") -> None:
        self._runner_ref = weakref.ref(runner)

    def _runner(self) -> "Runner":
        runner = self._runner_ref()
        if runner is None:
            raise RuntimeError("graph Agent dispatch target has been released")
        return runner

    @property
    def runner_id(self) -> str:
        return self._runner().runner_id

    @property
    def root_agent_name(self) -> str:
        return self._runner().root_agent_name

    def invoke_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        return self._runner().invoke_agent(spec)

    def agent_type_for(self, agent_name: str) -> str | None:
        instance = self._runner().agent_manager.get_by_name(agent_name).instance
        if instance is None:
            return None
        resolved = str(getattr(instance, "_resolved_agent_type", "") or "").strip()
        if resolved:
            return resolved
        declared = getattr(instance, "_declared_agent_config", None)
        configured = str(getattr(declared, "agent_type", "") or "").strip()
        if configured and configured != "default":
            return configured
        return "codeact" if "codeact" in type(instance).__name__.lower() else None


class _RunnerStream:
    """Close an unstarted request as reliably as an exhausted generator."""

    def __init__(self, iterator: Iterator[dict[str, Any]], on_unstarted_close: Callable[[], None]) -> None:
        self._iterator = iterator
        self._on_unstarted_close = on_unstarted_close
        self._started = False
        self._closed = False

    def __iter__(self) -> "_RunnerStream":
        return self

    def __next__(self) -> dict[str, Any]:
        if self._closed:
            raise StopIteration
        self._started = True
        try:
            return next(self._iterator)
        except StopIteration:
            self._closed = True
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            close = getattr(self._iterator, "close", None)
            if callable(close):
                close()
        else:
            self._on_unstarted_close()


class Runner:
    """A single entry point that dispatches every config through Managers.

    ``agent``, ``plan``, ``group`` and ``team`` differ only in immutable
    :class:`RunnerConfig` data.  There are no dynamic factories, no live
    Agent map and no mode-specific execution loop in this class.
    """

    def __init__(
        self,
        *,
        permission_mode: PermissionMode | str,
        runner_config: RunnerConfig,
        runner_id: str,
        base_dir: str | Path,
        state: RunnerState,
        runtime_config_path: str | Path | None = None,
        storage_root: str | Path | None = None,
    ) -> None:
        self.permission_mode = normalize_permission_mode(permission_mode)
        self.config = mode_registry.resolve(runner_config)
        self.runner_id = str(runner_id or "").strip()
        if not self.runner_id:
            raise ValueError("runner_id 不能为空")
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.layout = build_layout(self.base_dir, self.runner_id, root_dir=storage_root)
        self.layout.ensure_dirs()
        self.config_context = ConfigurationContext.from_workspace(self.base_dir)
        self.runtime_config_path = Path(runtime_config_path or DEFAULT_RUNTIME_CONFIG_PATH).expanduser().resolve()
        self.state: RunnerState = dict(state)
        self.state.update({
            "permission_mode": self.permission_mode,
            "agent_mode": self.config.mode_id,
            "runner_config": self.config.to_dict(),
            # ``root`` is the stable runtime identity across every mode.
            # Never restore a historical mode-specific declaration name here.
            "root_agent_name": "root",
            "root_agent_id": str(state.get("root_agent_id") or "root"),
        })
        self.runtime_workspace = RuntimeWorkspace(workspace_dir=self.base_dir)
        self._request_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._stop_reason: str | None = None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._listeners_lock = threading.RLock()
        self._team_updates: deque[dict[str, Any]] = deque(maxlen=256)
        self._ask_handler: AskHandler | None = None
        self._pending_attachments: dict[str, list[RuntimeAttachment]] = {}
        self._team_dispatch_lock = threading.RLock()
        self._team_dispatching = False
        self._team_stopping = False

        self.event_queue = RunnerEventQueue()
        self.tool_manager = ToolManager(
            state_dir=self.layout.tools_dir,
            policy_check=self._check_tool_policy,
        )
        self.async_task_manager = AsyncTaskManager(
            event_sink=self.event_queue,
            state_dir=self.layout.async_tasks_dir,
            scope_id=self.runner_id,
            scope_kind="runner",
            scope_meta={"runner_id": self.runner_id, "mode_id": self.config.mode_id},
            runtime_workspace=self.runtime_workspace,
            default_base_dir=self.base_dir,
            max_workers=self.config.concurrency_limit,
        )
        self.agent_registry = AgentRegistry(config_context=self.config_context)
        # Team selection is Runner state because a Team manifest is a static
        # relationship, while a Runner chooses one relationship for its
        # current conversation.  It never duplicates member configurations.
        self._team_member_names = self._load_team_member_names()
        if self.config.mode_id == "team":
            self.state["team_name"] = str(self.state.get("team_name") or "default")
        self.root_config = build_root_config(
            mode_id=self.config.mode_id,
            plan_file=self.layout.plans_dir / "root.md",
            managed_agent_names=self._discover_available_agent_names(),
        )
        self.agent_manager = AgentManager(
            registry=self.agent_registry,
            runner_id=self.runner_id,
            layout=self.layout,
            runtime_config_path=self.runtime_config_path,
            permission_mode=self.permission_mode,
            workspace_dir=self.base_dir,
            callbacks=AgentManagerCallbacks(
                bind_runtime=self._bind_managed_agent,
                attachments_provider=self._attachments_for,
                after_step=self._after_agent_step,
                after_persist=self._after_agent_persist,
                after_release=self._after_agent_release,
            ),
        )
        self.graph_manager = GraphRunManager(
            store=GraphRunStore(self.layout.root_dir),
            registry=GraphRegistry(config_context=self.config_context),
            config_context=self.config_context,
            runner_id=self.runner_id,
            agent_dispatcher=_GraphAgentDispatcher(self),
            owner_agent_name=self.root_agent_name,
            concurrency_limit=self.config.concurrency_limit,
            lifecycle_callback=self._emit_manager_lifecycle,
        )
        self.team_manager = self._create_team_manager()
        logger.info("runner_initialized runner_id=%s mode_id=%s", self.runner_id, self.mode_id)

    def _create_team_manager(self) -> Any | None:
        """Bind mutable Team state only when the declarative config enables it."""

        if self.config.team is None:
            return None
        from juice_agents.core.managers.team import TeamManager

        run_id = str(self.state.get("team_run_id") or uuid4().hex)
        self.state["team_run_id"] = run_id
        return TeamManager(
            self.layout.root_dir / "team" / self.team_name / run_id,
            member_names=tuple(sorted(self._team_member_names or ())),
            team_name=self.team_name,
            on_change=self._on_team_change,
        )

    def _on_team_change(self, change: dict[str, Any]) -> None:
        event = {
            "kind": "team_update", "runner_id": self.runner_id,
            "mode_id": self.mode_id, "team_name": self.team_name,
            **change,
        }
        with self._listeners_lock:
            self._team_updates.append(event)
        self._publish(event)
        self.wake_team()

    def _drain_team_updates(self) -> list[dict[str, Any]]:
        with self._listeners_lock:
            updates = list(self._team_updates)
            self._team_updates.clear()
        return updates

    def _active_team_calls(self) -> bool:
        if self.team_manager is None:
            return False
        if any(
            member.get("busy")
            for member in self.team_manager.snapshot()["members"]
            if member.get("name") != "root"
        ):
            return True
        return any(
            task.get("type") == "teammate"
            for task in self.async_task_manager.list_tasks(statuses={"pending", "running"})
        )

    def wake_team(self) -> None:
        """Schedule one bounded round per ready member; idle members use no worker."""

        manager = self.team_manager
        if manager is None or not self.config.team or not self.config.team.auto_dispatch:
            return
        with self._team_dispatch_lock:
            if self._team_dispatching or self._team_stopping:
                return
            self._team_dispatching = True
            try:
                for member in manager.snapshot()["members"]:
                    name = str(member.get("name") or "")
                    if name == "root" or member.get("status") == "closed" or member.get("busy"):
                        continue
                    task = manager.claim_next(name)
                    messages = manager.pending_messages(name)
                    if task is None and not messages:
                        continue
                    manager.set_member_busy(name, True)
                    task_id = "" if task is None else str(task.get("task_id") or "")
                    try:
                        self.async_task_manager.launch(
                            task_type="teammate",
                            owner_agent_id=self.root_agent_id,
                            owner_agent_name=self.root_agent_name,
                            description=f"team:{name}:{task_id or 'inbox'}",
                            runner=lambda _task, _path, n=name, t=task: self._run_team_member(n, t),
                            summary_builder=lambda _result, n=name: f"team member {n} round completed",
                            metadata={"member_name": name, "team_task_id": task_id},
                        )
                    except Exception:
                        manager.set_member_busy(name, False)
                        if task_id:
                            manager.fail_task(task_id, name, error="dispatch_failed")
                        raise
            finally:
                self._team_dispatching = False

    def _run_team_member(self, member_name: str, task: dict[str, Any] | None) -> Any:
        """Run one member turn with a persistent transcript and durable inbox."""

        manager = self.team_manager
        assert manager is not None
        task_id = "" if task is None else str(task.get("task_id") or "")
        if self._team_stopping or self._cancel_event.is_set():
            # A queued pool job can begin after stop marked its async record
            # killed. Never start a fresh Agent turn from that stale callback.
            if task_id:
                manager.fail_task(task_id, member_name, error="stopped_before_start")
            manager.set_member_busy(member_name, False)
            return None
        member = next(item for item in manager.snapshot()["members"] if item["name"] == member_name)
        from juice_agents.core.registry.teams import TeamRegistry
        from juice_agents.core.registry.tools.types import ToolRef
        from juice_agents.core.team.builtin.configs import DEFAULT_TEAMMATE_INSTRUCTIONS

        declaration = TeamRegistry(config_context=self.config_context).load_member_config(self.team_name, member_name)
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
        managed = self.agent_manager.acquire(
            binding, agent_name=member_name, role="teammate", lifecycle="persistent",
            agent_id=f"team--{self.state['team_run_id']}--{member_name}--{generation}",
            metadata={"team_member": member_name, "team_run_id": self.state["team_run_id"], "generation": generation},
        )
        if self._team_stopping or self._cancel_event.is_set():
            self.agent_manager.release(managed)
            if task_id:
                manager.fail_task(task_id, member_name, error="stopped_before_start")
            manager.set_member_busy(member_name, False)
            return None
        prompt = (
            f"{task_id}: {task.get('title', '')}\n{task.get('description', '')}"
            if task is not None else f"{len(manager.pending_messages(member_name))} new messages"
        )
        try:
            result = self.agent_manager.run(
                managed, prompt,
                attachments=self._team_initial_attachments(member_name),
            )
            if task_id:
                current = next((item for item in manager.list_tasks() if item["task_id"] == task_id), None)
                if current is not None and current.get("status") == "in_progress":
                    last_step = managed.session.steps[-1] if managed.session.steps else None
                    if getattr(last_step, "round_outcome", None) == "submitted" and not getattr(last_step, "error", None):
                        manager.complete_task(task_id, member_name, result=str(result or ""))
                    else:
                        manager.fail_task(task_id, member_name, error="member_did_not_submit")
            return result
        except AgentExecutionInterrupted:
            if task_id:
                manager.fail_task(task_id, member_name, error="interrupted")
            raise
        except Exception:
            if task_id:
                manager.fail_task(task_id, member_name, error="failed")
            logger.exception("team_member_round_failed runner_id=%s member=%s", self.runner_id, member_name)
            raise
        finally:
            manager.set_member_busy(member_name, False)

    def _team_initial_attachments(self, member_name: str) -> list[RuntimeAttachment]:
        manager = self.team_manager
        if manager is None:
            return []
        return merge_runtime_attachments(inbox_messages=manager.pending_messages(member_name))

    def _settle_team_member_call(self, name: str) -> None:
        """Wait for the old call before changing a member's session identity."""

        for task in self.async_task_manager.list_tasks(statuses={"pending", "running"}):
            if task.get("type") == "teammate" and dict(task.get("metadata") or {}).get("member_name") == name:
                self.async_task_manager.cancel(str(task["async_task_id"]), reason="team_member_lifecycle_change")
        try:
            managed = self.agent_manager.get_by_name(name)
        except KeyError:
            managed = None
        if managed is not None:
            if managed.status is AgentStatus.RUNNING:
                self.agent_manager.interrupt(managed, reason="team_member_lifecycle_change")
                with managed._run_lock:
                    pass
            self.agent_manager.release(managed)
        if self.team_manager is not None and self._team_stopping:
            # A pool future cancelled before start has no worker finally block.
            # Clear its durable busy/claim state here so stop and restart do
            # not wait forever for a callback that can never execute.
            claimed = [
                task for task in self.team_manager.list_tasks()
                if task.get("status") == "in_progress" and task.get("claimed_by") == name
            ]
            for task in claimed:
                self.team_manager.fail_task(str(task["task_id"]), name, error="member_stopped")
            self.team_manager.set_member_busy(name, False)
        # The worker clears TeamManager.busy in its own finally block, after
        # AgentManager.run has released the per-agent run lock.
        deadline = time.monotonic() + 30.0
        while self.team_manager is not None:
            member = next((item for item in self.team_manager.snapshot()["members"] if item["name"] == name), None)
            if member is None or not member.get("busy"):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Team member {name} did not stop")
            time.sleep(0.01)

    def restart_team_member(self, name: str) -> dict[str, Any]:
        manager = self.team_manager
        if manager is None:
            raise PermissionError("Team mode is not active")
        with self._team_dispatch_lock:
            self._team_stopping = True
        try:
            self._settle_team_member_call(name)
            result = manager.restart_member(name)
        finally:
            self._team_stopping = False
        self.wake_team()
        return result

    def close_team_member(self, name: str) -> dict[str, Any]:
        manager = self.team_manager
        if manager is None:
            raise PermissionError("Team mode is not active")
        with self._team_dispatch_lock:
            self._team_stopping = True
        try:
            self._settle_team_member_call(name)
            result = manager.close_member(name)
        finally:
            self._team_stopping = False
        self.wake_team()
        return result

    def finish_team(self) -> dict[str, Any]:
        manager = self.team_manager
        if manager is None:
            raise PermissionError("Team mode is not active")
        active = [
            task for task in self.async_task_manager.list_tasks(statuses={"pending", "running"})
            if task.get("type") == "teammate"
        ]
        if active:
            raise ValueError("Team has active member calls")
        snapshot = manager.finish(requester="root")
        for member in snapshot["members"]:
            if member["name"] == "root":
                continue
            try:
                self.agent_manager.release(self.agent_manager.get_by_name(member["name"]))
            except KeyError:
                pass
        return snapshot

    def create_team(self, name: str, *, description: str = "") -> dict[str, Any]:
        """Create a reusable empty definition, then select a fresh task board."""
        from juice_agents.core.registry.teams import TeamRegistry

        self._require_team_switch_ready()
        TeamRegistry(config_context=self.config_context).create_empty(name, description)
        return self.use_team(name)

    def _require_team_switch_ready(self) -> None:
        """Guard selection before changing the static registry or run state."""
        manager = self.team_manager
        if manager is None:
            raise PermissionError("Team mode is not active")
        current = manager.snapshot()
        if self._active_team_calls():
            raise ValueError("Team has active member calls")
        if not current["finished"] and (current["tasks"] or current["messages"]):
            raise ValueError("Finish the current Team before selecting another")

    def use_team(self, name: str) -> dict[str, Any]:
        """Select a definition and allocate a new isolated Team run."""
        self._require_team_switch_ready()
        from juice_agents.core.registry.teams import TeamRegistry

        selected = TeamRegistry(config_context=self.config_context).validate(name)
        self.state["team_name"] = selected.team_name
        self.state["team_run_id"] = uuid4().hex
        self._team_member_names = frozenset(selected.member_names)
        self.team_manager = self._create_team_manager()
        self.persist_runner_state()
        logger.info("team_selected runner_id=%s team=%s run=%s", self.runner_id, selected.team_name, self.state["team_run_id"])
        return self.team_manager.snapshot()

    def create_team_member(
        self, name: str, *, agent_name: str | None = None, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Add a definition and runtime member as one logical root operation."""
        manager = self.team_manager
        if manager is None:
            raise PermissionError("Team mode is not active")
        if any(item["name"] == name for item in manager.snapshot()["members"]):
            raise ValueError("Team member name already exists")
        from juice_agents.core.registry.teams import TeamRegistry

        teams = TeamRegistry(config_context=self.config_context)
        declaration = teams.add_member(self.team_name, name, agent_name=agent_name, config=config)
        try:
            member = manager.create_member(name, declaration.name)
        except Exception:
            teams.remove_member(self.team_name, name)
            raise
        self._team_member_names = frozenset({*(self._team_member_names or ()), name})
        self.root_config = build_root_config(
            mode_id=self.mode_id,
            plan_file=self.plan_file_path(),
            managed_agent_names=self._discover_available_agent_names(),
        )
        return member

    def _after_agent_persist(self, snapshot: dict[str, Any]) -> None:
        """Acknowledge only inbox IDs already present in a durable session."""

        manager = self.team_manager
        if manager is None:
            return
        member_name = str(snapshot.get("agent_name") or "")
        ids = {
            str(attachment.get("payload", {}).get("message_id") or "")
            for step in dict(snapshot.get("session") or {}).get("steps") or []
            if isinstance(step, dict)
            for attachment in step.get("attachments") or []
            if isinstance(attachment, dict) and attachment.get("attachment_type") == "inbox_message"
        }
        ids.discard("")
        if ids:
            manager.reconcile_messages(member_name, sorted(ids))

    @property
    def mode_id(self) -> str:
        return self.config.mode_id

    @property
    def agent_mode(self) -> str:
        """Display identity retained for protocol payloads, not a control flow key."""

        return self.config.mode_id

    @property
    def root_agent_name(self) -> str:
        return str(self.state.get("root_agent_name") or self.config.root_binding.agent_name)

    @property
    def root_agent_id(self) -> str:
        return str(self.state.get("root_agent_id") or "root")

    @property
    def root_dir(self) -> Path:
        return self.layout.root_dir

    @property
    def team_name(self) -> str:
        """The selected schema-2 Team name for a Team Runner, if any."""

        return str(self.state.get("team_name") or "")

    def _load_team_member_names(self, *, mode_id: str | None = None) -> frozenset[str] | None:
        """Resolve the selected Team's global member names without runtime work."""

        if str(mode_id or self.config.mode_id) != "team":
            return None
        from juice_agents.core.registry import TeamRegistry
        from juice_agents.core.team.builtin.configs import (
            DEFAULT_TEAM_NAME,
            build_default_team_config,
        )

        selected = str(self.state.get("team_name") or DEFAULT_TEAM_NAME).strip()
        teams = TeamRegistry(config_context=self.config_context)
        try:
            manifest = teams.validate(selected)
        except FileNotFoundError:
            # Only the known default is initialized implicitly. Its manifest
            # starts empty; membership is always an explicit root decision.
            if selected != DEFAULT_TEAM_NAME:
                raise
            teams.create(build_default_team_config())
            manifest = teams.validate(selected)
        return frozenset(manifest.member_names)

    def available_agent_names(self) -> frozenset[str] | None:
        """Return the root's policy boundary, or ``None`` for all mode agents.

        This is the single selection input used by Registry discovery, prompt
        projection and direct dispatch.  A Team narrows it to its manifest;
        Plan mode narrows it to its approved read-only investigators.
        """

        policy_names = frozenset(self.config.tool_policy.allowed_agent_names)
        if policy_names:
            return policy_names
        team_manager = getattr(self, "team_manager", None)
        if team_manager is not None:
            dynamic = {
                str(item.get("agent_name") or item.get("name") or "")
                for item in team_manager.snapshot()["members"]
                if item.get("status") != "closed" and item.get("name") != "root"
            }
            return frozenset(dynamic)
        return self._team_member_names

    def _discover_available_agent_names(self) -> tuple[str, ...]:
        """Resolve root-visible names through the Registry's shared predicate."""

        response = self.agent_registry.list_available_agents(
            mode_id=self.mode_id,
            runner=self,
        )
        return tuple(item["name"] for item in response["agents"])

    def list_available_agents(self, *, name: str | None = None) -> dict[str, Any]:
        """Expose the same effective projection used by root prompt/dispatch."""

        return self.agent_registry.list_available_agents(
            name=name,
            mode_id=self.mode_id,
            runner=self,
        )

    def is_agent_available(self, agent_name: str) -> bool:
        """Check one target without ever acquiring an Agent instance."""

        try:
            return bool(self.list_available_agents(name=agent_name).get("agents"))
        except (FileNotFoundError, ValueError):
            return False

    def disable_agent(self, agent_name: str) -> None:
        """Disable an Agent only for this Runner and the current mode."""

        normalized = str(agent_name or "").strip()
        if not normalized:
            raise ValueError("agent name 不能为空")
        if not self.is_agent_available(normalized):
            raise ValueError(f"当前 Runner 无可用 Agent: {normalized}")
        by_mode = dict(self.state.get("disabled_agent_names_by_mode") or {})
        disabled = {str(value).strip() for value in by_mode.get(self.mode_id, []) if str(value).strip()}
        disabled.add(normalized)
        by_mode[self.mode_id] = sorted(disabled)
        self.state["disabled_agent_names_by_mode"] = by_mode
        try:
            root = self.agent_manager.get(self.root_agent_id).instance
        except KeyError:
            root = None
        if root is not None:
            root._runtime_disabled_managed_agent_names = set(disabled)
        self.persist_runner_state()
        logger.info("runner_agent_disabled runner_id=%s mode_id=%s agent=%s", self.runner_id, self.mode_id, normalized)

    def enable_agent(self, agent_name: str) -> None:
        """Explicitly reactivate a Runner/mode-local Agent disable."""

        normalized = str(agent_name or "").strip()
        if not normalized:
            raise ValueError("agent name 不能为空")
        by_mode = dict(self.state.get("disabled_agent_names_by_mode") or {})
        disabled = {str(value).strip() for value in by_mode.get(self.mode_id, []) if str(value).strip()}
        disabled.discard(normalized)
        if disabled:
            by_mode[self.mode_id] = sorted(disabled)
        else:
            by_mode.pop(self.mode_id, None)
        self.state["disabled_agent_names_by_mode"] = by_mode
        try:
            root = self.agent_manager.get(self.root_agent_id).instance
        except KeyError:
            root = None
        if root is not None:
            root._runtime_disabled_managed_agent_names = set(disabled)
        self.persist_runner_state()
        logger.info("runner_agent_enabled runner_id=%s mode_id=%s agent=%s", self.runner_id, self.mode_id, normalized)

    def has_capability(self, capability: Capability | str) -> bool:
        """Return whether this declarative Runner configuration permits it."""

        return Capability(capability) in self.config.capability_flags

    def mode_switch_block_reason(self) -> str | None:
        """Return why configuration cannot change at the current boundary.

        A persistent idle root is intentionally not a blocker; it is released
        and recreated from its snapshot as part of a successful switch.  Work
        that can still mutate state, however, must settle first.
        """

        if str(self.state.get("status") or "") not in {"", "idle"}:
            return f"runner 状态为 {self.state.get('status')!r}"
        if any(
            managed.status is AgentStatus.RUNNING
            for managed in self.agent_manager.live_agents.values()
        ):
            return "存在正在执行的 Agent"
        if self.async_task_manager.list_tasks(statuses={"pending", "running"}):
            return "存在未完成的异步任务"
        if self.graph_manager.store.list(statuses={"pending", "running", "paused"}):
            return "存在未完成的 Graph"
        if self.team_manager is not None and self.team_manager.has_pending_work():
            return "存在未完成的 Team 工作"
        return None

    def reconfigure(
        self,
        *,
        mode_id: str | RunnerConfig | None = None,
        permission_mode: PermissionMode | str | None = None,
        plan_return_mode: str | None = None,
    ) -> None:
        """Apply a new declarative mode at an explicit idle boundary.

        The Runner id and manager snapshot directory stay stable.  Releasing
        the root first writes its session snapshot; the next request acquires
        a fresh root with the latest Registry declarations and effective
        in-memory root configuration.
        """

        target = mode_registry.resolve(mode_id or self.config)
        target_permission = normalize_permission_mode(permission_mode or self.permission_mode)
        if target == self.config and target_permission == self.permission_mode:
            return
        blocked = self.mode_switch_block_reason()
        if blocked:
            raise RunnerBusyError(f"runner {self.runner_id} 正忙，不能切换模式：{blocked}")

        # Validate a selected Team before releasing the current root.  A bad
        # manifest must leave the current mode and its session untouched.
        target_team_members = self._load_team_member_names(mode_id=target.mode_id)

        try:
            root = self.agent_manager.get(self.root_agent_id)
        except KeyError:
            root = None
        if root is not None:
            self.agent_manager.release(root)

        # Managers holding worker pools are recreated only after the activity
        # check above.  Their durable stores remain in the same Runner layout.
        self.graph_manager.release(wait=True)
        self.async_task_manager.release(wait=True)
        self.config = target
        self.permission_mode = target_permission
        self._team_member_names = target_team_members
        self.team_manager = None
        if target.mode_id == "team":
            self.state["team_name"] = str(self.state.get("team_name") or "default")
            # Re-entering Team mode starts a fresh board from its reusable
            # definition, even when a previous Team run is already finished.
            self.state["team_run_id"] = uuid4().hex
        self.root_config = build_root_config(
            mode_id=target.mode_id,
            plan_file=self.plan_file_path(),
            managed_agent_names=self._discover_available_agent_names(),
        )
        self.async_task_manager = AsyncTaskManager(
            event_sink=self.event_queue,
            state_dir=self.layout.async_tasks_dir,
            scope_id=self.runner_id,
            scope_kind="runner",
            scope_meta={"runner_id": self.runner_id, "mode_id": target.mode_id},
            runtime_workspace=self.runtime_workspace,
            default_base_dir=self.base_dir,
            max_workers=target.concurrency_limit,
        )
        self.graph_manager = GraphRunManager(
            store=GraphRunStore(self.layout.root_dir),
            registry=GraphRegistry(config_context=self.config_context),
            config_context=self.config_context,
            runner_id=self.runner_id,
            agent_dispatcher=_GraphAgentDispatcher(self),
            owner_agent_name="root",
            concurrency_limit=target.concurrency_limit,
            lifecycle_callback=self._emit_manager_lifecycle,
        )
        self._team_stopping = False
        self.team_manager = self._create_team_manager()
        self.agent_manager.permission_mode = target_permission
        self.state.update(
            {
                "permission_mode": target_permission,
                "agent_mode": target.mode_id,
                "runner_config": target.to_dict(),
                "root_agent_name": "root",
            }
        )
        if target.mode_id == "plan":
            candidate = str(plan_return_mode or self.state.get("plan_return_mode") or "agent").strip()
            self.state["plan_return_mode"] = mode_registry.resolve(candidate).mode_id
        else:
            self.state["plan_return_mode"] = ""
            self.state["pending_plan_exit"] = None
        self.persist_runner_state()
        logger.info(
            "runner_reconfigured runner_id=%s mode_id=%s permission_mode=%s",
            self.runner_id,
            target.mode_id,
            target_permission,
        )

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    @classmethod
    def create(
        cls,
        *,
        permission_mode: PermissionMode | str = "default",
        runner_config: RunnerConfig | str | None = None,
        team_name: str | None = None,
        base_dir: str | Path | None = None,
        runner_id: str | None = None,
        runtime_config_path: str | Path | None = None,
        storage_root: str | Path | None = None,
        **removed: Any,
    ) -> "Runner":
        """Create from static bindings only; passing a live Agent is rejected."""

        if removed:
            raise TypeError("Runner.create 已移除旧运行时参数: " + ", ".join(sorted(removed)))
        config = mode_registry.resolve(runner_config)
        resolved_base = Path(base_dir or Path.cwd()).expanduser().resolve()
        selected_team_name = str(team_name or "").strip()
        if config.mode_id == "team":
            from juice_agents.core.registry import TeamRegistry
            from juice_agents.core.team.builtin.configs import (
                DEFAULT_TEAM_NAME,
                build_default_team_config,
            )

            selected_team_name = selected_team_name or DEFAULT_TEAM_NAME
            context = ConfigurationContext.from_workspace(resolved_base)
            teams = TeamRegistry(config_context=context)
            try:
                teams.get_manifest(selected_team_name)
            except FileNotFoundError:
                # Only the known built-in default is initialized implicitly.
                # A named Team is user data and must be created explicitly.
                if selected_team_name != DEFAULT_TEAM_NAME:
                    raise
                teams.create(build_default_team_config())
        elif selected_team_name:
            raise ValueError("team_name 仅可用于 team mode")
        normalized_id = str(runner_id or "").strip() or new_runner_id(config.mode_id, config.root_binding.agent_name)
        root = Path(storage_root).expanduser().resolve() if storage_root else build_layout(resolved_base, normalized_id).root_dir
        state = blank_runner_state(
            runner_id=normalized_id,
            permission_mode=normalize_permission_mode(permission_mode),
            agent_mode=config.mode_id,
            root_agent_name=config.root_binding.agent_name,
        )
        if config.mode_id == "team":
            state["team_name"] = selected_team_name
        state["runner_config"] = config.to_dict()
        runner = cls(
            permission_mode=permission_mode,
            runner_config=config,
            runner_id=normalized_id,
            base_dir=resolved_base,
            state=state,
            runtime_config_path=runtime_config_path,
            storage_root=root,
        )
        runner.persist_runner_state()
        # Important: construction stays lazy. AgentManager.acquire_root() is
        # called only at the first request.
        return runner

    @classmethod
    def resume(
        cls,
        *,
        runner_id: str,
        base_dir: str | Path,
        runtime_config_path: str | Path | None = None,
        **removed: Any,
    ) -> "Runner":
        if removed:
            raise TypeError("Runner.resume 不接受旧运行时参数: " + ", ".join(sorted(removed)))
        resolved_base = Path(base_dir).expanduser().resolve()
        state = load_runner_state(build_layout(resolved_base, runner_id).manifest_path)
        raw_config = state.get("runner_config")
        if not isinstance(raw_config, dict) or not raw_config:
            raise ValueError("旧 Runner 状态不支持恢复: 缺少 runner_config 声明")
        runner = cls(
            permission_mode=str(state.get("permission_mode") or "default"),
            runner_config=RunnerConfig.from_dict(raw_config),
            runner_id=str(state.get("runner_id") or runner_id),
            base_dir=resolved_base,
            state=state,
            runtime_config_path=runtime_config_path,
        )
        runner.async_task_manager.recover()
        runner.graph_manager.recover()
        if runner.team_manager is not None:
            runner._team_stopping = True
            try:
                runner.team_manager.recover()
                runner._reconcile_team_inbox()
            finally:
                runner._team_stopping = False
            runner.wake_team()
        settle_status(runner.state, status="idle", reason="resumed")
        runner.persist_runner_state()
        return runner

    # -- Manager composition ------------------------------------------------------------

    def _bind_managed_agent(self, managed: ManagedAgent, cancel_event: threading.Event) -> None:
        instance = managed.instance
        if instance is None:
            raise RuntimeError("不能为已释放 agent 绑定运行时")
        context = RunnerContext(
            runner=self,
            agent_name=managed.agent_name,
            agent_id=managed.agent_id,
            agent_role=managed.role,
            agent=instance,
        )
        instance.runner_context = context
        # Prompt-facing AgentTool data must use the exact same availability
        # predicate as `/agents` and direct Runner dispatch.  Declarations
        # are copied by AgentRegistry, so updating this runtime projection
        # cannot mutate shared YAML or another Runner.
        if managed.is_root:
            visible_names = self._discover_available_agent_names()
        else:
            declared = getattr(instance, "_declared_agent_config", None)
            candidates = tuple(getattr(declared, "managed_agent_names", ()) or ())
            visible_names = tuple(name for name in candidates if self.is_agent_available(name))
        instance._runtime_managed_agent_refs = {
            name: {"agent_config_dir": str(self.config_context.agents_dir)}
            for name in visible_names
        }
        disabled_by_mode = dict(self.state.get("disabled_agent_names_by_mode") or {})
        instance._runtime_disabled_managed_agent_names = {
            str(name).strip()
            for name in disabled_by_mode.get(self.mode_id, [])
            if str(name).strip()
        }
        instance.bind_manager_infra(
            agent_id=managed.agent_id,
            event_queue=self.event_queue,
            async_task_manager=self.async_task_manager,
            runtime_workspace=self.runtime_workspace,
            runtime_base_dir=self.base_dir,
        )
        self.async_task_manager.register_agent(
            agent_name=managed.agent_name,
            agent_id=managed.agent_id,
            role=managed.role,
            metadata={"lifecycle": managed.lifecycle.value, "is_root": managed.is_root},
        )
        self.tool_manager.bind_agent(
            instance,
            agent_id=managed.agent_id,
            context=ToolExecutionContext(
                agent=instance,
                runner_context=context,
                runner=self,
                permission_mode=self.permission_mode,
                agent_mode=self.mode_id,
                cancel_event=cancel_event,
            ),
        )
        instance.set_log_file_path(self.layout.agents_dir / managed.agent_id / "log.txt")

    def _after_agent_step(self, managed: ManagedAgent, _step: Any) -> None:
        self.state["root_agent_name"] = self.root_agent_name
        self.state["root_agent_id"] = self.root_agent_id
        self.persist_runner_state()
        self._emit_manager_lifecycle({"scope": "agent", "event": "checkpointed", "agent_id": managed.agent_id})

    def _after_agent_release(self, managed: ManagedAgent) -> None:
        self.tool_manager.release_agent(managed.agent_id)

    def _reconcile_team_inbox(self) -> None:
        """Replay only messages absent from persisted agent TaskSteps."""

        if self.team_manager is None:
            return
        for snapshot in self.agent_manager.snapshot_store.list():
            self._after_agent_persist(snapshot)

    def _attachments_for(self, managed: ManagedAgent, _step_index: int) -> list[RuntimeAttachment]:
        events = self.event_queue.drain(managed.agent_id)
        inbox = self._team_initial_attachments(managed.agent_name)
        return [
            *self._pending_attachments.pop(managed.agent_id, []),
            *merge_runtime_attachments(runtime_events=events),
            *inbox,
        ]

    def _root(self) -> ManagedAgent:
        # The root config is detached in memory.  Passing it through the
        # standard Registry -> Manager construction flow preserves prompt and
        # tool rendering without making ``root`` editable Registry state.
        return self.agent_manager.acquire_root(self.root_config, role="root")

    def _require_capability(self, capability: Capability | str) -> None:
        resolved = Capability(capability)
        if not self.has_capability(resolved):
            raise PermissionError(
                f"RunnerConfig {self.mode_id!r} 未启用 capability {resolved.value!r}"
            )

    def _check_tool_policy(self, tool: Any, _args: dict[str, Any]) -> bool | dict[str, Any]:
        """Apply immutable RunnerConfig capability/tool policy before execution.

        Agent-level checks still run afterwards for per-request approval.  This
        layer is intentionally configuration-driven: it contains no mode ID
        branch and therefore applies identically to built-in and custom modes.
        """

        name = str(getattr(tool, "name", "") or "").strip()
        if not self.has_capability(Capability.TOOLS):
            return {"allowed": False, "reason": "当前 RunnerConfig 未启用 tools capability"}
        allowed = self.config.tool_policy.allowed_tools
        if allowed and name not in allowed:
            return {"allowed": False, "reason": f"RunnerConfig 不允许工具 {name!r}"}
        if name == "agent_tool" and not self.has_capability(Capability.AGENTS):
            return {"allowed": False, "reason": "当前 RunnerConfig 未启用 agents capability"}
        if name.startswith("graph_") and not self.has_capability(Capability.GRAPHS):
            return {"allowed": False, "reason": "当前 RunnerConfig 未启用 graphs capability"}
        if name.startswith("async_task") and not self.has_capability(Capability.ASYNC_TASKS):
            return {"allowed": False, "reason": "当前 RunnerConfig 未启用 async_tasks capability"}
        policy = self.config.tool_policy
        if (
            policy.read_only
            and not bool(getattr(tool, "is_read_only", False))
            and name not in policy.read_only_exceptions
        ):
            return {
                "allowed": False,
                "reason": f"RunnerConfig 的只读工具策略不允许工具 {name!r}",
            }
        return True

    # -- Request dispatch ---------------------------------------------------------------

    def stream(self, message: str, task_images: list[Any] | None = None) -> Iterator[dict[str, Any]]:
        if not self._request_lock.acquire(blocking=False):
            raise RunnerBusyError(f"runner {self.runner_id} is already processing a request")
        try:
            root = self._root()
        except Exception:
            # Registry validation/model construction happens lazily here.  A
            # failed acquisition must not leave the request gate permanently
            # held and make a corrected follow-up request appear busy.
            self._request_lock.release()
            raise
        self._cancel_event.clear()
        self._team_stopping = False
        self._stop_reason = None
        started_at = time.time()
        round_id = f"round_{uuid4().hex[:12]}"
        settle_status(
            self.state,
            status="running",
            reason="request_started",
            active_round={"round_id": round_id, "agent_id": root.agent_id, "started_at": started_at},
            at=started_at,
        )
        self.persist_runner_state()

        def iterate() -> Iterator[dict[str, Any]]:
            outcome, output = "failed", None
            try:
                yield from self._drain_team_updates()
                task = str(message)
                images = list(task_images or [])
                for continuation in range(self.config.continuation_policy.max_rounds):
                    for step in self.agent_manager.stream(
                        root, task, task_images=images,
                        attachments=self._team_initial_attachments("root"),
                    ):
                        outcome = str(step.round_outcome or "continue")
                        if outcome in {"submitted", "yielded", "failed"}:
                            output = step.output if step.output is not None else step.model_output
                        event = {
                            "kind": "action_step", "runner_id": self.runner_id, "mode_id": self.mode_id,
                            "permission_mode": self.permission_mode, "agent_name": root.agent_name,
                            "agent_id": root.agent_id, "agent_role": root.role, "step_num": step.step_num,
                            "action_step": step, "round_id": round_id,
                        }
                        self._publish(event)
                        yield event
                        yield from self._drain_team_updates()
                    # The transport owns only this stream. Keep it open while
                    # member calls are active so completion updates reach
                    # CLI/Web without a separate polling channel.
                    while self._active_team_calls() and not self._cancel_event.is_set():
                        yield from self._drain_team_updates()
                        time.sleep(0.02)
                    pending_root = [] if self.team_manager is None else self.team_manager.pending_messages("root")
                    if (
                        not pending_root or self._cancel_event.is_set()
                        or not self.config.continuation_policy.continue_on_pending_work
                    ):
                        break
                    if continuation + 1 == self.config.continuation_policy.max_rounds:
                        logger.warning("team_root_continuation_limit runner_id=%s pending=%d", self.runner_id, len(pending_root))
                        break
                    task = f"{len(pending_root)} new Team messages"
                    images = []
                reason = "interrupted" if root.status is AgentStatus.INTERRUPTED else "round_ended"
                settle_status(self.state, status="idle", reason=reason)
                yield from self._drain_team_updates()
                end = {
                    "kind": "round_end", "runner_id": self.runner_id, "mode_id": self.mode_id,
                    "permission_mode": self.permission_mode, "agent_name": root.agent_name,
                    "agent_id": root.agent_id, "agent_role": root.role, "round_id": round_id,
                    "outcome": outcome, "output": output, "reason": reason,
                }
                self._publish(end)
                yield end
            except AgentExecutionInterrupted as exc:
                settle_status(self.state, status="idle", reason="interrupted")
                event = {
                    "kind": "stream_cancelled", "runner_id": self.runner_id, "mode_id": self.mode_id,
                    "permission_mode": self.permission_mode, "agent_name": root.agent_name,
                    "agent_id": root.agent_id, "agent_role": root.role, "round_id": round_id,
                    "stop_reason": exc.reason,
                }
                self._publish(event)
                yield event
            except Exception:
                settle_status(self.state, status="failed", reason="request_failed")
                logger.exception("runner_request_failed runner_id=%s", self.runner_id)
                raise
            finally:
                self.persist_runner_state()
                self._request_lock.release()

        def close_unstarted() -> None:
            settle_status(self.state, status="idle", reason="stream_closed")
            self.persist_runner_state()
            self._request_lock.release()

        return _RunnerStream(iterate(), close_unstarted)

    def run(self, message: str, task_images: list[Any] | None = None) -> Any:
        result: Any = None
        for event in self.stream(message, task_images=task_images):
            if event.get("kind") == "round_end":
                result = event.get("output")
        return result

    def request_stop_stream(self, *, reason: str = "user_cancelled") -> None:
        self._team_stopping = True
        self._stop_reason = str(reason or "user_cancelled")
        self._cancel_event.set()
        self.agent_manager.interrupt(reason=self._stop_reason)
        logger.info("runner_stream_stop_requested runner_id=%s reason=%s", self.runner_id, self._stop_reason)

    def stop(self, *, reason: str = "runner_stopped") -> None:
        self.request_stop_stream(reason=reason)
        self.async_task_manager.stop(reason=reason)
        if self.team_manager is not None:
            for member in self.team_manager.snapshot()["members"]:
                if member["name"] != "root" and member.get("busy"):
                    self._settle_team_member_call(str(member["name"]))
        # AgentManager owns the running call.  Wait for its lock before
        # releasing the persistent instance and its durable session.
        for managed in list(self.agent_manager.live_agents.values()):
            if managed.status is AgentStatus.RUNNING:
                with managed._run_lock:
                    pass
        for managed in list(self.agent_manager.live_agents.values()):
            self.agent_manager.release(managed)
        settle_status(self.state, status="idle", reason=reason)
        self.state["last_stop"] = {"reason": reason, "at": time.time()}
        self.persist_runner_state()

    def close(self) -> None:
        self.stop(reason="runner_closed")
        self.graph_manager.release(wait=False)
        self.async_task_manager.release(wait=False)
        self.tool_manager.release(wait=False)

    # -- generic Manager capabilities ---------------------------------------------------

    def invoke_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Acquire and run a declaration through AgentManager, never directly."""

        self._require_capability(Capability.AGENTS)
        raw_ref = spec.get("agent_ref") or spec.get("agent_name")
        task = str(spec.get("task") or "").strip()
        if raw_ref is None or not task:
            raise ValueError("agent_ref 和 task 不能为空")
        agent_name = str(
            getattr(raw_ref, "name", "")
            or (raw_ref.get("name") if isinstance(raw_ref, dict) else raw_ref)
        ).strip()
        if not self.is_agent_available(agent_name):
            allowed = ", ".join(self._discover_available_agent_names()) or "(none)"
            raise PermissionError(
                f"当前 Runner 不允许调用 Agent ({allowed}); got {agent_name!r}"
            )
        execution = str(spec.get("execution") or "background").strip()
        owner_name = str(spec.get("owner_agent_name") or self.root_agent_name).strip()
        owner = self.agent_manager.get_by_name(owner_name)
        role = str(spec.get("agent_role") or self.config.member_binding.role).strip()
        lifecycle = str(spec.get("lifecycle") or self.config.member_binding.lifecycle).strip()
        binding: Any = raw_ref if not isinstance(raw_ref, str) else AgentBinding(agent_name, role, lifecycle)
        managed = self.agent_manager.acquire(
            binding, agent_name=agent_name or None, role=role, lifecycle=lifecycle,
            model=spec.get("model"), metadata={"parent_agent": owner.agent_name, **dict(spec.get("metadata") or {})},
        )
        run_args = {"task_images": list(spec.get("task_images") or []), "reset_session": not bool(spec.get("inherit_session"))}
        if execution == "sync":
            return {"status": "completed", "agent_id": managed.agent_id, "agent_name": managed.agent_name, "output": self.agent_manager.run(managed, task, **run_args)}
        if execution != "background":
            raise ValueError(f"未知 agent execution: {execution}")
        receipt = self.async_task_manager.launch(
            task_type="local_agent", owner_agent_id=owner.agent_id, owner_agent_name=owner.agent_name,
            description=task, runner=lambda _task, _path: self.agent_manager.run(managed, task, **run_args),
            summary_builder=lambda _result: f"agent {managed.agent_name} completed",
            metadata={"target_agent_name": managed.agent_name},
        )
        return {"status": "pending", "agent_id": managed.agent_id, "agent_name": managed.agent_name, **dict(receipt)}

    def launch_local_bash_async_task(self, *, owner_agent_name: str, command: str, cwd: str, timeout_seconds: float | None, max_observation_chars: int | None = None) -> dict[str, Any]:
        self._require_capability(Capability.ASYNC_TASKS)
        owner = self.agent_manager.get_by_name(owner_agent_name)
        return self.async_task_manager.launch_shell(
            owner_agent_id=owner.agent_id,
            owner_agent_name=owner.agent_name,
            command=command,
            cwd=cwd or self.base_dir,
            timeout_seconds=timeout_seconds,
            metadata={"max_observation_chars": max_observation_chars},
        )

    def launch_local_graph_async_task(self, *, owner_agent_name: str, graph_name: str, payload: dict[str, Any], config: dict[str, Any] | None = None, max_observation_chars: int | None = None) -> dict[str, Any]:
        self._require_capability(Capability.GRAPHS)
        self._require_capability(Capability.ASYNC_TASKS)
        owner = self.agent_manager.get_by_name(owner_agent_name)
        return self.async_task_manager.launch(
            task_type="local_graph", owner_agent_id=owner.agent_id, owner_agent_name=owner.agent_name,
            description=f"graph:{graph_name}",
            runner=lambda _task, _path: self.graph_manager.run(
                graph_name,
                payload,
                config,
                owner_agent_name=owner.agent_name,
            ),
            summary_builder=lambda result: f"graph {graph_name} {result.get('status', 'finished')}",
            metadata={"graph_name": graph_name, "max_observation_chars": max_observation_chars},
        )

    def list_async_tasks(self, *, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        return self.async_task_manager.list_tasks(statuses=statuses)

    def get_async_task(self, async_task_id: str) -> dict[str, Any] | None:
        return self.async_task_manager.get_task(async_task_id)

    # -- interaction, inspection and persistence --------------------------------------

    def set_ask_handler(self, handler: AskHandler | None) -> None:
        self._ask_handler = handler

    def ask_user(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"status": "error", "error": "没有可用的用户交互通道"} if self._ask_handler is None else dict(self._ask_handler(dict(request)))

    def describe_agent_sessions(self) -> dict[str, Any]:
        return {"runner_id": self.runner_id, "mode_id": self.mode_id, **self.agent_manager.describe_sessions()}

    def queue_agent_user_message(self, *, agent_name: str, message: str) -> dict[str, Any]:
        managed = self.agent_manager.get_by_name(agent_name)
        # A user message is a session-level input for a managed agent.  It is
        # queued rather than injected into a live instance, so it survives the
        # same acquire/step boundary as all other Runner-owned attachments.
        self._pending_attachments.setdefault(managed.agent_id, []).append({"attachment_type": "agent_user_message", "created_at": time.time(), "payload": {"from": "user", "agent_name": managed.agent_name, "text": str(message)}})
        return {"accepted": True, "agent_name": managed.agent_name, "delivery": "agent_attachment"}

    def interrupt_agent(self, *, agent_name: str, reason: str = "user_agent_interrupt") -> dict[str, Any]:
        managed = self.agent_manager.get_by_name(agent_name)
        return {"interrupted": bool(self.agent_manager.interrupt(managed, reason=reason)), "agent_name": managed.agent_name, "reason": reason}

    def plan_file_path(self) -> Path:
        """Return the sole formal plan artifact for this Runner.

        The path is intentionally not parameterized: workers must never gain
        a second formal plan location merely by choosing an agent name.
        """

        return self.layout.plans_dir / "root.md"

    def record_approved_plan_exit(self, *, agent_name: str, plan: str, plan_file: str) -> None:
        if self.mode_id != "plan":
            raise PermissionError("只能在 Plan Mode 记录计划审批")
        if Path(plan_file).resolve() != self.plan_file_path().resolve():
            raise PermissionError("审批计划必须来自当前 Runner 的 plans/root.md")
        payload = {"agent_name": agent_name, "plan": plan, "plan_file": plan_file}
        self.state["pending_plan_exit"] = dict(payload)
        self.persist_runner_state()
        self._pending_attachments.setdefault(self.root_agent_id, []).append({"attachment_type": "plan_mode_exit", "created_at": time.time(), "payload": payload})

    def pending_plan_exit(self) -> dict[str, Any] | None:
        """Read an approved exit request without changing it.

        The transport consumes this only after ``stream()`` has returned, so
        mode reconstruction never occurs inside a tool call or ``finally``.
        """

        value = self.state.get("pending_plan_exit")
        return dict(value) if isinstance(value, dict) else None

    def persist_runner_state(self) -> RunnerState:
        self.state = write_runner_state(self.layout.manifest_path, self.state)
        return self.state

    def register_stream_event_listener(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._listeners_lock:
            self._listeners.append(listener)
        def unregister() -> None:
            with self._listeners_lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return unregister

    def _publish(self, event: dict[str, Any]) -> None:
        with self._listeners_lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(dict(event))
            except Exception:
                logger.exception("runner stream listener failed")

    def _emit_manager_lifecycle(self, event: dict[str, Any] | str, **details: Any) -> None:
        payload = dict(event) if isinstance(event, dict) else {"event": event, **details}
        self._publish({"kind": "runner_lifecycle", "runner_id": self.runner_id, "mode_id": self.mode_id, "permission_mode": self.permission_mode, "agent_name": self.root_agent_name, "agent_id": self.root_agent_id, "agent_role": self.config.root_binding.role, "lifecycle_event": payload})


__all__ = ["Runner", "RunnerBusyError"]
