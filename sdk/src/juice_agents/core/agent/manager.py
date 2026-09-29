"""Runtime ownership for fresh Agent instances.

``AgentRegistry`` owns declarations and builds a fresh ``MultiStepAgent``.
This module owns every mutable runtime concern after that point: live
instances, session checkpoints, cooperative cancellation and release.  It is
intentionally independent of :mod:`juice_agents.core.runner`; a Runner only
passes small callbacks for capabilities such as tool context and attachments.

The separation is deliberately strict::

    AgentRegistry.instantiate() -> AgentManager.acquire() -> ManagedAgent
                                      |                     |
                                      +-- session snapshot --+-- step/run

Keeping the snapshot format here means a Runner never needs to retain an Agent
object just to resume a transcript.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypedDict
from uuid import uuid4

from juice_agents.core.agent.attachments import RuntimeAttachment, serialize_runtime_attachments_text
from juice_agents.core.agent.sessions import (
    ActionStep,
    AgentError,
    AgentSession,
    TaskStep,
    deserialize_session,
    is_recoverable_agent_error,
    observations_to_text,
    serialize_session,
)
from juice_agents.core.utils import sanitize_path_component

if TYPE_CHECKING:  # pragma: no cover - imports would create an avoidable runtime cycle
    from juice_agents.core.agent.agents import MultiStepAgent
    from juice_agents.core.registry.agents.registry import AgentRegistry


logger = logging.getLogger(__name__)

AGENT_SNAPSHOT_SCHEMA_VERSION = 1


class AgentLifecycle(StrEnum):
    """Lifetime of an Agent runtime instance.

    ``persistent`` keeps one live instance and serializes rounds with its own
    lock. ``functional`` is a fresh, one-request worker which is released as
    soon as its stream closes.  These are runtime terms only; Registry
    declarations remain static data.
    """

    PERSISTENT = "persistent"
    FUNCTIONAL = "functional"


class AgentStatus(StrEnum):
    """Observable lifecycle state of a :class:`ManagedAgent`."""

    READY = "ready"
    RUNNING = "running"
    IDLE = "idle"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    RELEASED = "released"


class AgentSnapshot(TypedDict, total=False):
    """Persisted runtime state; deliberately excludes AgentConfig/YAML data."""

    schema_version: int
    agent_id: str
    agent_name: str
    role: str
    lifecycle: str
    status: str
    is_root: bool
    metadata: dict[str, Any]
    created_at: float
    updated_at: float
    session: dict[str, Any]


class AgentSnapshotStore(Protocol):
    """Storage contract injected into :class:`AgentManager` when desired."""

    def load(self, agent_id: str) -> AgentSnapshot | None: ...

    def save(self, snapshot: AgentSnapshot) -> AgentSnapshot: ...

    def list(self) -> list[AgentSnapshot]: ...


class JsonAgentSnapshotStore:
    """Atomic JSON snapshot store rooted at one Runner-owned state directory.

    Agent runtime state is split into a small manifest and ``session.json``.
    The split keeps inspection inexpensive and prevents an update to lifecycle
    status from accidentally discarding a session.  The caller owns the root
    directory (normally ``RunnerLayout.agents_dir``); this store never reads
    project declarations or static YAML.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self._lock = threading.RLock()

    @staticmethod
    def _directory_name(agent_id: str) -> str:
        normalized = str(agent_id or "").strip()
        if not normalized:
            raise ValueError("agent_id 不能为空")
        # ``sanitize_path_component`` rejects separators while keeping the id
        # recognizable to operators inspecting a runner directory.
        return sanitize_path_component(normalized, fallback="agent")

    def _agent_dir(self, agent_id: str) -> Path:
        return self.root_dir / self._directory_name(agent_id)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"agent snapshot JSON 非法: {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"agent snapshot 必须是 object: {path}")
        return payload

    @staticmethod
    def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
        """Write one JSON file without exposing a half-written checkpoint."""

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            # os.replace succeeds atomically.  A failed serialization/write
            # should not leave an ignored temp file in the user's run state.
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _validate(snapshot: Mapping[str, Any]) -> AgentSnapshot:
        schema_version = snapshot.get("schema_version")
        if schema_version != AGENT_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(
                "agent snapshot schema 不支持恢复: "
                f"schema_version={schema_version!r}; expected={AGENT_SNAPSHOT_SCHEMA_VERSION}"
            )
        agent_id = str(snapshot.get("agent_id") or "").strip()
        agent_name = str(snapshot.get("agent_name") or "").strip()
        lifecycle = str(snapshot.get("lifecycle") or "").strip()
        status = str(snapshot.get("status") or "").strip()
        if not agent_id or not agent_name:
            raise ValueError("agent snapshot 缺少 agent_id 或 agent_name")
        if lifecycle not in {item.value for item in AgentLifecycle}:
            raise ValueError(f"agent snapshot lifecycle 非法: {lifecycle!r}")
        if status not in {item.value for item in AgentStatus}:
            raise ValueError(f"agent snapshot status 非法: {status!r}")
        session = snapshot.get("session") or {}
        if not isinstance(session, dict):
            raise ValueError("agent snapshot session 必须是 object")
        return {
            "schema_version": AGENT_SNAPSHOT_SCHEMA_VERSION,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "role": str(snapshot.get("role") or "member").strip() or "member",
            "lifecycle": lifecycle,
            "status": status,
            "is_root": bool(snapshot.get("is_root")),
            "metadata": dict(snapshot.get("metadata") or {}),
            "created_at": float(snapshot.get("created_at") or time.time()),
            "updated_at": float(snapshot.get("updated_at") or time.time()),
            "session": dict(session),
        }

    def load(self, agent_id: str) -> AgentSnapshot | None:
        with self._lock:
            agent_dir = self._agent_dir(agent_id)
            manifest_path = agent_dir / "manifest.json"
            session_path = agent_dir / "session.json"
            if not manifest_path.exists() and not session_path.exists():
                return None
            if not manifest_path.exists() or not session_path.exists():
                raise ValueError(f"agent snapshot 不完整: {agent_dir}")
            manifest = self._read_json(manifest_path)
            session = self._read_json(session_path)
            return self._validate({**manifest, "session": session})

    def save(self, snapshot: AgentSnapshot) -> AgentSnapshot:
        with self._lock:
            normalized = self._validate(snapshot)
            agent_dir = self._agent_dir(str(normalized["agent_id"]))
            manifest = {key: value for key, value in normalized.items() if key != "session"}
            self._atomic_write(agent_dir / "session.json", dict(normalized["session"]))
            self._atomic_write(agent_dir / "manifest.json", manifest)
            return normalized

    def list(self) -> list[AgentSnapshot]:
        with self._lock:
            if not self.root_dir.exists():
                return []
            snapshots: list[AgentSnapshot] = []
            for agent_dir in sorted(path for path in self.root_dir.iterdir() if path.is_dir()):
                try:
                    snapshot = self.load(agent_dir.name)
                except ValueError:
                    logger.warning("跳过损坏的 agent snapshot: path=%s", agent_dir)
                    continue
                if snapshot is not None:
                    snapshots.append(snapshot)
            return snapshots


