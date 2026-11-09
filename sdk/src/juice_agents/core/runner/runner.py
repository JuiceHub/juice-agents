"""Declarative Runner façade.

The Runner owns only request-scoped coordination.  Static definitions belong
to Registries; mutable objects, sessions, pools and checkpoints belong to
Managers.  No mode is allowed to inject an execution callback into this class.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import aclosing, contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterator
from uuid import uuid4
import weakref

from juice_agents.core.managers import AgentManager, AgentManagerCallbacks, AgentStatus, ManagedAgent
from juice_agents.core.agent.contracts import RoundResult
from juice_agents.core.managers.agents import JsonAgentSnapshotStore
from juice_agents.core.agent.attachments import (
    RuntimeAttachment, build_async_task_notification_attachment,
)
from juice_agents.core.agent.root_config import build_root_config
from juice_agents.core.agent.tools.runtime import ToolExecutionContext
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.config.model_catalog import DEFAULT_RUNTIME_CONFIG_PATH, normalize_runtime_model_effort
from juice_agents.core.managers import AsyncTaskManager, ModelManager, ToolManager, WorkflowRunManager
from .execution.async_bridge import run_async, on_runtime, iter_sync, iter_async
from .execution.async_io import blocking
from juice_agents.core.registry import AgentRegistry, WorkflowRegistry
from juice_agents.core.registry.agents.types import AgentConfig, normalize_agent_type

from .config import AgentBinding, Capability, RunnerConfig, mode_registry
from .errors import RunnerBusyError
from .execution.context import RunnerContext
from .persistence.store import blank_runner_state, build_layout, load_runner_state, write_runner_state
from .status import settle_status
from .types.definitions import PermissionMode, RunnerState, normalize_permission_mode
from .types.identity import new_runner_id
from .workspace import RuntimeWorkspace
from .project_rules import load_workspace_rules

logger = logging.getLogger(__name__)
AskHandler = Callable[[dict[str, Any]], dict[str, Any]]


class _WorkflowAgentDispatcher:
    """Weak, narrow bridge from workflow work to Runner-owned AgentManager.

    WorkflowRunManager needs to request ephemeral Agent work, but it must not
    own the Runner or a live Agent.  The bridge exposes only workflow-required
    methods and uses a weak reference, so a manager can never extend a closed
    Runner's lifetime.
    """

    def __init__(self, runner: "Runner") -> None:
        self._runner_ref = weakref.ref(runner)

    def _runner(self) -> "Runner":
        runner = self._runner_ref()
        if runner is None:
            raise RuntimeError("workflow Agent dispatch target has been released")
        return runner

    @property
    def runner_id(self) -> str:
        return self._runner().runner_id

    def resolve_agent(self, agent_name: str) -> AgentConfig:
        """按名字取已注册声明；沿用 ``/agents`` 的 mode/禁用可用性判断。"""

        runner = self._runner()
        if not runner.is_agent_available(agent_name):
            raise PermissionError(f"workflow 不能调用当前 mode 不可用的 agent: {agent_name!r}")
        return runner.agent_registry.load_config(agent_name)

    def tool_is_read_only(self, tool_name: str) -> bool:
        return self._runner().tool_is_read_only(tool_name)

    def invoke_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        """workflow 内部调用固定为 functional + ephemeral + accept。"""

        return self._runner().invoke_agent(
            {
                **spec,
                "execution": "sync",
                "lifecycle": "functional",
                "agent_role": "workflow",
                "ephemeral": True,
                "permission_mode": "accept",
            }
        )

    async def ainvoke_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        return await self._runner().ainvoke_agent({**spec, "execution": "sync",
            "lifecycle": "functional", "agent_role": "workflow", "ephemeral": True,
            "permission_mode": "accept"})


class _AgentInboxEventSink:
    """Async producer bridge; accepting an event means its inbox write succeeded."""

    def __init__(self, runner: "Runner") -> None:
        self._runner_ref = weakref.ref(runner)

    def enqueue(self, recipient_agent_id: str, event: dict[str, Any]) -> dict[str, Any]:
        runner = self._runner_ref()
        if runner is None:
            raise RuntimeError("notification target Runner has been released")
        runner._ensure_delivery_target(recipient_agent_id)
        runner.agent_manager.post(recipient_agent_id, build_async_task_notification_attachment(event))
        return dict(event)


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


@dataclass
class _RoundObserver:
    """一个请求的观察订阅；关闭它只解绑，不撤销已持久化的输入。"""

    events: asyncio.Queue
    round_id: str
    message_id: str
    agent_id: str
    cleanup: Callable[[], None]
    managed: ManagedAgent | None = None
    cancel_check: Callable[[], bool] | None = None
    task: asyncio.Task | None = None


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
        runtime_overrides: dict[str, Any] | None = None,
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
        self.runtime_config_path = Path(runtime_config_path or DEFAULT_RUNTIME_CONFIG_PATH).expanduser().resolve()
        self.config_context = ConfigurationContext.from_workspace(
            self.base_dir, project_config_path=self.runtime_config_path,
            overrides=runtime_overrides,
        )
        # Rules are one launch-scoped snapshot shared by root, children, Team
        # and Workflow Agents. A resumed Runner reads today's files afresh.
        self.workspace_rules = load_workspace_rules(self.base_dir)
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
        self._request_lock = threading.RLock()
        self._active_requests = 0
        self._functional_root_gate = None
        self._functional_calls: set[asyncio.Task] = set()
        self._state_lock = threading.RLock()
        self._activated = False
        self._closed = False
        self._cancel_event = threading.Event()
        self._stop_reason: str | None = None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._listeners_lock = threading.RLock()
        self._team_updates: deque[dict[str, Any]] = deque(maxlen=256)
        self._ask_handler: AskHandler | None = None
        # Process-local strategy configuration survives fresh Agent objects;
        # scorer instances must never enter the durable session JSON.
        self._reconfigured_agent_strategies: dict[str, Any] = {}

        self.tool_manager = ToolManager(
            state_dir=self.layout.tools_dir,
            policy_check=self._check_tool_policy,
        )
        self.async_task_manager = AsyncTaskManager(
            event_sink=_AgentInboxEventSink(self),
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
        # The workspace owns a portable root declaration. Mode-specific paths
        # and workflows belong only to the effective copy passed to Manager.
        self._root_declaration = self.agent_registry.load_config("root")
        self.root_config = build_root_config(
            mode_id=self.config.mode_id,
            plan_file=self.layout.plans_dir / "root.md",
            managed_agent_names=self._discover_available_agent_names(),
            declaration=self._root_declaration,
            runtime_config=self.config_context.read_merged_config(),
        )
        self.model_manager = ModelManager(usage_path=self.layout.agents_dir / "model_usage.jsonl")
        self.agent_manager = AgentManager(
            registry=self.agent_registry,
            model_manager=self.model_manager,
            runner_id=self.runner_id,
            layout=self.layout,
            runtime_config_path=self.runtime_config_path,
            permission_mode=self.permission_mode,
            workspace_dir=self.base_dir,
            callbacks=AgentManagerCallbacks(
                bind_runtime=self._bind_managed_agent,
                attachments_provider=self._attachments_for,
                after_step=self._after_agent_step,
                after_release=self._after_agent_release,
                on_event=self._on_agent_event,
                acknowledge=self._acknowledge_attachments,
            ),
        )
        self.workflow_manager = self._create_workflow_manager()
        self.team_manager = self._create_team_manager()
        logger.info("runner_initialized runner_id=%s mode_id=%s", self.runner_id, self.mode_id)

    def _create_workflow_manager(self) -> WorkflowRunManager:
        """构造与当前 RunnerConfig 绑定的 WorkflowRunManager（init 与 reconfigure 共用）。"""

        return WorkflowRunManager(
            runner_root=self.layout.root_dir,
            registry=WorkflowRegistry(config_context=self.config_context),
            config_context=self.config_context,
            runner_id=self.runner_id,
            agent_dispatcher=_WorkflowAgentDispatcher(self),
            owner_agent_name="root",
            lifecycle_callback=self._emit_manager_lifecycle,
        )

    def _create_team_manager(self) -> Any | None:
        """Bind mutable Team state only when the declarative config enables it."""

        if self.config.team is None:
            return None
        from juice_agents.core.managers.team import TeamManager

        run_id = str(self.state.get("team_run_id") or uuid4().hex)
        self.state["team_run_id"] = run_id
        manager = TeamManager(
            self.layout.root_dir / "team" / self.team_name / run_id,
            member_names=tuple(sorted(self._team_member_names or ())),
            team_name=self.team_name,
            team_run_id=run_id,
            on_change=self._on_team_change,
        )
        from functools import partial
        from juice_agents.core.registry.teams import TeamRegistry

        root_ref = weakref.WeakMethod(self._root)

        def resolve_root():
            resolve = root_ref()
            if resolve is None:
                raise RuntimeError("Team root host has been released")
            return resolve()

        manager.bind_runtime(
            agents=self.agent_manager, root_resolver=resolve_root,
            member_config_loader=partial(TeamRegistry(config_context=self.config_context).load_member_config, self.team_name),
            auto_dispatch=self.config.team.auto_dispatch,
        )
        return manager

    def _on_team_change(self, change: dict[str, Any]) -> None:
        event = {
            "kind": "team_update", "runner_id": self.runner_id,
            "agent_id": self.root_agent_id, "agent_name": self.root_agent_name,
            "agent_role": "root", "round_id": self.active_round_id, "step_num": None,
            "event_id": f"event_{uuid4().hex}", "timestamp": time.time(),
            "data": {"team_name": self.team_name, **change},
        }
        with self._listeners_lock:
            self._team_updates.append(event)
        self._publish(event)

    def _drain_team_updates(self) -> list[dict[str, Any]]:
        with self._listeners_lock:
            updates = list(self._team_updates)
            self._team_updates.clear()
        return updates

    def _active_team_calls(self) -> bool:
        return self.team_manager is not None and self.team_manager.has_active_calls()

    def wake_team(self) -> None:
        if self.team_manager is not None:
            self.team_manager.wake()

    def restart_team_member(self, name: str) -> dict[str, Any]:
        if self.team_manager is None:
            raise PermissionError("Team mode is not active")
        return self.team_manager.restart_runtime_member(name)

    def close_team_member(self, name: str) -> dict[str, Any]:
        if self.team_manager is None:
            raise PermissionError("Team mode is not active")
        return self.team_manager.close_runtime_member(name)

    def finish_team(self) -> dict[str, Any]:
        if self.team_manager is None:
            raise PermissionError("Team mode is not active")
        return self.team_manager.finish_runtime()

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
        previous_source = teams.get_source(self.team_name)
        declaration = teams.add_member(self.team_name, name, agent_name=agent_name, config=config)
        try:
            member = manager.create_member(name, declaration.name)
        except Exception:
            teams.remove_member(self.team_name, name)
            if previous_source == "builtin":
                # The failed first edit must restore the read-only default,
                # including removing its newly materialized local manifest.
                teams.delete(self.team_name)
            raise
        self._team_member_names = frozenset({*(self._team_member_names or ()), name})
        self._refresh_root_config()
        return member

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
        from juice_agents.core.team.builtin.configs import DEFAULT_TEAM_NAME

        selected = str(self.state.get("team_name") or DEFAULT_TEAM_NAME).strip()
        teams = TeamRegistry(config_context=self.config_context)
        # Default and saved Teams share Registry resolution. Looking up the
        # built-in default must not persist a manifest or worker declarations.
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
        if self.team_manager is not None and self.team_manager.has_pending_work():
            return "存在未完成的 Team 工作"
        return None

    @contextmanager
    def configuration_boundary(self) -> Iterator[None]:
        """Reserve an idle configuration boundary, including external writes.

        Gateways can validate, persist a project patch and reload under this
        same reentrant lock. A new request cannot start between those steps.
        """

        if not self._request_lock.acquire(blocking=False):
            raise RunnerBusyError(f"runner {self.runner_id} 正忙，不能切换配置")
        try:
            if self._active_requests:
                raise RunnerBusyError(f"runner {self.runner_id} 正忙，不能切换配置")
            blocked = self.mode_switch_block_reason()
            if blocked:
                raise RunnerBusyError(f"runner {self.runner_id} 正忙，不能切换模式：{blocked}")
            yield
        finally:
            self._request_lock.release()

    def reconfigure(
        self,
        *,
        mode_id: str | RunnerConfig | None = None,
        permission_mode: PermissionMode | str | None = None,
        plan_return_mode: str | None = None,
        runtime_overrides: dict[str, Any] | None = None,
        reload_config: bool = False,
    ) -> None:
        """Commit choices; explicitly reload files with ``reload_config=True``.

        Fresh clients use today's providers while Runner identity, session
        history, Team board and process-local exploration choices survive.
        """

        with self.configuration_boundary():
            self._reconfigure_at_idle(
                mode_id=mode_id, permission_mode=permission_mode,
                plan_return_mode=plan_return_mode, runtime_overrides=runtime_overrides,
                reload_config=reload_config,
            )

    def _reconfigure_at_idle(
        self,
        *,
        mode_id: str | RunnerConfig | None,
        permission_mode: PermissionMode | str | None,
        plan_return_mode: str | None,
        runtime_overrides: dict[str, Any] | None,
        reload_config: bool,
    ) -> None:
        """Apply a new declarative mode at an explicit idle boundary.

        The Runner id and manager snapshot directory stay stable.  Releasing
        the root first writes its session snapshot; the next request acquires
        a fresh root with the latest Registry declarations and effective
        in-memory root configuration.
        """

        target = mode_registry.resolve(mode_id or self.config)
        target_permission = normalize_permission_mode(permission_mode or self.permission_mode)
        runtime_changed = reload_config or (
            runtime_overrides is not None and runtime_overrides != self.config_context.overrides
        )
        mode_unchanged = target == self.config and target_permission == self.permission_mode
        if mode_unchanged and not runtime_changed:
            return
        blocked = self.mode_switch_block_reason()
        if blocked:
            raise RunnerBusyError(f"runner {self.runner_id} 正忙，不能切换模式：{blocked}")

        # Validate before releasing anything, including simultaneous model /
        # protocol changes. A bad Team manifest leaves the old session usable.
        target_team_members = self._load_team_member_names(mode_id=target.mode_id)

        if runtime_changed:
            # Validate static choices before touching live Agents. Provider
            # construction remains lazy, so no API key or network is needed.
            if runtime_overrides is not None and not isinstance(runtime_overrides, dict):
                raise TypeError("runtime_overrides 必须为映射")
            candidate_context = replace(self.config_context, overrides=deepcopy(
                self.config_context.overrides if runtime_overrides is None else runtime_overrides
            ))
            runtime = candidate_context.read_merged_config().get("runtime", {})
            if not isinstance(runtime, dict):
                raise ValueError("runtime 必须为映射")
            normalize_agent_type(runtime.get("agent_type"))
            normalize_runtime_model_effort(runtime.get("model_effort"))
            candidate_registry = AgentRegistry(config_context=candidate_context)
            candidate_registry.load_config("root")
            # Fresh objects consume new model/protocol choices, while their
            # session snapshots and Team board remain owned by the same Runner.
            # Do not mutate a shared frozen context or persistent declarations.
            for managed in list(self.agent_manager.live_agents.values()):
                self._remember_agent_strategy(managed, candidate_context, reload_config=reload_config)
                self.agent_manager.release(managed)
            self.config_context = candidate_context
            self.agent_registry = candidate_registry
            self.agent_manager.registry = self.agent_registry
            if mode_unchanged:
                self._refresh_root_config()
                self.workflow_manager = self._create_workflow_manager()
                self.persist_runner_state()
                logger.info("runner_runtime_reconfigured runner_id=%s", self.runner_id)
                return

        try:
            root = self.agent_manager.get(self.root_agent_id)
        except KeyError:
            root = None
        if root is not None:
            self._remember_agent_strategy(root, self.config_context)
            self.agent_manager.release(root)

        # Managers holding worker pools are recreated only after the activity
        # check above.  Their durable stores remain in the same Runner layout.
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
        self._refresh_root_config()
        self.async_task_manager = AsyncTaskManager(
            event_sink=_AgentInboxEventSink(self),
            state_dir=self.layout.async_tasks_dir,
            scope_id=self.runner_id,
            scope_kind="runner",
            scope_meta={"runner_id": self.runner_id, "mode_id": target.mode_id},
            runtime_workspace=self.runtime_workspace,
            default_base_dir=self.base_dir,
            max_workers=target.concurrency_limit,
        )
        self.workflow_manager = self._create_workflow_manager()
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
        runtime_overrides: dict[str, Any] | None = None,
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
            context = ConfigurationContext.from_workspace(
                resolved_base, project_config_path=runtime_config_path,
                overrides=runtime_overrides,
            )
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
            runtime_overrides=runtime_overrides,
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
        runtime_overrides: dict[str, Any] | None = None,
        **removed: Any,
    ) -> "Runner":
        if removed:
            raise TypeError("Runner.resume 不接受旧运行时参数: " + ", ".join(sorted(removed)))
        resolved_base = Path(base_dir).expanduser().resolve()
        state = load_runner_state(build_layout(resolved_base, runner_id).manifest_path)
        # Validate the complete session before constructors/recovery hooks can
        # write anything. Old files remain available for manual inspection.
        for snapshot in JsonAgentSnapshotStore(build_layout(resolved_base, runner_id).agents_dir).list():
            AgentManager.deserialize_snapshot(snapshot)
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
            runtime_overrides=runtime_overrides,
        )
        runner.async_task_manager.recover()
        if runner.team_manager is not None:
            with runner.team_manager.suspend_dispatch():
                runner.team_manager.recover(preserve_claims=True)
        settle_status(runner.state, status="idle", reason="resumed")
        runner.persist_runner_state()
        return runner

    # -- Manager composition ------------------------------------------------------------

    def _remember_agent_strategy(
        self, managed: ManagedAgent, context: ConfigurationContext, *, reload_config: bool = False,
    ) -> None:
        """Retain choices in memory and invalidate only catalog-owned scorers."""

        strategy = getattr(managed.instance, "exploration_strategy", None)
        if strategy is None:
            return
        from juice_agents.core.models.reward_models import GenerativeRewardModel

        scorer = getattr(strategy, "reward_model", None)
        if reload_config and isinstance(scorer, GenerativeRewardModel):
            from .execution.async_bridge import run_async

            run_async(scorer.reload_config(context), owner=scorer)
        self._reconfigured_agent_strategies[managed.agent_id] = strategy

    def _bind_managed_agent(self, managed: ManagedAgent, cancel_event: threading.Event) -> None:
        instance = managed.instance
        if instance is None:
            raise RuntimeError("不能为已释放 agent 绑定运行时")
        strategy = self._reconfigured_agent_strategies.pop(managed.agent_id, None)
        if strategy is not None:
            instance.set_exploration_strategy(strategy)
        context = RunnerContext(
            runner=self,
            agent_name=managed.agent_name,
            agent_id=managed.agent_id,
            agent_role=managed.role,
            agent=instance,
            cancellation=cancel_event,
        )
        instance.runner_context = context
        instance.workspace_rules = self.workspace_rules.prompt
        # Prompt-facing AgentTool data must use the exact same availability
        # predicate as `/agents` and direct Runner dispatch.  Declarations
        # are copied by AgentRegistry, so updating this runtime projection
        # cannot mutate shared YAML or another Runner.
        if managed.is_root:
            # Evolution persists the portable declaration, never this Runner's
            # effective Plan/Team copy. Keep effective tool metadata produced
            # by Registry intact for prompt/executor construction.
            instance._declared_agent_config = AgentConfig.from_dict(self._root_declaration)
            visible_names = self._discover_available_agent_names()
        else:
            if managed.role == "teammate":
                from juice_agents.core.registry.teams import TeamRegistry

                teams = TeamRegistry(config_context=self.config_context)
                member_name = str(managed.metadata["team_member"])
                manifest = teams.get_manifest(self.team_name)
                shared_name = manifest.shared_agent_name(member_name)
                declaration_dir = (
                    self.config_context.agents_dir
                    if shared_name is not None
                    else teams.member_config_dir(self.team_name)
                )
                # Keep the original portable declaration, since the Team turn
                # adds coordination instructions/tools only to its runtime copy.
                instance._declared_agent_config = teams.load_member_config(self.team_name, member_name)
                instance._declared_agent_config_dir = str(declaration_dir)
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
            async_task_manager=self.async_task_manager,
            runtime_workspace=self.runtime_workspace,
            runtime_base_dir=self.base_dir,
        )
        self.tool_manager.bind_agent(
            instance,
            agent_id=managed.agent_id,
            context=ToolExecutionContext(
                agent=instance,
                runner_context=context,
                runner=self,
                permission_mode=managed.permission_mode or self.permission_mode,
                agent_mode=self.mode_id,
                cancel_event=cancel_event,
            ),
        )
        # ManagedAgent 级审批覆盖（workflow 内部 agent 为 accept）交给 PermissionEngine 读取。
        instance.permission_mode_override = managed.permission_mode
        # ephemeral agent 不写文件日志；运行过程只经 logger 输出。
        instance.set_log_file_path(None if managed.ephemeral else self.layout.agents_dir / managed.agent_id / "log.txt")
        # Render after replacing the restored ContextTree and binding the
        # current role/tools. This replaces historical rules, never appends.
        refresh_prompt = getattr(instance, "refresh_system_prompt", None)
        if callable(refresh_prompt):
            refresh_prompt()

    def _after_agent_step(self, managed: ManagedAgent, _step: Any) -> None:
        self.state["root_agent_name"] = self.root_agent_name
        self.state["root_agent_id"] = self.root_agent_id
        self.persist_runner_state()

    def _after_agent_release(self, managed: ManagedAgent) -> None:
        self.tool_manager.release_agent(managed.agent_id)

    def _attachments_for(self, managed: ManagedAgent, _step_index: int) -> list[RuntimeAttachment]:
        # All producers use AgentManager.post(). The callback remains the
        # extension seam for per-step dynamic prompt state, never a mailbox.
        return []

    def _acknowledge_attachments(self, managed: ManagedAgent, attachments: list[RuntimeAttachment]) -> None:
        """Confirm business delivery only after the context/checkpoint commit."""

        team_ids: list[str] = []
        notification_ids: set[str] = set()
        for attachment in attachments:
            payload = dict(attachment.get("payload") or {})
            message_id = str(attachment.get("message_id") or payload.get("message_id") or "")
            if attachment.get("attachment_type") == "inbox_message" and message_id:
                team_ids.append(message_id)
            elif attachment.get("attachment_type") == "async_task_notification" and message_id:
                notification_ids.add(message_id)
        if team_ids and self.team_manager is not None:
            self.team_manager.acknowledge(managed, team_ids)
        if notification_ids:
            self.async_task_manager.mark_notifications_delivered(notification_ids)

    def _on_agent_event(self, managed: ManagedAgent, payload: dict[str, Any]) -> dict[str, Any]:
        """Publish each scheduled event once to request and ambient listeners."""

        event = {
            "runner_id": self.runner_id, "mode_id": self.mode_id,
            "permission_mode": self.permission_mode, "agent_id": managed.agent_id,
            "agent_name": managed.agent_name, "agent_role": managed.role,
            **payload,
        }
        event.setdefault("event_id", f"evt_{uuid4().hex}")
        kind = event.get("kind")
        if managed.is_root and kind in {"round_start", "round_end"}:
            with self._state_lock:
                if kind == "round_start":
                    settle_status(self.state, status="running", reason="round_started", active_round={
                        "round_id": event["round_id"], "agent_id": managed.agent_id,
                        "started_at": event.get("started_at", time.time()),
                    })
                elif dict(self.state.get("active_round") or {}).get("round_id") == event.get("round_id"):
                    settle_status(self.state, status="idle", reason=str(event.get("data", {}).get("result", {}).get("reason") or "round_ended"))
                try:
                    self.persist_runner_state()
                except Exception:
                    # The authoritative Agent checkpoint is already saved.
                    # Keep its real result observable if this summary fails.
                    logger.exception("runner_event_state_save_failed runner_id=%s", self.runner_id)
        self._publish(event)
        if self.team_manager is not None:
            self.team_manager.on_agent_event(managed, event)
        return event

    def _ensure_delivery_target(self, agent_id: str) -> ManagedAgent:
        """Restore a persisted identity before replay, without activating it."""

        try:
            managed = self.agent_manager.get(agent_id)
            if managed.is_live:
                return managed
        except KeyError:
            pass
        if agent_id == self.root_agent_id:
            return self._root()
        snapshot = self.agent_manager.snapshot_store.load(agent_id)
        if snapshot is None or snapshot.get("lifecycle") != "persistent":
            raise ValueError(f"notification target is unavailable: {agent_id}")
        metadata = dict(snapshot.get("metadata") or {})
        if metadata.get("team_member"):
            managed = self.team_manager.acquire_member(str(metadata["team_member"]))
            if managed.agent_id != agent_id:
                raise ValueError(f"notification target generation changed: {agent_id}")
            return managed
        return self.agent_manager.acquire(
            str(snapshot["agent_name"]), agent_name=str(snapshot["agent_name"]),
            agent_id=agent_id, role=str(snapshot.get("role") or "member"),
            lifecycle="persistent", metadata=metadata,
        )

    def _refresh_root_config(self, declaration: AgentConfig | None = None) -> None:
        """Read shared root data and apply the current mode only in memory."""

        self._root_declaration = declaration or self.agent_registry.load_config("root")
        self.root_config = build_root_config(
            mode_id=self.mode_id,
            plan_file=self.plan_file_path(),
            managed_agent_names=self._discover_available_agent_names(),
            declaration=self._root_declaration,
            runtime_config=self.config_context.read_merged_config(),
        )

    def _root(self) -> ManagedAgent:
        # A live root retains its session and reconciles tool additions on the
        # next step. A fresh acquire must observe persisted declaration edits.
        try:
            current = self.agent_manager.get(self.root_agent_id)
        except KeyError:
            current = None
        if current is None or not current.is_live:
            declaration = self.agent_registry.load_config("root")
            if declaration != self._root_declaration:
                self._refresh_root_config(declaration)
            # If YAML did not change, retain intentional in-memory overrides
            # (for example an embedded host selecting ReAct versus CodeAct).
        return self.agent_manager.acquire_root(self.root_config, role="root", lifecycle=self.config.root_binding.lifecycle)

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
        if name.startswith("workflow_") and not self.has_capability(Capability.WORKFLOWS):
            return {"allowed": False, "reason": "当前 RunnerConfig 未启用 workflows capability"}
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

    def activate(self) -> None:
        """Enable autonomous scheduling after event and Ask bindings are ready."""

        if self._closed:
            raise RuntimeError("Runner is closed")
        # Cold empty sessions remain key-free: constructing a model is only
        # necessary when a saved identity or incoming message needs one.
        # Restore persisted identities before handing scheduling authority to
        # AgentManager. Invalid targets stay visible in producer/inbox records.
        for snapshot in self.agent_manager.snapshot_store.list():
            if snapshot.get("lifecycle") != "persistent":
                continue
            try:
                self._ensure_delivery_target(str(snapshot["agent_id"]))
            except Exception:
                logger.exception("runner_restore_target_failed runner_id=%s agent_id=%s", self.runner_id, snapshot.get("agent_id"))
        self.wake_team()
        self.agent_manager.activate()
        self._activated = True
        logger.info("runner_activated runner_id=%s", self.runner_id)

    def post(self, agent_id: str, attachment: RuntimeAttachment) -> dict[str, Any]:
        """Durably accept an attachment; activation is explicit for background hosts."""

        self._ensure_delivery_target(agent_id)
        return self.agent_manager.post(agent_id, attachment)

    async def _open_stream(self, message: str, task_images: list[Any], cancel_check=None, *,
                           cancel_accepted: bool = False) -> _RoundObserver:
        events = asyncio.Queue()
        loop = asyncio.get_running_loop()
        released = False
        unsubscribe = lambda: None

        def cleanup() -> None:
            nonlocal released
            with self._request_lock:
                if released:
                    return
                released = True
                self._active_requests -= 1
            unsubscribe()

        def prepare():
            nonlocal unsubscribe
            with self._request_lock:
                if self._closed or self.agent_manager._closed:
                    raise RuntimeError("Runner is stopped or closed")
                self._active_requests += 1
            unsubscribe = self.subscribe(lambda event: loop.call_soon_threadsafe(events.put_nowait, event))
            for update in self._drain_team_updates():
                loop.call_soon_threadsafe(events.put_nowait, update)
            try:
                if self.config.root_binding.lifecycle == "functional":
                    return _RoundObserver(events, f"round_{uuid4().hex}", "", self.root_agent_id, cleanup,
                                          cancel_check=cancel_check)
                with self._request_lock:
                    root = self._root()
                    self._stop_reason = None
                    self._cancel_event.clear()
                    receipt = self.agent_manager.post(root.agent_id, {
                        "attachment_type": "user_message", "message_id": f"msg_{uuid4().hex}",
                        "created_at": time.time(), "payload": {"text": message, "task_images": task_images},
                    })
                    if cancel_check is not None and cancel_check():
                        self.agent_manager.cancel_round(receipt["round_id"])
                    self.activate()
                return _RoundObserver(events, str(receipt["round_id"]), str(receipt["message_id"]),
                                      root.agent_id, cleanup, root, cancel_check)
            except BaseException:
                cleanup()
                raise

        async def discard(observer):
            try:
                if cancel_accepted:
                    await self._cancel_observer(observer)
            finally:
                observer.cleanup()

        observer = await blocking(prepare, on_discard=discard)
        if self.config.root_binding.lifecycle == "functional":
            async def execute():
                if self._functional_root_gate is None:
                    self._functional_root_gate = asyncio.Lock()
                try:
                    async with self._functional_root_gate:
                        managed = await blocking(self._root)
                        observer.managed = managed
                        if cancel_check is not None and await blocking(cancel_check):
                            managed.external_cancel = threading.Event()
                            managed.external_cancel.set()
                        await self.agent_manager.arun(managed, message, task_images=task_images,
                                                      round_id=observer.round_id)
                except Exception as exc:
                    await events.put(exc)

            task = asyncio.create_task(execute(), name=f"functional-{self.runner_id}")
            observer.task = task
            self._functional_calls.add(task)
            task.add_done_callback(self._functional_calls.discard)
        return observer

    async def _cancel_observer(self, observer: _RoundObserver) -> None:
        """取消本次调用并等已启动的执行收尾；排队输入只需持久化意图。"""
        if observer.task is not None:
            observer.task.cancel()
            await asyncio.gather(observer.task, return_exceptions=True)
            return
        cancelled = await blocking(self.cancel_round, observer.round_id, reason="caller_cancelled")
        managed = observer.managed
        while cancelled and managed is not None:
            if await blocking(self.agent_manager.round_result, managed.agent_id, observer.round_id) is not None:
                return
            if managed._execution_error is not None:
                raise managed._execution_error
            await asyncio.sleep(0.01)

    async def _observe_round(self, observer: _RoundObserver):
        try:
            while True:
                if observer.cancel_check is not None and await blocking(observer.cancel_check):
                    await blocking(self.cancel_round, observer.round_id)
                try:
                    event = await asyncio.wait_for(observer.events.get(), 0.05)
                except TimeoutError:
                    if observer.managed is not None and observer.managed._execution_error is not None:
                        raise observer.managed._execution_error
                    continue
                if isinstance(event, BaseException):
                    raise event
                if event.get("kind") == "message_deferred" and event.get("data", {}).get("message_id") == observer.message_id:
                    observer.round_id = str(event["round_id"])
                    continue
                if event.get("kind") == "team_update":
                    yield event
                    continue
                if event.get("agent_id") != observer.agent_id or event.get("round_id") != observer.round_id:
                    continue
                yield event
                if event.get("kind") == "round_end":
                    return
        finally:
            observer.cleanup()

    def stream(self, message: str, task_images: list[Any] | None = None, *, cancel_check=None):
        observer = run_async(self._open_stream(str(message), list(task_images or []), cancel_check))
        return _RunnerStream(iter_sync(self._observe_round(observer)), observer.cleanup)

    async def astream(self, message: str, task_images: list[Any] | None = None, *, cancel_check=None):
        observer = await on_runtime(self._open_stream(str(message), list(task_images or []), cancel_check))
        async with aclosing(iter_async(self._observe_round(observer))) as events:
            async for event in events:
                yield event

    async def arun_round(self, message: str, task_images: list[Any] | None = None, *, cancel_check=None) -> RoundResult:
        async def execute():
            observer = await self._open_stream(str(message), list(task_images or []), cancel_check,
                                               cancel_accepted=True)
            try:
                async with aclosing(self._observe_round(observer)) as events:
                    async for event in events:
                        if event["kind"] == "round_end":
                            return RoundResult.from_dict(event["data"]["result"])
            except asyncio.CancelledError:
                await self._cancel_observer(observer)
                raise
            raise RuntimeError("round ended without a committed result")
        return await on_runtime(execute())

    async def arun(self, message: str, task_images: list[Any] | None = None) -> Any:
        return (await self.arun_round(message, task_images)).unwrap()

    @classmethod
    async def acreate(cls, **kwargs: Any):
        return await blocking(cls.create, **kwargs)

    @classmethod
    async def aresume(cls, **kwargs: Any):
        return await blocking(cls.resume, **kwargs)

    async def aactivate(self) -> None:
        await blocking(self.activate)

    async def apost(self, agent_id: str, attachment: RuntimeAttachment) -> dict[str, Any]:
        return await blocking(self.post, agent_id, attachment)

    async def aclose(self) -> None:
        await on_runtime(self._aclose())

    async def _aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.team_manager is not None:
            await self.team_manager.aclose()
        await blocking(self.stop, reason="runner_closed")
        await asyncio.gather(*tuple(self._functional_calls), return_exceptions=True)
        await self.async_task_manager.aclose(wait=True, timeout_seconds=None)
        await self.tool_manager.aclose(wait=True)
        await self.model_manager.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    def run_round(
        self, message: str, task_images: list[Any] | None = None, *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> RoundResult:
        """Wait for the committed outcome, also used by transport request handlers."""
        stream = self.stream(message, task_images=task_images, cancel_check=cancel_check)
        try:
            for event in stream:
                if event["kind"] == "round_end":
                    return RoundResult.from_dict(event["data"]["result"])
        finally:
            stream.close()
        raise RuntimeError("round ended without a committed result")

    def run(self, message: str, task_images: list[Any] | None = None) -> Any:
        return self.run_round(message, task_images=task_images).unwrap()

    @property
    def active_round_id(self) -> str | None:
        value = dict(self.state.get("active_round") or {}).get("round_id")
        return str(value) if value else None

    def cancel_round(self, round_id: str, *, reason: str = "user_cancelled") -> bool:
        """Persist cancellation for this round; later messages remain runnable."""

        cancelled = bool(self.agent_manager.cancel_round(round_id, reason=reason))
        if cancelled:
            logger.info("runner_round_cancelled runner_id=%s round_id=%s reason=%s", self.runner_id, round_id, reason)
        return cancelled

    def request_stop_stream(self, *, reason: str = "user_cancelled") -> None:
        round_id = self.active_round_id
        if round_id:
            self.cancel_round(round_id, reason=reason)

    def stop(self, *, reason: str = "runner_stopped") -> None:
        """Close scheduling and resources; this is separate from round cancellation."""

        if self.team_manager is not None:
            self.team_manager.stopping = True
        self._stop_reason = reason
        self._cancel_event.set()
        self.agent_manager.close(wait=True)
        self.async_task_manager.stop(reason=reason)
        settle_status(self.state, status="idle", reason=reason)
        self.state["last_stop"] = {"reason": reason, "at": time.time()}
        self.persist_runner_state()
        self._activated = False

    def close(self) -> None:
        run_async(self._aclose())

    # -- generic Manager capabilities ---------------------------------------------------

    def invoke_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        return run_async(self._ainvoke_agent(spec))

    async def ainvoke_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        return await on_runtime(self._ainvoke_agent(spec))

    async def _ainvoke_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        """调用 agent 的唯一入口；构造、运行、释放全部交给 AgentManager。

        - ``agent_ref`` 为名字：按 ``/agents`` 同一套可用性判断（mode、禁用列表），
          functional / persistent 均可。
        - ``agent_ref`` 为 ``AgentConfig``：调用方已组装并校验声明，不按名字查可用性；
          临时声明没有稳定身份，只允许 functional。
        - ``ephemeral=True``：不写 snapshot / 日志（要求 functional）。
        - ``permission_mode``：覆盖本次 agent 的审批模式；``cancel_event``：调用方取消信号。
        - 未指定 ``model`` 时继承发起者（owner）的模型。
        """

        def prepare():
            self._require_capability(Capability.AGENTS)
            raw_ref = spec.get("agent_ref") or spec.get("agent_name")
            task = str(spec.get("task") or "").strip()
            if raw_ref is None or not task:
                raise ValueError("agent_ref 和 task 不能为空")
            is_inline = isinstance(raw_ref, AgentConfig)
            agent_name = str(
                getattr(raw_ref, "name", "")
                or (raw_ref.get("name") if isinstance(raw_ref, dict) else raw_ref)
            ).strip()
            if not is_inline and not self.is_agent_available(agent_name):
                allowed = ", ".join(self._discover_available_agent_names()) or "(none)"
                raise PermissionError(
                    f"当前 Runner 不允许调用 Agent ({allowed}); got {agent_name!r}"
                )
            execution = str(spec.get("execution") or "background").strip()
            owner_name = str(spec.get("owner_agent_name") or self.root_agent_name).strip()
            owner = self.agent_manager.get_by_name(owner_name)
            if execution == "background" and owner.lifecycle.value == "functional":
                raise PermissionError("functional Agent cannot launch background work; use a synchronous call")
            role = str(spec.get("agent_role") or self.config.member_binding.role).strip()
            lifecycle = str(spec.get("lifecycle") or ("functional" if is_inline else self.config.member_binding.lifecycle)).strip()
            if is_inline and lifecycle != "functional":
                raise ValueError("临时 AgentConfig 只支持 functional 生命周期")
            binding: Any = raw_ref if not isinstance(raw_ref, str) else AgentBinding(agent_name, role, lifecycle)
            model = spec.get("model")
            if model is None and owner.instance is not None:
                model = getattr(owner.instance, "model", None)
            managed = self.agent_manager.acquire(
                binding, agent_name=agent_name or None, role=role, lifecycle=lifecycle,
                model=model, metadata={"parent_agent": owner.agent_name,
                    **({"cancellation_round_id": owner.metadata.get("cancellation_round_id")
                        or owner.round_checkpoint.get("round_id")} if execution == "sync" else {}),
                    **dict(spec.get("metadata") or {})},
                ephemeral=bool(spec.get("ephemeral")),
                permission_mode=str(spec.get("permission_mode") or ""),
                external_cancel=(spec.get("cancel_event") or owner.cancel_event) if lifecycle == "functional" and execution == "sync" else None,
            )
            run_args = {"task_images": list(spec.get("task_images") or []), "reset_session": not bool(spec.get("inherit_session"))}
            return managed, task, owner, execution, run_args

        managed, task, owner, execution, run_args = await blocking(prepare)
        async def execute(cancel_event: threading.Event | None = None) -> Any:
            if managed.lifecycle.value == "persistent":
                return await self._ainvoke_persistent_round(managed, task, task_images=run_args["task_images"], cancel_event=cancel_event)
            if cancel_event is not None:
                managed.external_cancel = cancel_event
            return await self.agent_manager.arun(managed, task, **run_args)

        if execution == "sync":
            result = await execute(spec.get("cancel_event") or owner.cancel_event)
            output = result.unwrap()
            return {"status": result.status, "agent_id": managed.agent_id, "agent_name": managed.agent_name,
                    "output": output, "result": result.to_dict()}
        if execution != "background":
            raise ValueError(f"未知 agent execution: {execution}")
        async def background(async_task, _path):
            return (await execute(self.async_task_manager.cancel_event_for(str(async_task["async_task_id"])))).unwrap()

        receipt = await self.async_task_manager.alaunch(
            task_type="local_agent", owner_agent_id=owner.agent_id, owner_agent_name=owner.agent_name,
            description=task,
            runner=background,
            summary_builder=lambda _result: f"agent {managed.agent_name} completed",
            metadata={"target_agent_name": managed.agent_name},
        )
        return {"status": "pending", "agent_id": managed.agent_id, "agent_name": managed.agent_name, **dict(receipt)}

    async def _ainvoke_persistent_round(
        self, managed: ManagedAgent, task: str, *, task_images: list[Any],
        cancel_event: threading.Event | None,
    ) -> RoundResult:
        """Persistent child calls share inbox scheduling and parent cancellation."""

        events = asyncio.Queue()
        loop = asyncio.get_running_loop()
        unsubscribe = self.subscribe(lambda event: loop.call_soon_threadsafe(events.put_nowait, event))
        try:
            receipt = await self.agent_manager.apost(managed.agent_id, {
                "attachment_type": "task_assignment", "message_id": f"msg_{uuid4().hex}",
                "created_at": time.time(), "payload": {"text": task, "task_images": task_images},
            })
            round_id, message_id = str(receipt["round_id"]), str(receipt["message_id"])
            await blocking(self.agent_manager.activate)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    await blocking(self.cancel_round, round_id, reason="caller_cancelled")
                try:
                    event = await asyncio.wait_for(events.get(), 0.05)
                except TimeoutError:
                    if managed._execution_error is not None:
                        raise managed._execution_error
                    continue
                if event.get("kind") == "message_deferred" and event.get("data", {}).get("message_id") == message_id:
                    round_id = str(event["round_id"])
                    continue
                if event.get("agent_id") == managed.agent_id and event.get("round_id") == round_id and event.get("kind") == "round_end":
                    return RoundResult.from_dict(event["data"]["result"])
        except asyncio.CancelledError:
            await blocking(self.cancel_round, round_id, reason="caller_cancelled")
            # Round cancellation is durable, but its result follows cleanup.
            while managed.round_checkpoint.get("status") == "active":
                await asyncio.sleep(0.01)
            raise
        finally:
            unsubscribe()

    @staticmethod
    def _require_persistent_async_owner(owner: ManagedAgent) -> None:
        if owner.lifecycle.value == "functional":
            raise PermissionError("functional Agent cannot launch background work")

    def launch_local_bash_async_task(self, *, owner_agent_name: str, command: str, cwd: str, timeout_seconds: float | None, max_observation_chars: int | None = None) -> dict[str, Any]:
        self._require_capability(Capability.ASYNC_TASKS)
        owner = self.agent_manager.get_by_name(owner_agent_name)
        self._require_persistent_async_owner(owner)
        return self.async_task_manager.launch_shell(
            owner_agent_id=owner.agent_id,
            owner_agent_name=owner.agent_name,
            command=command,
            cwd=cwd or self.base_dir,
            timeout_seconds=timeout_seconds,
            metadata={"max_observation_chars": max_observation_chars},
        )

    def launch_local_workflow_async_task(self, *, owner_agent_name: str, workflow: Any, args: dict[str, Any], config: dict[str, Any] | None = None, max_observation_chars: int | None = None) -> dict[str, Any]:
        """后台调用 workflow；``workflow`` 可以是名称或已通过权限检查的 WorkflowMetadata。

        取消复用 AsyncTaskManager 的任务取消信号（``async_task_stop``）。
        """

        self._require_capability(Capability.WORKFLOWS)
        self._require_capability(Capability.ASYNC_TASKS)
        owner = self.agent_manager.get_by_name(owner_agent_name)
        self._require_persistent_async_owner(owner)
        name = str(getattr(workflow, "name", workflow) or "")
        caller_round_id = owner.round_checkpoint.get("round_id")
        caller_step_num = getattr(owner.instance, "_active_step_num", None)
        async def execute(task, _path):
            return await self.workflow_manager.acall(
                workflow, args, config,
                owner_agent_name=owner.agent_name, owner_agent_id=owner.agent_id,
                round_id=caller_round_id, step_num=caller_step_num,
                cancel_event=self.async_task_manager.cancel_event_for(str(task["async_task_id"])),
            )

        return self.async_task_manager.launch(
            task_type="local_workflow", owner_agent_id=owner.agent_id, owner_agent_name=owner.agent_name,
            description=f"workflow:{name}", runner=execute,
            summary_builder=lambda _result: f"workflow {name} completed",
            metadata={"workflow_name": name, "max_observation_chars": max_observation_chars},
        )

    def tool_is_read_only(self, tool_name: str) -> bool:
        """按工具类声明判断只读，不实例化工具（未知工具视为非只读，fail-closed）。"""

        from juice_agents.core.registry.tools.defaults import get_default_tool_factories

        factory = get_default_tool_factories().get(str(tool_name or "").strip())
        return bool(getattr(factory, "is_read_only", False))

    def list_async_tasks(self, *, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        return self.async_task_manager.list_tasks(statuses=statuses)

    def get_async_task(self, async_task_id: str) -> dict[str, Any] | None:
        return self.async_task_manager.get_task(async_task_id)

    # -- interaction, inspection and persistence --------------------------------------

    def set_ask_handler(self, handler: AskHandler | None) -> None:
        self._ask_handler = handler

    def ask_user(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"status": "error", "error": "没有可用的用户交互通道"} if self._ask_handler is None else dict(self._ask_handler(dict(request)))

    def describe_agent_sessions(self, agent_id: str | None = None) -> dict[str, Any]:
        return {"runner_id": self.runner_id, "mode_id": self.mode_id,
                **self.agent_manager.describe_sessions(agent_id=agent_id)}

    def token_usage(self) -> dict[str, Any]:
        """只读代理 ModelManager 的用量汇总，不持有第二份账本或唤醒 Agent。"""
        return self.model_manager.token_usage()

    def queue_agent_user_message(self, *, agent_name: str, message: str) -> dict[str, Any]:
        managed = self._root() if agent_name == self.root_agent_name else self.agent_manager.get_by_name(agent_name)
        receipt = self.agent_manager.post(managed.agent_id, {
            "attachment_type": "agent_user_message", "message_id": f"msg_{uuid4().hex}",
            "created_at": time.time(),
            "payload": {"from": "user", "agent_name": managed.agent_name, "text": str(message)},
        })
        return {"accepted": True, "agent_name": managed.agent_name, "delivery": "agent_attachment", **receipt}

    def interrupt_agent(self, *, agent_name: str, reason: str = "user_agent_interrupt") -> dict[str, Any]:
        managed = self.agent_manager.get_by_name(agent_name)
        round_id = str(managed.round_checkpoint.get("round_id") or "")
        return {"interrupted": self.cancel_round(round_id, reason=reason), "agent_name": managed.agent_name, "round_id": round_id, "reason": reason}

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
        self.agent_manager.post(self.root_agent_id, {"attachment_type": "plan_mode_exit", "message_id": f"msg_{uuid4().hex}", "created_at": time.time(), "payload": payload})

    def pending_plan_exit(self) -> dict[str, Any] | None:
        """Read an approved exit request without changing it.

        The transport consumes this only after ``stream()`` has returned, so
        mode reconstruction never occurs inside a tool call or ``finally``.
        """

        value = self.state.get("pending_plan_exit")
        return dict(value) if isinstance(value, dict) else None

    def persist_runner_state(self) -> RunnerState:
        with self._state_lock:
            self.state = write_runner_state(self.layout.manifest_path, self.state)
            return self.state

    def subscribe_events(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Subscribe once to public execution and business events."""
        return self.register_stream_event_listener(listener)

    def subscribe(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Subscribe to all rounds, including autonomous rounds after a request."""

        return self.register_stream_event_listener(listener)

    def register_stream_event_listener(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._listeners_lock:
            self._listeners.append(listener)
        def unregister() -> None:
            with self._listeners_lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return unregister

    def _publish(self, event: dict[str, Any]) -> None:
        event.setdefault("event_id", f"evt_{uuid4().hex}")
        event.setdefault("timestamp", time.time())
        with self._listeners_lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(dict(event))
            except Exception:
                logger.exception("runner stream listener failed")

    def _emit_manager_lifecycle(self, event: dict[str, Any] | str, **details: Any) -> None:
        """Only business progress is public; storage/pool details stay in logs."""
        payload = dict(event) if isinstance(event, dict) else {"event": event, **details}
        if payload.get("scope") != "workflow":
            logger.debug("manager_lifecycle %s", payload)
            return
        agent_id = str(payload.pop("agent_id", None) or payload.get("owner_agent_id") or self.root_agent_id)
        try:
            managed = self.agent_manager.get(agent_id)
        except KeyError:
            return
        round_id = payload.pop("round_id", managed.round_checkpoint.get("round_id"))
        step_num = payload.pop("step_num", getattr(managed.instance, "_active_step_num", None))
        self.agent_manager._emit_round_event(managed, {"kind": "workflow_progress", "data": payload,
            "round_id": round_id, "step_num": step_num})


__all__ = ["Runner", "RunnerBusyError"]
