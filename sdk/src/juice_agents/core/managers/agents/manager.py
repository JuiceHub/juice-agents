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
import asyncio
from contextlib import aclosing
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypedDict
from uuid import uuid4

from juice_agents.core.agent.attachments import RuntimeAttachment, serialize_runtime_attachments_text
from juice_agents.core.agent.context import (
    ActionStep,
    AgentError,
    ContextNode,
    ContextTree,
    NodeStatus,
    TaskStep,
    deserialize_context_tree,
    is_recoverable_agent_error,
    observations_to_text,
    serialize_context_transcript,
    serialize_context_tree,
)
from juice_agents.core.utils import sanitize_path_component
from ..models import ModelManager
from ...runner.execution.async_bridge import run_async, iter_sync, iter_async, on_runtime
from ...runner.execution.async_io import blocking, iterate_blocking
from .inbox import AgentInboxScheduler
from ...agent.contracts import RoundResult

if TYPE_CHECKING:  # pragma: no cover - imports would create an avoidable runtime cycle
    from juice_agents.core.agent.agents import MultiStepAgent
    from juice_agents.core.registry.agents.registry import AgentRegistry


logger = logging.getLogger(__name__)

AGENT_SNAPSHOT_SCHEMA_VERSION = 2


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

    RUNNING = "running"
    IDLE = "idle"
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
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
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
            # The session is the commit record. A process may stop after the
            # manifest write but before its matching session replacement.
            return self._validate({**manifest, "status": session.get("agent_status", manifest.get("status")),
                                   "session": session})

    def save(self, snapshot: AgentSnapshot) -> AgentSnapshot:
        with self._lock:
            normalized = self._validate(snapshot)
            agent_dir = self._agent_dir(str(normalized["agent_id"]))
            manifest = {key: value for key, value in normalized.items() if key != "session"}
            # Write the summary first and the authoritative checkpoint last.
            # A manifest failure therefore cannot follow an already committed
            # successful result, and session.agent_status rejects an ahead-of-
            # checkpoint manifest after a crash or failed second write.
            self._atomic_write(agent_dir / "manifest.json", manifest)
            self._atomic_write(agent_dir / "session.json", {
                **dict(normalized["session"]), "agent_status": normalized["status"],
            })
            return normalized

    def list(self) -> list[AgentSnapshot]:
        with self._lock:
            if not self.root_dir.exists():
                return []
            snapshots: list[AgentSnapshot] = []
            for agent_dir in sorted(path for path in self.root_dir.iterdir() if path.is_dir()):
                # Unsupported state is visible at the recovery boundary. A
                # silently omitted identity could later overwrite old history.
                snapshot = self.load(agent_dir.name)
                if snapshot is not None:
                    snapshots.append(snapshot)
            return snapshots


class AgentExecutionInterrupted(RuntimeError):
    """Raised by AgentManager at a cooperative step boundary after interrupt."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "agent_interrupted")
        super().__init__(self.reason)


class AgentCancellationEvent(threading.Event):
    """A round token that also observes its synchronous caller's token.

    Provider/tool APIs accept the usual Event methods. Waiting polls at a short
    interval only when two independent tokens must be observed, so propagation
    needs no relay thread and never cancels independent asynchronous work.
    """

    parent: threading.Event | None = None

    def is_set(self) -> bool:
        return super().is_set() or (self.parent is not None and self.parent.is_set())

    def wait(self, timeout: float | None = None) -> bool:
        if self.parent is None:
            return super().wait(timeout)
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = 0.05 if deadline is None else min(0.05, deadline - time.monotonic())
            if remaining <= 0:
                return self.is_set()
            super().wait(remaining)
        return True


@dataclass(slots=True)
class ManagedAgent:
    """The sole live-runtime record for one acquired Agent.

    ``context`` remains available after ``instance`` is released.  This makes
    a functional worker's final transcript inspectable without retaining a
    live model/tool object, which is important for deterministic release.
    """

    agent_id: str
    agent_name: str
    role: str
    lifecycle: AgentLifecycle
    instance: "MultiStepAgent | None"
    context: ContextTree
    status: AgentStatus = AgentStatus.IDLE
    is_root: bool = False
    # ephemeral agent（workflow 内部调用）只在内存中存在：不写 snapshot、不触发
    # after_step 回调、不写文件日志；只保留不含正文的模型用量记录。
    ephemeral: bool = False
    # 非空时覆盖 Runner 的审批模式（workflow 内部 agent 固定为 accept）。
    permission_mode: str = ""
    # 调用方的取消信号（例如外层 workflow 的 cancel_event），与自身 cancel_event
    # 一起在 step 边界检查；不需要额外线程转发。
    external_cancel: threading.Event | None = field(default=None, repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel_event: AgentCancellationEvent = field(default_factory=AgentCancellationEvent, repr=False)
    _run_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # These fields live in session.json beside the tree. A transcript and its
    # scheduling decision must cross the same atomic durability boundary.
    round_checkpoint: dict[str, Any] = field(default_factory=dict)
    # Outcomes belong to rounds, including those without a generated step.
    # Retaining them separately preserves cancellation/failure on cold reads.
    round_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    committed_message_ids: list[str] = field(default_factory=list)
    message_round_ids: dict[str, str] = field(default_factory=dict)
    # Context mutation can precede a failed file write. Only successful saves
    # authorize producer acknowledgement; an in-memory append is insufficient.
    _persisted_message_ids: set[str] = field(default_factory=set, repr=False)
    _current_step: ActionStep | None = field(default=None, repr=False)
    _published_round_id: str | None = field(default=None, repr=False)
    _execution_error: BaseException | None = field(default=None, repr=False)

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
    on_event: Callable[[ManagedAgent, dict[str, Any]], None] | None = None
    acknowledge: Callable[[ManagedAgent, list[RuntimeAttachment]], None] | None = None


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


class _ManagedStream:
    """Give close() meaning even before a Python generator's first next()."""

    def __init__(self, iterator: Iterator[Any], on_unstarted_close: Callable[[], None]) -> None:
        self.iterator = iterator
        self.on_unstarted_close = on_unstarted_close
        self.started = False

    def __iter__(self) -> _ManagedStream:
        return self

    def __next__(self) -> Any:
        self.started = True
        return next(self.iterator)

    def close(self) -> None:
        if self.started:
            self.iterator.close()
        else:
            self.on_unstarted_close()