class AgentExecutionInterrupted(RuntimeError):
    """Raised by AgentManager at a cooperative step boundary after interrupt."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "agent_interrupted")
        super().__init__(self.reason)


@dataclass(slots=True)
class ManagedAgent:
    """The sole live-runtime record for one acquired Agent.

    ``session`` remains available after ``instance`` is released.  This makes
    a functional worker's final transcript inspectable without retaining a
    live model/tool object, which is important for deterministic release.
    """

    agent_id: str
    agent_name: str
    role: str
    lifecycle: AgentLifecycle
    instance: "MultiStepAgent | None"
    session: AgentSession
    status: AgentStatus = AgentStatus.READY
    is_root: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _run_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def is_live(self) -> bool:
        return self.instance is not None and self.status is not AgentStatus.RELEASED


AgentRuntimeBinder = Callable[[ManagedAgent, threading.Event], None]
AgentAttachmentsProvider = Callable[[ManagedAgent, int], list[RuntimeAttachment]]
AgentAfterStep = Callable[[ManagedAgent, ActionStep], None]
AgentPersistenceHook = Callable[[AgentSnapshot], None]
AgentReleaseHook = Callable[[ManagedAgent], None]


@dataclass(frozen=True, slots=True)
class AgentManagerCallbacks:
    """Runner-supplied runtime capabilities, kept deliberately narrow."""

    bind_runtime: AgentRuntimeBinder | None = None
    attachments_provider: AgentAttachmentsProvider | None = None
    after_step: AgentAfterStep | None = None
    after_persist: AgentPersistenceHook | None = None
    after_release: AgentReleaseHook | None = None


def _binding_value(binding: Any, name: str) -> Any:
    """Read a field from a dataclass-like binding or a declaration mapping."""

    if isinstance(binding, Mapping):
        return binding.get(name)
    return getattr(binding, name, None)


def _binding_ref(binding: Any) -> Any:
    """Unwrap the declared ref while accepting the compact Runner binding DTO."""

    nested = _binding_value(binding, "agent_ref")
    if nested is None:
        nested = _binding_value(binding, "ref")
    return binding if nested is None else nested


def _binding_name(binding: Any) -> str:
    raw_ref = _binding_ref(binding)
    name = _binding_value(binding, "name") or _binding_value(raw_ref, "name")
    if name is None and isinstance(raw_ref, str):
        name = raw_ref
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("AgentBinding 必须提供非空 name")
    return normalized


def _normalize_lifecycle(value: AgentLifecycle | str | None, *, binding: Any) -> AgentLifecycle:
    candidate = value
    if candidate is None:
        candidate = _binding_value(binding, "lifecycle")
    normalized = str(candidate or AgentLifecycle.PERSISTENT).strip()
    try:
        return AgentLifecycle(normalized)
    except ValueError as exc:
        raise ValueError(
            f"未知 agent lifecycle: {normalized!r}; expected persistent or functional"
        ) from exc


def _resolved_agent_type(instance: Any) -> str:
    return str(
        getattr(instance, "_resolved_agent_type", "")
        or ("codeact" if "codeact" in type(instance).__name__.lower() else "react")
    )


class AgentManager:
    """Create, restore, execute, interrupt and release Agent runtime objects.

    The manager never imports Runner, reads a declaration file, or stores a
    domain executor.  A caller supplies a Registry and optional callbacks; the
    only construction operation is ``registry.instantiate(...)``.  Therefore
    every restore produces a fresh Agent class/tool surface before its session
    is loaded.
    """

    def __init__(
        self,
        *,
        registry: "AgentRegistry",
        runner_id: str,
        state_dir: str | Path | None = None,
        layout: Any | None = None,
        runtime_config_path: str | Path | None = None,
        permission_mode: str = "default",
        workspace_dir: str | Path | None = None,
        callbacks: AgentManagerCallbacks | None = None,
        snapshot_store: AgentSnapshotStore | None = None,
    ) -> None:
        self.registry = registry
        self.runner_id = str(runner_id or "").strip()
        if not self.runner_id:
            raise ValueError("runner_id 不能为空")
        self.runtime_config_path = None if runtime_config_path is None else str(runtime_config_path)
        self.permission_mode = str(permission_mode or "default").strip() or "default"
        self.workspace_dir = None if workspace_dir is None else Path(workspace_dir).expanduser().resolve()
        self.callbacks = callbacks or AgentManagerCallbacks()
        self._lock = threading.RLock()
        self._managed_by_id: dict[str, ManagedAgent] = {}
        self._managed_id_by_name: dict[str, str] = {}
        self._interrupt_reasons: dict[str, str] = {}

        if snapshot_store is not None:
            self.snapshot_store = snapshot_store
        else:
            root = self._resolve_state_dir(state_dir=state_dir, layout=layout)
            self.snapshot_store = JsonAgentSnapshotStore(root)

    @staticmethod
    def _resolve_state_dir(*, state_dir: str | Path | None, layout: Any | None) -> Path:
        """Use the new layout's ``agents_dir`` without depending on its class."""

        if layout is not None:
            candidate = getattr(layout, "agents_dir", None)
            if candidate is not None:
                return Path(candidate)
        if state_dir is not None:
            return Path(state_dir).expanduser().resolve() / "agents"
        raise ValueError("AgentManager 需要 state_dir、layout.agents_dir 或 snapshot_store")

    @property
    def live_agents(self) -> Mapping[str, ManagedAgent]:
        """Read-only view keyed by stable ``agent_id``; Manager owns the dict."""

        with self._lock:
            return dict(self._managed_by_id)

    def _agent_id_for(
        self,
        *,
        agent_name: str,
        lifecycle: AgentLifecycle,
        is_root: bool,
        requested_id: str | None,
    ) -> str:
        if requested_id is not None:
            resolved = str(requested_id).strip()
            if not resolved:
                raise ValueError("agent_id 不能为空")
            return resolved
        if is_root:
            return "root"
        safe_name = sanitize_path_component(agent_name, fallback="agent")
        if lifecycle is AgentLifecycle.PERSISTENT:
            return f"persistent--{safe_name}"
        return f"functional--{safe_name}--{uuid4().hex[:12]}"

    def _instantiate(self, binding: Any, *, model: Any | None, runtime_kwargs: Mapping[str, Any]) -> "MultiStepAgent":
        """Call the Registry's sole fresh-construction API.

        No fallback to a former ``create`` API is intentional.  A Registry
        without ``instantiate`` violates the Registry/Manager boundary and
        should fail loudly during composition rather than silently reviving an
        old runtime path.
        """

        instantiate = getattr(self.registry, "instantiate", None)
        if not callable(instantiate):
            raise TypeError("AgentRegistry 必须提供 instantiate() 供 AgentManager 构造 fresh instance")
        kwargs: dict[str, Any] = dict(runtime_kwargs)
        if model is not None:
            kwargs["model"] = model
        if self.runtime_config_path is not None:
            kwargs.setdefault("runtime_config_path", self.runtime_config_path)
        if self.workspace_dir is not None:
            kwargs.setdefault("juice_root", self.workspace_dir)
        instance = instantiate(_binding_ref(binding), **kwargs)
        if not hasattr(instance, "session") or not hasattr(instance, "step"):
            raise TypeError("AgentRegistry.instantiate() 必须返回具备 session 与 step() 的 MultiStepAgent")
        return instance

    def _snapshot_for(self, managed: ManagedAgent) -> AgentSnapshot:
        return {
            "schema_version": AGENT_SNAPSHOT_SCHEMA_VERSION,
            "agent_id": managed.agent_id,
            "agent_name": managed.agent_name,
            "role": managed.role,
            "lifecycle": managed.lifecycle.value,
            "status": managed.status.value,
            "is_root": managed.is_root,
            "metadata": {"resolved_agent_type": _resolved_agent_type(managed.instance), **managed.metadata},
            "created_at": managed.created_at,
            "updated_at": managed.updated_at,
            "session": serialize_session(managed.session),
        }

    @staticmethod
    def _session_from_state(session_state: Mapping[str, Any] | AgentSession | None) -> AgentSession | None:
        if session_state is None:
            return None
        if isinstance(session_state, AgentSession):
            return session_state.clone()
        if not isinstance(session_state, Mapping):
            raise TypeError("session_state 必须是 AgentSession 或可序列化 object")
        return deserialize_session(dict(session_state))

    def acquire(
        self,
        binding: Any,
        *,
        role: str | None = None,
        lifecycle: AgentLifecycle | str | None = None,
        session_state: Mapping[str, Any] | AgentSession | None = None,
        is_root: bool = False,
        agent_id: str | None = None,
        agent_name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        model: Any | None = None,
        **runtime_kwargs: Any,
    ) -> ManagedAgent:
        """Acquire one fresh runtime instance and checkpoint its restored session.

        The order is fixed: locate snapshot -> fresh Registry instance ->
        restore session -> bind runtime callback -> persist.  This prevents a
        stale instance from carrying old tools or model state across a resume.
        """

        resolved_lifecycle = _normalize_lifecycle(lifecycle, binding=binding)
        declaration_name = _binding_name(binding)
        resolved_name = str(agent_name or declaration_name).strip()
        if not resolved_name:
            raise ValueError("agent_name 不能为空")
        resolved_role = str(role or _binding_value(binding, "role") or "member").strip() or "member"
        resolved_id = self._agent_id_for(
            agent_name=resolved_name,
            lifecycle=resolved_lifecycle,
            is_root=is_root,
            requested_id=agent_id,
        )

        with self._lock:
            existing = self._managed_by_id.get(resolved_id)
            if existing is not None and existing.is_live:
                if existing.lifecycle is not resolved_lifecycle:
                    raise ValueError(
                        f"agent_id={resolved_id!r} 已以 lifecycle={existing.lifecycle.value!r} 获取"
                    )
                logger.debug(
                    "agent_acquire_reused runner_id=%s agent_id=%s agent_name=%s",
                    self.runner_id,
                    existing.agent_id,
                    existing.agent_name,
                )
                return existing

            persisted = self.snapshot_store.load(resolved_id)
            if persisted is not None:
                if bool(persisted.get("is_root")) != bool(is_root):
                    raise ValueError(f"agent snapshot root 身份不匹配: agent_id={resolved_id}")
                if str(persisted.get("lifecycle") or "") != resolved_lifecycle.value:
                    raise ValueError(f"agent snapshot lifecycle 不匹配: agent_id={resolved_id}")

            instance = self._instantiate(binding, model=model, runtime_kwargs=runtime_kwargs)
            restored_session = self._session_from_state(session_state)
            if restored_session is None and persisted is not None:
                restored_session = self._session_from_state(persisted.get("session") or {})
            if restored_session is not None:
                instance.session = restored_session

            instance.agent_id = resolved_id
            effective_metadata = dict((persisted or {}).get("metadata") or {})
            # ``resolved_agent_type`` is generated from the fresh instance and
            # must not be trusted from old runtime state.
            effective_metadata.pop("resolved_agent_type", None)
            effective_metadata.update(dict(metadata or {}))
            managed = ManagedAgent(
                agent_id=resolved_id,
                agent_name=resolved_name,
                role=resolved_role,
                lifecycle=resolved_lifecycle,
                instance=instance,
                session=instance.session,
                status=AgentStatus.READY,
                is_root=is_root,
                metadata=effective_metadata,
                created_at=float((persisted or {}).get("created_at") or time.time()),
            )
            self._managed_by_id[resolved_id] = managed
            # Functional runs use unique names in Runner capability dispatch.
            # If a caller deliberately reuses one name, only the newest entry
            # is addressable by name; both remain available by immutable id.
            self._managed_id_by_name[resolved_name] = resolved_id

            try:
                if self.callbacks.bind_runtime is not None:
                    self.callbacks.bind_runtime(managed, managed.cancel_event)
                self.persist(managed)
            except Exception:
                self._managed_by_id.pop(resolved_id, None)
                if self._managed_id_by_name.get(resolved_name) == resolved_id:
                    self._managed_id_by_name.pop(resolved_name, None)
                raise
            logger.info(
                "agent_acquired runner_id=%s agent_id=%s agent_name=%s role=%s lifecycle=%s restored=%s",
                self.runner_id,
                managed.agent_id,
                managed.agent_name,
                managed.role,
                managed.lifecycle.value,
                persisted is not None or session_state is not None,
            )
            return managed

    def acquire_root(self, binding: Any, **kwargs: Any) -> ManagedAgent:
        """Lazy root acquisition; Runner must never construct a root directly."""

        # Root is a role, not a lifetime.  A declarative RunnerConfig may use
        # either a persistent conversation root or a functional request root;
        # both still follow the same acquire -> run -> checkpoint -> release
        # lifecycle owned by this manager.
        lifecycle = kwargs.pop("lifecycle", None)
        return self.acquire(
            binding,
            role=str(kwargs.pop("role", "root") or "root"),
            lifecycle=lifecycle,
            is_root=True,
            agent_id="root",
            **kwargs,
        )

    def get(self, identifier: str) -> ManagedAgent:
        """Get a live managed Agent by stable id or current runtime name."""

        normalized = str(identifier or "").strip()
        with self._lock:
            managed = self._managed_by_id.get(normalized)
            if managed is None:
                managed = self._managed_by_id.get(self._managed_id_by_name.get(normalized, ""))
            if managed is None:
                raise KeyError(f"未知 managed agent: {identifier}")
            return managed

    def get_by_name(self, agent_name: str) -> ManagedAgent:
        """Return the current live Agent selected by its runtime name."""

        normalized = str(agent_name or "").strip()
        with self._lock:
            agent_id = self._managed_id_by_name.get(normalized)
            if agent_id is None:
                raise KeyError(f"未知 managed agent name: {agent_name}")
            return self._managed_by_id[agent_id]

    def load_snapshot(self, agent_id: str) -> AgentSnapshot | None:
        """Load and validate a persisted snapshot without acquiring an Agent."""

        return self.snapshot_store.load(agent_id)

    @staticmethod
    def deserialize_snapshot(snapshot: Mapping[str, Any]) -> AgentSession:
        """Decode a validated snapshot session for read-only recovery tooling."""

        normalized = JsonAgentSnapshotStore._validate(snapshot)
        return deserialize_session(dict(normalized["session"]))

    def persist(self, managed: ManagedAgent | str) -> AgentSnapshot:
        """Checkpoint a session and lifecycle state without retaining Runner state."""

        item = self.get(managed) if isinstance(managed, str) else managed
        with self._lock:
            item.updated_at = time.time()
            snapshot = self.snapshot_store.save(self._snapshot_for(item))
            if self.callbacks.after_persist is not None:
                self.callbacks.after_persist(snapshot)
            logger.debug(
                "agent_checkpointed runner_id=%s agent_id=%s status=%s steps=%s",
                self.runner_id,
                item.agent_id,
                item.status.value,
                len(item.session.steps),
            )
            return snapshot

    def interrupt(self, managed: ManagedAgent | str | None = None, *, reason: str = "agent_interrupted") -> list[str]:
        """Request cooperative interruption; no thread is forcefully killed."""

        with self._lock:
            targets = (
                [self.get(managed) if isinstance(managed, str) else managed]
                if managed is not None
                else [item for item in self._managed_by_id.values() if item.status is AgentStatus.RUNNING]
            )
            interrupted: list[str] = []
            for item in targets:
                if item.status is not AgentStatus.RUNNING:
                    continue
                item.cancel_event.set()
                self._interrupt_reasons[item.agent_id] = str(reason or "agent_interrupted")
                interrupted.append(item.agent_id)
                logger.info(
                    "agent_interrupt_requested runner_id=%s agent_id=%s reason=%s",
                    self.runner_id,
                    item.agent_id,
                    reason,
                )
            return interrupted

    def _raise_if_interrupted(self, managed: ManagedAgent) -> None:
        if managed.cancel_event.is_set():
            raise AgentExecutionInterrupted(
                self._interrupt_reasons.get(managed.agent_id, "agent_interrupted")
            )

    @staticmethod
    def _format_task_log_content(task: str, attachments: list[RuntimeAttachment]) -> str:
        serialized = serialize_runtime_attachments_text(attachments)
        return task if not serialized else f"{task}\n\n{serialized}"

    @staticmethod
    def _normalize_failed_step(instance: Any, step: ActionStep, exc: Exception) -> ActionStep:
        """Keep Agent protocol errors recoverable without reintroducing an Executor."""

        normalizer = getattr(type(instance), "_normalize_failed_action_step", None)
        if callable(normalizer):
            return normalizer(instance, step, exc)
        step.model_output = step.model_output or "异常终止"
        step.error = exc if isinstance(exc, AgentError) else AgentError(str(exc))
        step.round_outcome = "continue" if is_recoverable_agent_error(exc) else "failed"
        return step

    def _perform_step(self, managed: ManagedAgent, step_index: int) -> ActionStep:
        instance = managed.instance
        if instance is None:
            raise RuntimeError(f"agent 已释放，不能执行: {managed.agent_id}")
        self._raise_if_interrupted(managed)
        step = ActionStep(step_num=step_index, model_output="")
        try:
            returned = instance.step(step)
        except Exception as exc:
            # A model/provider may observe the callback-bound cancel token and
            # raise its own cancellation exception. Preserve that failure as an
            # interruption rather than serializing it as a protocol error.
            if managed.cancel_event.is_set():
                self._raise_if_interrupted(managed)
            logger.exception(
                "agent_step_failed runner_id=%s agent_id=%s step=%s",
                self.runner_id,
                managed.agent_id,
                step_index,
            )
            return self._normalize_failed_step(instance, step, exc=exc)
        if returned is None:
            return step
        if not isinstance(returned, ActionStep):
            raise TypeError("Agent.step() 必须返回 ActionStep 或 None")
        return returned

    def _finish_step(self, managed: ManagedAgent, step: ActionStep, step_index: int) -> ActionStep:
        instance = managed.instance
        if instance is None:
            raise RuntimeError(f"agent 已释放，不能写入 session: {managed.agent_id}")
        attachments = (
            list(self.callbacks.attachments_provider(managed, step_index) or [])
            if self.callbacks.attachments_provider is not None and step.round_outcome == "continue"
            else []
        )
        step.attachments = attachments
        instance.session.append_step(step)
        managed.session = instance.session
        # A session checkpoint is the durability boundary for every action;
        # callbacks may publish events, but cannot make the transcript vanish.
        self.persist(managed)
        if self.callbacks.after_step is not None:
            self.callbacks.after_step(managed, step)
        return step

    def stream(
        self,
        managed: ManagedAgent | str,
        task: str,
        *,
        task_images: list[Any] | None = None,
        reset_session: bool = False,
        attachments: list[RuntimeAttachment] | None = None,
    ) -> Iterator[ActionStep]:
        """Run a managed Agent round and yield durable action steps.

        Persistent Agents hold their per-agent lock for the full generator
        lifetime. Functional Agents have unique identities and are released in
        ``finally`` even when a caller stops consuming the generator.
        """

        item = self.get(managed) if isinstance(managed, str) else managed
        normalized_task = str(task or "")
        if not normalized_task.strip():
            raise ValueError("task 不能为空")

        def _stream() -> Iterator[ActionStep]:
            with item._run_lock:
                if not item.is_live:
                    raise RuntimeError(f"agent 已释放，不能执行: {item.agent_id}")
                instance = item.instance
                assert instance is not None  # narrow type for protocol calls
                item.cancel_event.clear()
                self._interrupt_reasons.pop(item.agent_id, None)
                item.status = AgentStatus.RUNNING
                self.persist(item)
                initial_attachments = list(attachments or [])
                try:
                    if reset_session:
                        reset_tools = getattr(instance, "_reset_tool_runtime_state", None)
                        if callable(reset_tools):
                            reset_tools()
                        instance.session.steps.clear()
                    instance.session.append_step(
                        TaskStep(
                            task=normalized_task,
                            task_images=list(task_images or []),
                            attachments=initial_attachments,
                        )
                    )
                    item.session = instance.session
                    self.persist(item)
                    pretty_log = getattr(instance, "_pretty_log", None)
                    if callable(pretty_log):
                        pretty_log("用户任务", self._format_task_log_content(normalized_task, initial_attachments))
                    logger.info(
                        "agent_run_started runner_id=%s agent_id=%s agent_name=%s",
                        self.runner_id,
                        item.agent_id,
                        item.agent_name,
                    )

                    terminated = False
                    last_index = 0
                    for step_index in range(1, int(instance.max_steps) + 1):
                        last_index = step_index
                        action_step = self._finish_step(item, self._perform_step(item, step_index), step_index)
                        yield action_step
                        self._raise_if_interrupted(item)
                        if action_step.round_outcome in {"submitted", "yielded", "failed"}:
                            terminated = True
                            break

                    if not terminated:
                        # The fallback is a fixed runtime instruction, not
                        # user prompt content.  It guarantees one final chance
                        # to submit a partial answer when the step budget ends.
                        fallback = TaskStep(
                            task=(
                                "[runtime] 你已用完所有步数。请立即根据已有信息调用 "
                                "submit_output 提交结果；即使结果不完整也必须提交。"
                            )
                        )
                        instance.session.append_step(fallback)
                        item.session = instance.session
                        self.persist(item)
                        fallback_step = self._finish_step(
                            item,
                            self._perform_step(item, max(last_index, 0) + 1),
                            max(last_index, 0) + 1,
                        )
                        yield fallback_step
                    item.status = AgentStatus.IDLE
                    self.persist(item)
                    logger.info(
                        "agent_run_finished runner_id=%s agent_id=%s status=%s",
                        self.runner_id,
                        item.agent_id,
                        item.status.value,
                    )
                except AgentExecutionInterrupted:
                    item.status = AgentStatus.INTERRUPTED
                    self.persist(item)
                    logger.info(
                        "agent_run_interrupted runner_id=%s agent_id=%s reason=%s",
                        self.runner_id,
                        item.agent_id,
                        self._interrupt_reasons.get(item.agent_id, "agent_interrupted"),
                    )
                    raise
                except Exception:
                    item.status = AgentStatus.FAILED
                    self.persist(item)
                    raise
                finally:
                    if item.lifecycle is AgentLifecycle.FUNCTIONAL and item.is_live:
                        self.release(item)

        return _stream()

    def run(self, managed: ManagedAgent | str, task: str, **kwargs: Any) -> Any:
        """Consume :meth:`stream` and return the normal Agent output value."""

        item = self.get(managed) if isinstance(managed, str) else managed
        last_step: ActionStep | None = None
        for last_step in self.stream(item, task, **kwargs):
            pass
        if last_step is None:
            return None
        if last_step.round_outcome == "submitted" and not last_step.error:
            return last_step.output
        output = (
            (str(last_step.error) if last_step.error else "")
            or observations_to_text(last_step.observations)
            or last_step.model_output
        )
        if last_step.round_outcome == "continue" and not str(output or "").strip():
            return (
                f"[runtime] {item.agent_name} 执行了 {getattr(item.instance, 'max_steps', 0)} 步"
                "但未提交有效输出。"
            )
        return output

    def release(self, managed: ManagedAgent | str) -> AgentSnapshot:
        """Drop the live instance after persisting its transcript exactly once."""

        item = self.get(managed) if isinstance(managed, str) else managed
        with self._lock:
            if item.status is AgentStatus.RELEASED:
                return self._snapshot_for(item)
            item.status = AgentStatus.RELEASED
            snapshot = self.persist(item)
            item.instance = None
            self._managed_by_id.pop(item.agent_id, None)
            if self._managed_id_by_name.get(item.agent_name) == item.agent_id:
                self._managed_id_by_name.pop(item.agent_name, None)
            if self.callbacks.after_release is not None:
                self.callbacks.after_release(item)
            logger.info(
                "agent_released runner_id=%s agent_id=%s agent_name=%s",
                self.runner_id,
                item.agent_id,
                item.agent_name,
            )
            return snapshot

    def describe_sessions(self) -> dict[str, Any]:
        """Return live-or-persisted transcript snapshots without resuming work."""

        snapshots_by_id = {snapshot["agent_id"]: snapshot for snapshot in self.snapshot_store.list()}
        with self._lock:
            for managed in self._managed_by_id.values():
                snapshots_by_id[managed.agent_id] = self._snapshot_for(managed)
        agents = sorted(
            snapshots_by_id.values(),
            key=lambda snapshot: (not bool(snapshot.get("is_root")), str(snapshot.get("agent_name") or "")),
        )
        return {
            "runner_id": self.runner_id,
            "agents": [
                {
                    **dict(snapshot),
                    "steps": list(dict(snapshot.get("session") or {}).get("steps") or []),
                }
                for snapshot in agents
            ],
        }


__all__ = [
    "AGENT_SNAPSHOT_SCHEMA_VERSION",
    "AgentAttachmentsProvider",
    "AgentExecutionInterrupted",
    "AgentLifecycle",
    "AgentManager",
    "AgentManagerCallbacks",
    "AgentSnapshot",
    "AgentSnapshotStore",
    "AgentStatus",
    "JsonAgentSnapshotStore",
    "ManagedAgent",
]