class AgentManager(AgentInboxScheduler):
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
        model_manager: ModelManager | None = None,
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
        self._activated = False
        self._closed = False
        self._workers: dict[str, Any] = {}
        self._inboxes: dict[str, list[dict[str, Any]]] = {}

        if snapshot_store is not None:
            self.snapshot_store = snapshot_store
        else:
            root = self._resolve_state_dir(state_dir=state_dir, layout=layout)
            self.snapshot_store = JsonAgentSnapshotStore(root)

        usage_root = (
            self._resolve_state_dir(state_dir=state_dir, layout=layout)
            if state_dir is not None or layout is not None
            else getattr(self.snapshot_store, "root_dir", None)
        )
        self._owns_model_manager = model_manager is None
        self.model_manager = model_manager or ModelManager(
            usage_path=Path(usage_root) / "model_usage.jsonl" if usage_root is not None else None
        )
        self._inbox_root = None if usage_root is None else Path(usage_root)

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
            kwargs.setdefault("juice_root", self.workspace_dir / ".juice")
        instance = instantiate(_binding_ref(binding), **kwargs)
        if not hasattr(instance, "context") or not hasattr(instance, "step"):
            raise TypeError("AgentRegistry.instantiate() 必须返回具备 context 与 step() 的 MultiStepAgent")
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
            "session": {
                **serialize_context_tree(managed.context),
                "agent_status": managed.status.value,
                "round_checkpoint": deepcopy(managed.round_checkpoint),
                "round_results": deepcopy(managed.round_results),
                "committed_message_ids": list(managed.committed_message_ids),
                "message_round_ids": dict(managed.message_round_ids),
            },
        }

    @staticmethod
    def _session_from_state(session_state: Mapping[str, Any] | ContextTree | None) -> ContextTree | None:
        if session_state is None:
            return None
        if isinstance(session_state, ContextTree):
            payload = serialize_context_tree(session_state)
        elif isinstance(session_state, Mapping):
            payload = dict(session_state)
        else:
            raise TypeError("session_state 必须是 ContextTree 或可序列化 object")
        # Serialization normalizes the schema but retains nested payloads such
        # as output, tool arguments and metadata. Detach once at the Manager's
        # ownership boundary so callers and inherited sessions evolve freely.
        return deserialize_context_tree(deepcopy(payload))

    def acquire(
        self,
        binding: Any,
        *,
        role: str | None = None,
        lifecycle: AgentLifecycle | str | None = None,
        session_state: Mapping[str, Any] | ContextTree | None = None,
        is_root: bool = False,
        agent_id: str | None = None,
        agent_name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        model: Any | None = None,
        ephemeral: bool = False,
        permission_mode: str = "",
        external_cancel: threading.Event | None = None,
        **runtime_kwargs: Any,
    ) -> ManagedAgent:
        """Acquire one fresh runtime instance and checkpoint its restored session.

        The order is fixed: locate snapshot -> fresh Registry instance ->
        restore session -> bind runtime callback -> persist.  This prevents a
        stale instance from carrying old tools or model state across a resume.
        """

        resolved_lifecycle = _normalize_lifecycle(lifecycle, binding=binding)
        if ephemeral and (is_root or resolved_lifecycle is not AgentLifecycle.FUNCTIONAL):
            raise ValueError("ephemeral agent 必须是非 root 的 functional agent")
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
            if self._closed:
                raise RuntimeError("AgentManager 已关闭")
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

            persisted = None if ephemeral else self.snapshot_store.load(resolved_id)
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
                instance.context = restored_session

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
                context=instance.context,
                status=AgentStatus.IDLE,
                is_root=is_root,
                ephemeral=ephemeral,
                permission_mode=str(permission_mode or "").strip(),
                external_cancel=external_cancel,
                metadata=effective_metadata,
                created_at=float((persisted or {}).get("created_at") or time.time()),
                round_checkpoint=deepcopy(dict((persisted or {}).get("session") or {}).get("round_checkpoint") or {}),
                round_results=deepcopy(dict((persisted or {}).get("session") or {}).get("round_results") or {}),
                committed_message_ids=list(dict((persisted or {}).get("session") or {}).get("committed_message_ids") or []),
                message_round_ids=dict(dict((persisted or {}).get("session") or {}).get("message_round_ids") or {}),
            )
            self._managed_by_id[resolved_id] = managed
            managed.cancel_event.parent = external_cancel
            # Import stable IDs from pre-inbox transcripts. This also repairs
            # an interrupted migration without needing a second message format.
            for node in managed.context.nodes.values():
                for attachment in getattr(node.step, "attachments", []):
                    message_id = str(attachment.get("message_id") or dict(attachment.get("payload") or {}).get("message_id") or "")
                    if message_id and message_id not in managed.committed_message_ids:
                        managed.committed_message_ids.append(message_id)
            # Functional runs use unique names in Runner capability dispatch.
            # If a caller deliberately reuses one name, only the newest entry
            # is addressable by name; both remain available by immutable id.
            self._managed_id_by_name[resolved_name] = resolved_id

            try:
                if self.callbacks.bind_runtime is not None:
                    self.callbacks.bind_runtime(managed, managed.cancel_event)
                if restored_session is not None:
                    # The transcript is restored, but its old system prompt
                    # must not override the freshly resolved tool declaration
                    # or the current mode's permissions. Reuse Agent's public
                    # refresh hooks after binding the effective Runner context.
                    for method in ("refresh_system_prompt", "refresh_executor_tools"):
                        refresh = getattr(instance, method, None)
                        if callable(refresh):
                            refresh()
                self.persist(managed)
            except Exception:
                self._managed_by_id.pop(resolved_id, None)
                if self._managed_id_by_name.get(resolved_name) == resolved_id:
                    self._managed_id_by_name.pop(resolved_name, None)
                raise
            self._emit_agent_state(managed)
            logger.info(
                "agent_acquired runner_id=%s agent_id=%s agent_name=%s role=%s lifecycle=%s restored=%s",
                self.runner_id,
                managed.agent_id,
                managed.agent_name,
                managed.role,
                managed.lifecycle.value,
                persisted is not None or session_state is not None,
            )
            if self._activated and managed.lifecycle is AgentLifecycle.PERSISTENT:
                self._ack_inbox(managed)
                self._schedule(managed)
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
    def deserialize_snapshot(snapshot: Mapping[str, Any]) -> ContextTree:
        """Decode a validated snapshot session for read-only recovery tooling."""

        normalized = JsonAgentSnapshotStore._validate(snapshot)
        session = AgentManager._session_from_state(normalized["session"])
        assert session is not None  # Snapshot validation always supplies a session mapping.
        return session

    def persist(self, managed: ManagedAgent | str) -> AgentSnapshot:
        """Checkpoint a session and lifecycle state without retaining Runner state."""

        item = self.get(managed) if isinstance(managed, str) else managed
        with self._lock:
            item.updated_at = time.time()
            if item.ephemeral:
                return self._snapshot_for(item)
            snapshot = self.snapshot_store.save(self._snapshot_for(item))
            item._persisted_message_ids = set(item.committed_message_ids)
            if self.callbacks.after_persist is not None:
                try:
                    self.callbacks.after_persist(snapshot)
                except Exception:
                    # Observers run after the storage commit. Their failure
                    # cannot roll memory back over a durable terminal result.
                    logger.exception("agent_after_persist_failed agent_id=%s", item.agent_id)
            logger.debug(
                "agent_checkpointed runner_id=%s agent_id=%s status=%s steps=%s",
                self.runner_id,
                item.agent_id,
                item.status.value,
                len(item.context.to_linear_steps()),
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
                if not self.cancel_round(str(item.round_checkpoint.get("round_id") or ""), reason=reason):
                    continue
                interrupted.append(item.agent_id)
                logger.info(
                    "agent_interrupt_requested runner_id=%s agent_id=%s reason=%s",
                    self.runner_id,
                    item.agent_id,
                    reason,
                )
            return interrupted

    def _raise_if_interrupted(self, managed: ManagedAgent) -> None:
        if managed.external_cancel is not None and managed.external_cancel.is_set():
            raise AgentExecutionInterrupted("caller_cancelled")
        if managed.cancel_event.is_set():
            raise AgentExecutionInterrupted(
                self._interrupt_reasons.get(managed.agent_id, "agent_interrupted")
            )

    @staticmethod
    def _format_task_log_content(task: str, attachments: list[RuntimeAttachment]) -> str:
        serialized = serialize_runtime_attachments_text(attachments)
        return task if not serialized else f"{task}\n\n{serialized}"

    def _log_input(
        self, item: ManagedAgent, title: str, task: str, attachments: list[RuntimeAttachment],
    ) -> None:
        """Write optional Agent diagnostics without changing a durable handoff.

        Scripted/custom Agents need not implement pretty logging. Real Agents
        reuse their existing file logger, while formatting or storage failures
        remain diagnostic failures rather than failed message acceptance.
        """
        pretty_log = getattr(item.instance, "_pretty_log", None)
        if not callable(pretty_log):
            return
        try:
            pretty_log(title, self._format_task_log_content(task, attachments))
        except Exception:
            logger.exception("agent_input_log_failed runner_id=%s agent_id=%s title=%s",
                             self.runner_id, item.agent_id, title)

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

    async def _aperform_step(self, managed: ManagedAgent, step_index: int) -> AsyncIterator[Any]:
        """
        执行 Agent 的 step() 方法，返回流式事件的生成器。

        Yields:
            - ActionEvent: 统一的流式事件
        """
        from juice_agents.core.agent.context import ActionEvent

        instance = managed.instance
        if instance is None:
            raise RuntimeError(f"agent 已释放，不能执行: {managed.agent_id}")
        def prepare() -> ActionStep:
            self._raise_if_interrupted(managed)
            instance._active_step_num = step_index
            instance.event_handler = lambda event: self._emit_internal_event(managed, event)
            step = ActionStep(step_num=step_index, model_output="")
            step.round_id = managed.round_checkpoint["round_id"]
            step.step_id = f"{step.round_id}:step:{step_index}"
            managed._current_step = step
            self._emit_round_event(managed, {"kind": "step_start", "step_num": step_index,
                "data": {"step_id": step.step_id}})

            # 运行时回调属于 AgentManager；模型只返回 usage，不接收记录器。
            instance.model_manager = self.model_manager
            instance.model_usage_handler = lambda **kwargs: self._record_model_result(managed, **kwargs)
            return step

        step = await blocking(prepare)
        try:
            self._raise_if_interrupted(managed)
            from ...agent.agents import MultiStepAgent

            async def events():
                if isinstance(instance, MultiStepAgent) and type(instance).step is MultiStepAgent.step:
                    async with aclosing(instance.astep(step)) as native_events:
                        async for event in native_events:
                            yield event
                else:
                    # 自定义同步 Agent 保留整个生成器的线程亲和性。
                    def legacy():
                        result = instance.step(step)
                        if isinstance(result, ActionStep):
                            yield ActionEvent(step_num=step_index, event_type="step_complete", data={"step": result})
                        else:
                            yield from result
                    async with aclosing(iterate_blocking(legacy, on_cancel=managed.cancel_event.set)) as legacy_events:
                        async for event in legacy_events:
                            yield event

            async with aclosing(events()) as stream:
                async for event in stream:
                    if isinstance(event, ActionEvent) and event.event_type == "candidates":
                        # This internal boundary runs before the generator resumes
                        # into tool/code execution.  Persist every proposal first;
                        # subsequent errors must update the selected step itself.
                        step = await blocking(self._record_candidates, managed, event.data)
                        managed._current_step = step
                        continue
                    # 透传所有 ActionEvent
                    yield event
                    # 最后一个事件应该是 step_complete
                    if isinstance(event, ActionEvent) and event.event_type == "step_complete":
                        # 返回完整的 ActionStep 用于后续处理
                        return
            # 如果没有收到 step_complete，返回原始 step
            return
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
            failed_step = self._normalize_failed_step(instance, step, exc=exc)
            # 对于异常情况，也需要包装为 ActionEvent
            yield ActionEvent(
                step_num=step_index,
                event_type="step_complete",
                data={"step": failed_step},
            )
            return

    def _record_model_result(
        self, managed: ManagedAgent, *, kind: str, model: Any,
        response: Any = None, error: Exception | None = None,
    ) -> None:
        """接收 Agent/Strategy 显式上报，异常结果也保留已返回的用量。"""
        self.model_manager.record(agent_id=managed.agent_id, agent_name=managed.agent_name,
                                  kind=kind, model=model, response=response, error=error)
        logger.debug("模型用量已记录: agent=%s kind=%s", managed.agent_name, kind)

    def model_usage_records(self) -> list[dict[str, Any]]:
        """返回独立账本快照；不从 ContextTree 重复累计步骤 usage。"""
        return self.model_manager.model_usage_records()

    def round_result(self, agent_id: str, round_id: str) -> RoundResult | None:
        """读取已提交的指定回合结果；后续回合不覆盖历史结果。"""
        with self._lock:
            result = self.get(agent_id).round_results.get(round_id)
            return RoundResult.from_dict(result) if result is not None else None

    def token_usage(self) -> dict[str, Any]:
        """读取本 Manager 所属 Runner 的账本，无需实例化或恢复 Agent。"""
        return self.model_manager.token_usage()

    def _record_candidates(self, managed: ManagedAgent, data: Mapping[str, Any]) -> ActionStep:
        """Save sibling proposals while advancing only the selected branch.

        Agent generation stays side-effect free.  Manager owns the transition
        from generated outputs to durable nodes and checkpoints it before the
        selected proposal is allowed to execute.
        """
        instance = managed.instance
        if instance is None:
            raise RuntimeError(f"agent 已释放，不能保存候选: {managed.agent_id}")
        steps = list(data["steps"])
        scores = list(data["scores"])
        selected_index = data["selected_index"]
        if (
            not steps
            or not all(isinstance(candidate, ActionStep) for candidate in steps)
            or len(scores) != len(steps)
            or isinstance(selected_index, bool)
            or not isinstance(selected_index, int)
            or not 0 <= selected_index < len(steps)
        ):
            raise ValueError("候选事件缺少有效的 steps、scores 或 selected_index")

        for candidate in steps:
            candidate.round_id = managed.round_checkpoint["round_id"]
            candidate.step_id = f"{candidate.round_id}:step:{candidate.step_num}"
        tree = instance.context
        parent_id = tree.active_leaf_ids[0] if tree.active_leaf_ids else tree.root_id or None
        if parent_id is None:
            raise ValueError("保存候选前必须先创建任务根节点")
        selected_node_id = ""
        for index, (candidate, score) in enumerate(zip(steps, scores)):
            node_id = f"node_{uuid4().hex[:12]}"
            tree.add_node(
                ContextNode(
                    node_id=node_id,
                    step=candidate,
                    status=NodeStatus.ACTIVE if index == selected_index else NodeStatus.REJECTED,
                    score=score,
                    generation_strategy=str(data.get("strategy") or ""),
                    generation_params={"breadth": int(data.get("breadth") or len(steps)), "candidate_index": index},
                    annotations={"selected": index == selected_index},
                ),
                parent_id=parent_id,
            )
            if index == selected_index:
                selected_node_id = node_id
        tree.active_leaf_ids = [selected_node_id]
        managed.context = tree
        self.persist(managed)
        logger.info(
            "agent_candidates_selected runner_id=%s agent_id=%s candidates=%s selected=%s",
            self.runner_id, managed.agent_id, len(steps), selected_index,
        )
        return steps[selected_index]

    def _emit_round_event(self, item: ManagedAgent, payload: dict[str, Any]) -> None:
        """Publish a lightweight typed envelope; private workers never escape."""
        if item.ephemeral or self.callbacks.on_event is None:
            return
        payload = dict(payload)
        kind = payload.pop("kind")
        step_num = payload.pop("step_num", None)
        round_id = payload.pop("round_id", item.round_checkpoint.get("round_id"))
        data = payload.pop("data", payload)
        event = {
            "kind": kind, "data": data, "event_id": f"event_{uuid4().hex}",
            "timestamp": time.time(), "runner_id": self.runner_id,
            "agent_id": item.agent_id, "agent_name": item.agent_name,
            "agent_role": item.role, "round_id": round_id, "step_num": step_num,
        }
        try:
            self.callbacks.on_event(item, event)
        except Exception:
            # Transport listeners cannot rewrite a successfully saved result.
            logger.exception("agent_event_listener_failed runner_id=%s agent_id=%s", self.runner_id, item.agent_id)

    def _emit_agent_state(self, item: ManagedAgent) -> None:
        checkpoint = item.round_checkpoint
        self._emit_round_event(item, {"kind": "agent_state", "data": {
            "status": item.status.value, "lifecycle": item.lifecycle.value,
            "is_root": item.is_root,
            "round_checkpoint": {key: deepcopy(checkpoint[key]) for key in (
                "round_id", "status", "next_step", "max_steps", "cancel_requested"
            ) if key in checkpoint},
        }})

    def _emit_internal_event(self, item: ManagedAgent, event: Any) -> None:
        if event.event_type in {
            "thinking", "model_start", "model_end", "tool_start", "tool_end", "workflow_progress"
        }:
            self._emit_round_event(item, {"kind": event.event_type,
                "step_num": event.step_num, "data": deepcopy(event.data)})

    @staticmethod
    def _result(item: ManagedAgent) -> RoundResult | None:
        value = item.round_checkpoint.get("result")
        return RoundResult.from_dict(value) if isinstance(value, dict) else None

    @staticmethod
    def _step_output(step: ActionStep | None) -> Any:
        if step is None:
            return None
        return step.output if step.output is not None else observations_to_text(step.observations) or step.model_output

    def _commit_result(self, item: ManagedAgent, result: RoundResult) -> RoundResult:
        """Commit once, before publishing. Cancellation intent is not terminal."""
        previous = self._result(item)
        if previous is not None:
            return previous
        # Reserve the next input's identity before committing this terminal
        # result. If this mandatory write fails there is no committed ending
        # to hide; if the process dies here recovery excludes the reassigned
        # input from the still-active round.
        self._defer_inbox(item)
        old_checkpoint, old_status = deepcopy(item.round_checkpoint), item.status
        old_results = deepcopy(item.round_results)
        item.round_checkpoint.update(status=result.status, result=result.to_dict(),
                                     reason=result.reason, ended_at=time.time())
        item.round_results[result.round_id] = result.to_dict()
        # Functional lifetime is part of the same durable ending. Resource
        # cleanup after this point needs no second write that could suppress
        # the already committed public result.
        item.status = AgentStatus.RELEASED if item.lifecycle is AgentLifecycle.FUNCTIONAL else AgentStatus.IDLE
        try:
            self.persist(item)
        except Exception:
            item.round_checkpoint, item.status = old_checkpoint, old_status
            item.round_results = old_results
            raise
        return result

    def _publish_result(self, item: ManagedAgent, result: RoundResult) -> None:
        # This flag is intentionally in memory. A restored committed round is
        # represented by the initial snapshot rather than replayed as new work.
        if getattr(item, "_published_round_id", None) == result.round_id:
            return
        # A caller observing completion must already see a released functional
        # worker, even when its listener dequeues the event immediately.
        if item.lifecycle is AgentLifecycle.FUNCTIONAL and item.instance is not None:
            self.release(item)
        else:
            self._emit_agent_state(item)
        item._published_round_id = result.round_id
        self._emit_round_event(item, {"kind": "round_end", "data": {"result": result.to_dict()}})

    def _complete_cancelled_round(self, item: ManagedAgent, entries: list[dict[str, Any]]) -> None:
        """Record queued cancellation at the serial boundary without starting work."""
        round_id = entries[0]["round_id"]
        attachments = [entry["attachment"] for entry in entries if entry["round_id"] == round_id]
        step = TaskStep(task="", attachments=attachments)
        step.round_id = round_id
        item.context.append_step(step)
        item.round_checkpoint = {"round_id": round_id, "status": "active",
            "input_node_id": item.context.active_leaf_ids[0], "next_step": 1,
            "max_steps": int(item.instance.max_steps), "cancelled_before_start": True}
        self._commit_messages(item, attachments)
        result = self._commit_result(item, RoundResult(round_id, "cancelled",
            reason=str(entries[0].get("cancellation_reason") or "user_cancelled")))
        self._ack_inbox(item)
        self._publish_result(item, result)

    def _save_step(self, managed: ManagedAgent, step: ActionStep, step_index: int) -> None:
        """Write exactly one committed snapshot, including selected candidates."""
        step.round_id = managed.round_checkpoint["round_id"]
        step.step_id = f"{step.round_id}:step:{step_index}"
        tree = managed.context
        selected = next((node for node in tree.get_active_leaves() if node.step is step), None)
        if selected is None:
            tree.append_step(step)
        elif step.round_outcome == "failed":
            selected.status = NodeStatus.FAILED
        managed.round_checkpoint["next_step"] = step_index + 1
        self._commit_messages(managed, step.attachments)

    def _step_end(self, item: ManagedAgent, step: ActionStep) -> None:
        from ...agent.context.serialization import _serialize_step
        self._emit_round_event(item, {"kind": "step_end", "step_num": step.step_num,
            "data": {"step": _serialize_step(step),
                     "message_ids": list(item.round_checkpoint.get("message_ids") or [])}})
        if self.callbacks.after_step is not None and not item.ephemeral:
            try:
                self.callbacks.after_step(item, step)
            except Exception:
                logger.exception("agent_after_step_failed agent_id=%s", item.agent_id)

    def _finish_step(self, managed: ManagedAgent, step: ActionStep, step_index: int) -> ActionStep:
        """Atomically save final steps with the result; then publish boundaries."""
        with self._lock:
            self._raise_if_interrupted(managed)
            checkpoint = managed.round_checkpoint
            terminal = step.round_outcome in {"submitted", "failed"} or step_index > checkpoint["max_steps"]
            if not terminal:
                attachments = self._pending_attachments(managed)
                if self.callbacks.attachments_provider is not None:
                    attachments.extend(self.callbacks.attachments_provider(managed, step_index) or [])
                step.attachments.extend(attachments)
            self._save_step(managed, step, step_index)
            result = None
            if terminal:
                status = "completed" if step.round_outcome == "submitted" and not step.error else "failed"
                reason = "submitted" if status == "completed" else (
                    "max_steps_exceeded" if step_index > checkpoint["max_steps"] and step.round_outcome == "continue" else "execution_failed")
                result = self._commit_result(managed, RoundResult(checkpoint["round_id"], status,
                    self._step_output(step), reason, str(step.error) if step.error else None))
            else:
                self.persist(managed)
            self._ack_inbox(managed)
        self._step_end(managed, step)
        if result is not None:
            self._publish_result(managed, result)
        return step

    def stream(self, managed: ManagedAgent | str, task: str, **kwargs: Any) -> Iterator[Any]:
        """同步入口适配完整异步流；所有执行状态只有 astream 一份实现。"""
        item = self.get(managed) if isinstance(managed, str) else managed
        if self._closed:
            raise RuntimeError("AgentManager 已关闭")
        if not kwargs.get("scheduled") and not str(task or "").strip():
            raise ValueError("task 不能为空")
        return _ManagedStream(iter_sync(self.astream(item, task, **kwargs)),
                              lambda: self.release(item) if item.lifecycle is AgentLifecycle.FUNCTIONAL and item.is_live else None)

    async def astream(self, managed: ManagedAgent | str, task: str, **kwargs: Any):
        async with aclosing(iter_async(self._astream(managed, task, **kwargs))) as events:
            async for event in events:
                yield event

    async def _astream(
        self,
        managed: ManagedAgent | str,
        task: str,
        *,
        task_images: list[Any] | None = None,
        reset_session: bool = False,
        attachments: list[RuntimeAttachment] | None = None,
        round_id: str | None = None,
        resume: bool = False,
        scheduled: bool = False,
    ):
        """Execute one round under the Agent's lock, resuming its saved budget.

        Scheduled persistent calls obtain all input from the inbox. Direct
        functional calls use the same checkpoint/execution code and finish
        before returning. A completed final step never remains active on disk.
        """
        from juice_agents.core.agent.context import ActionEvent

        item = self.get(managed) if isinstance(managed, str) else managed
        if self._closed:
            raise RuntimeError("AgentManager 已关闭")
        if not scheduled and not str(task or "").strip():
            raise ValueError("task 不能为空")

        await blocking(item._run_lock.acquire, on_discard=lambda _held: item._run_lock.release())
        try:
            if self._closed:
                raise RuntimeError("AgentManager 已关闭")
            if not item.is_live:
                raise RuntimeError(f"agent 已释放，不能执行: {item.agent_id}")
            instance = item.instance
            assert instance is not None
            item.cancel_event.parent = item.external_cancel
            try:
                def prepare() -> bool:
                    with self._lock:
                        if scheduled and not resume:
                            # select_work 与获得 run lock 之间，已接受的输入可能
                            # 被取消。原子收敛该取消，不能把空输入当作执行错误。
                            cancelled = [entry for entry in self._cancelled_inputs(item)
                                         if entry["round_id"] == round_id]
                            if cancelled:
                                self._complete_cancelled_input(item, cancelled)
                                return False
                        if resume:
                            if item.round_checkpoint.get("status") != "active":
                                return
                        else:
                            item.cancel_event.clear()
                            self._interrupt_reasons.pop(item.agent_id, None)
                            if reset_session:
                                reset_tools = getattr(instance, "_reset_tool_runtime_state", None)
                                if callable(reset_tools):
                                    reset_tools()
                                instance.context.nodes.clear()
                                instance.context.all_paths.clear()
                                instance.context.active_leaf_ids.clear()
                                instance.context.root_id = ""
                                instance.context.selected_path_id = None
                                instance.context.stats.clear()
                            initial = [*list(attachments or []), *self._pending_attachments(item)]
                            if scheduled and not initial:
                                return
                            if scheduled:
                                first = next(entry for entry in self._load_inbox(item.agent_id)
                                             if entry["message_id"] == initial[0]["message_id"])
                                if first["round_id"] != round_id:
                                    return
                            normalized_task = str(task or "")
                            images = list(task_images or [])
                            if scheduled:
                                # All dynamic inputs remain attachments too,
                                # so recovery can deduplicate their identities.
                                texts = [str(value.get("payload", {}).get("text") or "") for value in initial]
                                normalized_task = "\n".join(text for text in texts if text).strip()
                                # Scheduled multimodal input stays with its
                                # attachment, including messages received later
                                # at a boundary. Content rendering emits images.
                                images = []
                            task_step = TaskStep(task=normalized_task, task_images=images, attachments=initial)
                            if not instance.context.nodes:
                                instance.context.add_node(ContextNode(node_id="root", step=task_step, status=NodeStatus.ACTIVE))
                                instance.context.active_leaf_ids = ["root"]
                            else:
                                instance.context.append_step(task_step)
                            item.context = instance.context
                            item.round_checkpoint = {
                                "round_id": round_id or f"round_{uuid4().hex}", "status": "active",
                                "input_node_id": instance.context.active_leaf_ids[0],
                                "next_step": 1, "max_steps": int(instance.max_steps),
                                "started_at": time.time(),
                            }
                            item._current_step = None
                            task_step.round_id = item.round_checkpoint["round_id"]
                            self._commit_messages(item, initial)
                        item.status = AgentStatus.RUNNING
                        instance._active_round_id = item.round_checkpoint["round_id"]
                        self.persist(item)
                        if not resume and item.lifecycle is AgentLifecycle.FUNCTIONAL:
                            # Functional calls bypass inbox acceptance. Record
                            # their saved initial input once, without repeating
                            # attachments on later boundaries or recovery.
                            self._log_input(item, "Task", task_step.task, task_step.attachments)
                        self._ack_inbox(item)
                    return True

                if not await blocking(prepare):
                    return
                if item.round_checkpoint.get("cancel_requested"):
                    self._interrupt_reasons[item.agent_id] = str(item.round_checkpoint.get("cancellation_reason") or "user_cancelled")
                    item.cancel_event.set()
                await blocking(self._emit_agent_state, item)
                # Publish the saved input, including functional calls that
                # bypass the inbox. This also recovers an accepted message
                # after a reconnect. The UI deduplicates by message_id.
                input_node = item.context.nodes.get(item.round_checkpoint.get("input_node_id"))
                input_step = input_node.step if input_node is not None else None
                await blocking(self._emit_round_event, item, {"kind": "round_start", "data": {"resumed": resume,
                    "message_ids": list(item.round_checkpoint.get("message_ids") or []),
                    "input": {"task": str(getattr(input_step, "task", "") or ""),
                              "attachments": deepcopy(getattr(input_step, "attachments", []))}}})
                logger.info("agent_round_started runner_id=%s agent_id=%s round_id=%s resumed=%s",
                            self.runner_id, item.agent_id, item.round_checkpoint["round_id"], resume)
                checkpoint = item.round_checkpoint
                # The final extra execution preserves the existing submit
                # opportunity. Its marker contains only changing state;
                # the workflow instruction belongs to the system prompt.
                for index in range(int(checkpoint["next_step"]), int(checkpoint["max_steps"]) + 2):
                    self._raise_if_interrupted(item)
                    await blocking(self._receive_at_boundary, item)
                    if index > checkpoint["max_steps"] and not checkpoint.get("budget_marker_saved"):
                        def save_budget_marker() -> None:
                            with self._lock:
                                instance.context.append_step(TaskStep(task="[runtime] remaining_steps=0", round_id=checkpoint["round_id"]))
                                checkpoint["budget_marker_saved"] = True
                                self.persist(item)
                        await blocking(save_budget_marker)
                    action_step = None
                    item._current_step = None
                    async for event in self._aperform_step(item, index):
                        if isinstance(event, ActionEvent):
                            if event.event_type != "step_complete":
                                await blocking(self._emit_internal_event, item, event)
                                yield event
                            elif event.event_type == "step_complete":
                                action_step = event.data.get("step")
                        elif isinstance(event, ActionStep):
                            action_step = event
                    if action_step is None:
                        raise RuntimeError(f"step {index} 未返回有效的 ActionStep")
                    action_step = await blocking(self._finish_step, item, action_step, index)
                    item._current_step = None
                    yield action_step
                    if checkpoint["status"] != "active":
                        break
                # No later status write is needed: _finish_step saved the
                # final outcome in the very same JSON as the final action.
            except (AgentExecutionInterrupted, GeneratorExit, asyncio.CancelledError) as exc:
                def commit_cancel() -> None:
                    with self._lock:
                        if self._result(item) is None:
                            reason = exc.reason if isinstance(exc, AgentExecutionInterrupted) else "task_cancelled" if isinstance(exc, asyncio.CancelledError) else "stream_closed"
                            step = getattr(item, "_current_step", None)
                            if step is not None:
                                step.round_outcome = "failed"
                                step.error = AgentError(reason)
                                self._save_step(item, step, step.step_num)
                            result = self._commit_result(item, RoundResult(item.round_checkpoint["round_id"],
                                "cancelled", self._step_output(step), reason))
                            if step is not None:
                                self._step_end(item, step)
                            self._publish_result(item, result)
                await blocking(commit_cancel)
                if isinstance(exc, (GeneratorExit, asyncio.CancelledError)):
                    raise
                logger.info("agent_round_interrupted runner_id=%s agent_id=%s round_id=%s",
                            self.runner_id, item.agent_id, item.round_checkpoint.get("round_id"))
            except Exception as exc:
                def commit_failure() -> None:
                    with self._lock:
                        if self._result(item) is None:
                            step = item._current_step
                            if step is not None:
                                step.round_outcome = "failed"
                                step.error = AgentError(str(exc))
                                self._save_step(item, step, step.step_num)
                            result = self._commit_result(item, RoundResult(item.round_checkpoint["round_id"],
                                "failed", self._step_output(step), reason="execution_failed", error=str(exc)))
                            if step is not None:
                                self._step_end(item, step)
                            self._publish_result(item, result)
                await blocking(commit_failure)
                raise
            finally:
                instance._active_round_id = None
                item._current_step = None
                if item.lifecycle is AgentLifecycle.FUNCTIONAL and item.is_live:
                    await blocking(self.release, item)
        finally:
            item._run_lock.release()
    def run(self, managed: ManagedAgent | str, task: str, **kwargs: Any) -> RoundResult:
        return run_async(self.arun(managed, task, **kwargs))

    async def arun(self, managed: ManagedAgent | str, task: str, **kwargs: Any) -> RoundResult:
        return await on_runtime(self._arun(managed, task, **kwargs))

    async def aacquire(self, binding: Any, **kwargs: Any) -> ManagedAgent:
        return await blocking(self.acquire, binding, **kwargs)

    async def apersist(self, managed: ManagedAgent | str) -> AgentSnapshot:
        return await blocking(self.persist, managed)

    async def arelease(self, managed: ManagedAgent | str) -> AgentSnapshot:
        return await blocking(self.release, managed)

    async def _arun(self, managed: ManagedAgent | str, task: str, **kwargs: Any) -> RoundResult:
        """Return the true result; partial/error text never implies success."""
        item = self.get(managed) if isinstance(managed, str) else managed
        previous_result = self._result(item)
        stream = self._astream(item, task, **kwargs)
        try:
            async for _ in stream:
                pass
        except Exception:
            result = self._result(item)
            if result is None or (previous_result is not None and result.round_id == previous_result.round_id):
                raise
        finally:
            await stream.aclose()
        result = self._result(item)
        if result is None:
            raise RuntimeError("round ended without a committed result")
        return result

    def release(self, managed: ManagedAgent | str) -> AgentSnapshot:
        """Drop the live instance after persisting its transcript exactly once."""

        item = self.get(managed) if isinstance(managed, str) else managed
        with self._lock:
            if item.status is AgentStatus.RELEASED and item.instance is None:
                return self._snapshot_for(item)
            if item.status is AgentStatus.RELEASED:
                snapshot = self._snapshot_for(item)
            else:
                previous_status = item.status
                item.status = AgentStatus.RELEASED
                try:
                    snapshot = self.persist(item)
                except Exception:
                    item.status = previous_status
                    raise
            self._emit_agent_state(item)
            item.instance = None
            self._managed_by_id.pop(item.agent_id, None)
            if self._managed_id_by_name.get(item.agent_name) == item.agent_id:
                self._managed_id_by_name.pop(item.agent_name, None)
            if self.callbacks.after_release is not None:
                try:
                    self.callbacks.after_release(item)
                except Exception:
                    logger.exception("agent_release_cleanup_failed agent_id=%s", item.agent_id)
            logger.info(
                "agent_released runner_id=%s agent_id=%s agent_name=%s",
                self.runner_id,
                item.agent_id,
                item.agent_name,
            )
            return snapshot

    def describe_sessions(self, agent_id: str | None = None) -> dict[str, Any]:
        """Return live-or-persisted transcript snapshots without resuming work."""

        snapshots_by_id = {snapshot["agent_id"]: snapshot for snapshot in self.snapshot_store.list()}
        with self._lock:
            for managed in self._managed_by_id.values():
                if managed.ephemeral:
                    continue
                snapshots_by_id[managed.agent_id] = self._snapshot_for(managed)
            # A disconnected observer may miss acceptance while the Agent is
            # still in a slow model/tool call. Project the durable queue too;
            # a context-only history cannot yet contain these inputs. Reading
            # this view never acquires an Agent or acknowledges its messages.
            pending_by_id = {}
            for snapshot_id, snapshot in snapshots_by_id.items():
                if agent_id is not None and snapshot_id != agent_id:
                    continue
                committed = set(snapshot.get("session", {}).get("committed_message_ids") or [])
                pending_by_id[snapshot_id] = [deepcopy(entry["attachment"])
                                              for entry in self._load_inbox(snapshot_id)
                                              if entry["message_id"] not in committed]
        agents = sorted(
            (snapshot for snapshot in snapshots_by_id.values() if agent_id is None or snapshot["agent_id"] == agent_id),
            key=lambda snapshot: (not bool(snapshot.get("is_root")), str(snapshot.get("agent_name") or "")),
        )
        return {
            "runner_id": self.runner_id,
            "agents": [
                {
                    **dict(snapshot),
                    "agent_role": snapshot["role"],
                    "agent_kind": snapshot["lifecycle"],
                    "current_async_task_id": str(snapshot.get("metadata", {}).get("async_task_id") or ""),
                    "pending_messages": pending_by_id[snapshot["agent_id"]],
                    # session.json 保存 ContextTree 节点；扁平 steps 仅是展示层
                    # 投影。冷读持久化快照即可导出历史，无需 acquire/执行 Agent。
                    "steps": self._history_for(snapshot),
                }
                for snapshot in agents
            ],
        }

    @classmethod
    def _history_for(cls, snapshot: AgentSnapshot) -> list[dict[str, Any]]:
        """Interleave saved outcomes without inferring them from step text.

        A cancelled-before-start round has a TaskStep but no ActionStep; a
        failed round may later be followed by successful work. Both remain
        inspectable after restart, independently of the latest checkpoint.
        """
        steps = serialize_context_transcript(cls.deserialize_snapshot(snapshot))
        results = dict(snapshot.get("session", {}).get("round_results") or {})
        last_indices = {step.get("round_id"): index for index, step in enumerate(steps) if step.get("round_id")}
        history: list[dict[str, Any]] = []
        emitted: set[str] = set()
        for index, step in enumerate(steps):
            history.append(step)
            round_id = step.get("round_id")
            if round_id in results and last_indices[round_id] == index:
                history.append({"type": "round_result", "round_id": round_id, "result": deepcopy(results[round_id])})
                emitted.add(round_id)
        history.extend({"type": "round_result", "round_id": round_id, "result": deepcopy(result)}
                       for round_id, result in results.items() if round_id not in emitted)
        return history


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
