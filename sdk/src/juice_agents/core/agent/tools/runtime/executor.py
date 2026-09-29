"""Unified execution contract for agent tools.

The executor is deliberately independent from ReAct and CodeAct.  Both
adapters can resolve their model-produced calls into :class:`ResolvedToolAction`
and hand the actions to this module.  Keeping permission checks, scheduling,
normalisation and audit records here prevents the two agent loops from slowly
developing different semantics again.

The implementation uses a small, bounded worker pool for adjacent
``PARALLEL_SAFE`` actions.  Serial and barrier actions run on the caller's
thread after waiting for all prior work, which also preserves the
manager-request thread affinity used by Runner.  A resource-key lock is held for the complete
call, so two tools that touch the same logical resource never overlap.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import Enum
import asyncio
import concurrent.futures
import inspect
import logging
import threading
import time
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence
from uuid import uuid4

from juice_agents.core.runner.execution.cancellation import StreamCancelled

from .base_tools import Tool

logger = logging.getLogger(__name__)


class ToolExecutionMode(str, Enum):
    """Scheduling mode selected by a tool, never by the language model."""

    SERIAL = "serial"
    PARALLEL_SAFE = "parallel_safe"
    BARRIER = "barrier"
    BACKGROUND = "background"


@dataclass(frozen=True, slots=True)
class ToolExecutionPolicy:
    """A tool's runtime scheduling constraints.

    ``resource_keys`` are logical, process-local locks.  They intentionally do
    not imply a permission grant: a tool may only tighten its own constraints
    based on validated business arguments.
    """

    mode: ToolExecutionMode
    thread_affinity: Literal["any", "main"] = "any"
    resource_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        mode = self.mode
        if not isinstance(mode, ToolExecutionMode):
            try:
                mode = ToolExecutionMode(str(mode).strip().lower())
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid tool execution mode: {mode!r}") from exc
            object.__setattr__(self, "mode", mode)
        affinity = str(self.thread_affinity)
        if affinity not in {"any", "main"}:
            raise ValueError("thread_affinity must be 'any' or 'main'")
        if affinity != self.thread_affinity:
            object.__setattr__(self, "thread_affinity", affinity)
        keys = tuple(dict.fromkeys(sorted(str(key).strip() for key in self.resource_keys if str(key).strip())))
        if keys != self.resource_keys:
            object.__setattr__(self, "resource_keys", keys)


@dataclass(slots=True)
class ToolExecutionContext:
    """Context passed to policy resolution and tool execution.

    The fields are intentionally callback-oriented.  This avoids coupling the
    runtime package to Agent or Runner implementations while still allowing
    the executor to perform fail-closed permission, plan-mode and sandbox
    checks before submitting work to a worker.
    """

    agent: Any | None = None
    runner_context: Any | None = None
    # ``runner``/``permission_engine``/``sandbox_guard`` are convenience
    # aliases for adapters that do not expose a full RunnerContext object.
    runner: Any | None = None
    # 权限维度与执行模式维度分开传递：permission_mode 只表达 default/accept 的
    # 审批强度，agent_mode 只表达 agent/plan/team/group 的执行模式。工具的
    # check_permissions() 同时收到两者，不再从单个字段里反解。
    permission_mode: str | None = None
    agent_mode: str | None = None
    permission_engine: Any | None = None
    permission_check: Callable[[Tool, dict[str, Any]], Any] | None = None
    permission_guard: Callable[[Tool, dict[str, Any]], Any] | None = None
    plan_check: Callable[[Tool, dict[str, Any]], Any] | None = None
    sandbox_check: Callable[[Tool, dict[str, Any]], Any] | None = None
    sandbox_guard: Callable[[Tool, dict[str, Any]], Any] | None = None
    sandbox: Any | None = None
    background_launcher: Callable[..., Any] | None = None
    cancel_event: threading.Event | None = None
    cancel_token: threading.Event | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.runner_context is None and self.runner is not None:
            self.runner_context = self.runner
        if self.runner is None:
            self.runner = getattr(self.runner_context, "runner", None)
        if self.permission_check is None and self.permission_guard is not None:
            self.permission_check = self.permission_guard
        if self.sandbox_check is None and self.sandbox_guard is not None:
            self.sandbox_check = self.sandbox_guard
        if self.cancel_event is None and self.cancel_token is not None:
            self.cancel_event = self.cancel_token
        if self.cancel_event is None:
            candidate = getattr(self.runner_context, "cancel_event", None)
            if isinstance(candidate, threading.Event):
                self.cancel_event = candidate
            elif self.agent is not None:
                candidate = getattr(self.agent, "_runner_cancel_event", None)
                if callable(candidate):
                    try:
                        candidate = candidate()
                    except Exception:  # pragma: no cover - defensive adapter hook
                        candidate = None
                if isinstance(candidate, threading.Event):
                    self.cancel_event = candidate

    def is_cancelled(self) -> bool:
        event = self.cancel_event
        return bool(event is not None and event.is_set())

    def check_cancelled(self) -> None:
        """Raise a small, dependency-free cancellation error when requested."""

        if self.is_cancelled():
            raise ToolExecutionCancelled("tool execution cancelled")


class ToolExecutionCancelled(RuntimeError):
    """Raised internally when an action is cancelled before or during a call."""


@dataclass(frozen=True, slots=True)
class ResolvedToolAction:
    """A validated tool call ready for scheduling.

    ``order`` is the model action index.  It is kept separate from ``call_id``
    because call IDs are runtime-owned and must never be supplied by a model.
    """

    tool: Tool
    args: Mapping[str, Any] = field(default_factory=dict)
    order: int = 0
    max_observation_chars: int | None = None
    context: ToolExecutionContext | None = None
    raw: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool, Tool):
            raise TypeError("ResolvedToolAction.tool must be a Tool instance")
        raw_args = {} if self.args is None else self.args
        if not isinstance(raw_args, Mapping):
            raise TypeError("ResolvedToolAction.args must be a mapping")
        object.__setattr__(self, "args", dict(raw_args))
        if isinstance(self.order, bool) or not isinstance(self.order, int) or self.order < 0:
            raise ValueError("action order must be a non-negative integer")
        limit = self.max_observation_chars
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
        ):
            raise ValueError("max_observation_chars must be a positive integer")

    @property
    def name(self) -> str:
        return str(getattr(self.tool, "name", "") or type(self.tool).__name__)

    @property
    def tool_name(self) -> str:
        return self.name

    @property
    def target(self) -> Tool:
        """Compatibility alias for adapters that call the resolved tool target."""

        return self.tool


@dataclass(slots=True)
class ToolExecutionResult:
    """Stable result/audit record emitted for every resolved action."""

    call_id: str
    order: int
    tool_name: str
    status: str
    observation: Any = None
    error: str | None = None
    task_id: str | None = None
    policy: ToolExecutionPolicy | None = None
    duration_seconds: float | None = None
    observation_images: list[Any] = field(default_factory=list)
    action: ResolvedToolAction | None = None
    started_at: float | None = None
    finished_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def result(self) -> Any:
        """Alias used by adapters that call the raw observation a result."""

        return self.observation

    @property
    def name(self) -> str:
        """Compatibility alias for the action/tool name."""

        return self.tool_name

    @property
    def execution_mode(self) -> ToolExecutionMode:
        """Resolved framework-owned scheduling mode.

        Guard failures can happen before policy resolution. They still need a
        stable serial audit projection rather than a nullable field.
        """

        return self.policy.mode if self.policy is not None else ToolExecutionMode.SERIAL

    @property
    def thread_affinity(self) -> str:
        return self.policy.thread_affinity if self.policy is not None else "any"

    @property
    def resource_keys(self) -> tuple[str, ...]:
        return self.policy.resource_keys if self.policy is not None else ()

    @property
    def observations(self) -> list[Any]:
        """Plural alias matching session step terminology."""

        if self.observation is None:
            return []
        return self.observation if isinstance(self.observation, list) else [self.observation]

    @property
    def task_handle(self) -> str | None:
        return self.task_id

    @property
    def async_task_id(self) -> str | None:
        return self.task_id

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"

    @property
    def ok(self) -> bool:
        return self.status in {"completed", "background"}

    @property
    def succeeded(self) -> bool:
        """Alias used by Agent execution records."""

        return self.ok

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly audit projection (policy object omitted)."""

        return {
            "call_id": self.call_id,
            "order": self.order,
            "tool_name": self.tool_name,
            "name": self.tool_name,
            "status": self.status,
            "succeeded": self.ok,
            "execution_mode": self.execution_mode.value,
            "mode": self.execution_mode.value,
            "thread_affinity": self.thread_affinity,
            "resource_keys": list(self.resource_keys),
            "observation": self.observation,
            "error": self.error,
            "task_id": self.task_id,
            "duration_seconds": self.duration_seconds,
            "observation_images": list(self.observation_images),
            "metadata": dict(self.metadata),
        }


# ``ToolExecutionRecord`` is a descriptive alias used by audit adapters; the
# canonical public type remains ``ToolExecutionResult``.
ToolExecutionRecord = ToolExecutionResult


class _ToolExecutionEngine:
    """Private scheduling helper owned exclusively by ``ToolManager``.

    ``tools`` may be a mapping or any iterable of tools.  A caller can also
    pass an already-resolved ``ResolvedToolAction`` to :meth:`execute`.
    """

    _FORBIDDEN_MODEL_FIELDS = frozenset(
        {
            "execution",
            "execution_mode",
            "async_label",
            "thread",
            "thread_affinity",
            "worker",
            "workers",
            "max_workers",
            "resource_keys",
            "resource_key",
            "thread_name",
            "worker_count",
            "mode",
            "call_id",
        }
    )
    # Resource keys name process-local logical resources. Separate executors
    # can belong to separate agents while still touching the same file,
    # browser session, or registry, so their locks must be shared.
    _process_resource_locks: dict[str, threading.Lock] = {}
    _process_resource_locks_guard = threading.Lock()

    def __init__(
        self,
        tools: Mapping[str, Tool] | Iterable[Tool] | None = None,
        *,
        context: ToolExecutionContext | None = None,
        runner_context: Any | None = None,
        agent: Any | None = None,
        max_workers: int = 8,
        strict_policies: bool = True,
        permission_check: Callable[[Tool, dict[str, Any]], Any] | None = None,
        plan_check: Callable[[Tool, dict[str, Any]], Any] | None = None,
        sandbox_check: Callable[[Tool, dict[str, Any]], Any] | None = None,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError("max_workers must be a positive integer")
        self.agent = agent
        self.max_workers = max_workers
        self.strict_policies = bool(strict_policies)
        self.context = context or ToolExecutionContext(
            agent=agent,
            runner_context=runner_context,
            permission_check=permission_check,
            plan_check=plan_check,
            sandbox_check=sandbox_check,
        )
        # Explicit constructor hooks win over hooks supplied by a context.
        if permission_check is not None:
            self.context.permission_check = permission_check
        if plan_check is not None:
            self.context.plan_check = plan_check
        if sandbox_check is not None:
            self.context.sandbox_check = sandbox_check
        self._tools: dict[str, Tool] = {}
        if isinstance(tools, Mapping):
            for name, tool in tools.items():
                self.register(str(name), tool)
        elif tools is not None:
            for tool in tools:
                self.register(getattr(tool, "name", type(tool).__name__), tool)
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="juice-tool",
        )
        self._call_counter = 0
        self._call_counter_lock = threading.Lock()
        self._records: list[ToolExecutionResult] = []
        self._records_lock = threading.Lock()
        self._closed = False

    def register(self, name: str, tool: Tool) -> Tool:
        """Register one tool and optionally enforce explicit policy metadata."""

        if not isinstance(tool, Tool):
            raise TypeError("tool execution engine can only register Tool instances")
        if self.strict_policies and not type(tool).has_explicit_execution_policy():
            raise TypeError(
                f"工具 {getattr(tool, 'name', name)!r} 未声明 execution_policy"
            )
        normalized_name = str(name or getattr(tool, "name", "")).strip()
        if not normalized_name:
            raise ValueError("tool name must not be empty")
        self._tools[normalized_name] = tool
        return tool

    @property
    def tools(self) -> Mapping[str, Tool]:
        return dict(self._tools)

    @property
    def records(self) -> list[ToolExecutionResult]:
        with self._records_lock:
            return list(sorted(self._records, key=lambda item: item.order))

    @property
    def execution_records(self) -> list[ToolExecutionResult]:
        return self.records

    def close(self, *, wait: bool = True) -> None:
        if not self._closed:
            self._closed = True
            self._pool.shutdown(wait=wait, cancel_futures=True)

    def __enter__(self) -> "_ToolExecutionEngine":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _next_call_id(self) -> str:
        # Prefix plus monotonic sequence makes records easy to correlate while
        # UUID entropy prevents collisions across executor instances.
        with self._call_counter_lock:
            self._call_counter += 1
            sequence = self._call_counter
        return f"tool_call_{sequence}_{uuid4().hex[:10]}"

    def _reject_model_fields(self, payload: Mapping[str, Any]) -> None:
        forbidden = sorted(self._FORBIDDEN_MODEL_FIELDS.intersection(payload))
        if forbidden:
            raise ValueError(
                "model cannot control tool execution fields: " + ", ".join(forbidden)
            )

    def resolve(
        self,
        action: ResolvedToolAction | Mapping[str, Any] | Tool,
        *,
        order: int = 0,
        context: ToolExecutionContext | None = None,
    ) -> ResolvedToolAction:
        """Resolve a model/action payload without executing it."""

        if isinstance(action, ResolvedToolAction):
            # Resolved actions are normally produced by a trusted adapter, but
            # CodeAct can construct one from model-controlled Python kwargs.
            # Re-apply the protocol boundary here so adapters cannot bypass
            # the reserved execution fields checked for raw mappings.
            self._reject_model_fields(action.args)
            if action.raw is not None:
                self._reject_model_fields(action.raw)
            if context is not None and action.context is None:
                return ResolvedToolAction(
                    tool=action.tool,
                    args=action.args,
                    order=action.order,
                    max_observation_chars=action.max_observation_chars,
                    context=context,
                    raw=action.raw,
                )
            return action
        if isinstance(action, Tool):
            return ResolvedToolAction(tool=action, args={}, order=order, context=context)
        if not isinstance(action, Mapping):
            raise TypeError("tool action must be a mapping, Tool, or ResolvedToolAction")
        self._reject_model_fields(action)
        raw_name = action.get("name", action.get("tool"))
        tool: Tool | None
        if isinstance(raw_name, Tool):
            tool = raw_name
        else:
            tool = self._tools.get(str(raw_name or "").strip())
        if tool is None:
            raise KeyError(f"unknown tool: {raw_name!r}")
        raw_args = action.get("args", {})
        if raw_args is None:
            raw_args = {}
        if not isinstance(raw_args, Mapping):
            raise TypeError("tool action args must be a mapping")
        self._reject_model_fields(raw_args)
        max_limit = action.get("max_observation_chars")
        if max_limit is not None:
            if isinstance(max_limit, bool) or not isinstance(max_limit, int) or max_limit <= 0:
                raise ValueError("max_observation_chars must be a positive integer")
        return ResolvedToolAction(
            tool=tool,
            args=dict(raw_args),
            order=order,
            max_observation_chars=max_limit,
            context=context,
            raw=dict(action),
        )

    def resolve_actions(
        self,
        actions: Sequence[ResolvedToolAction | Mapping[str, Any] | Tool],
        *,
        context: ToolExecutionContext | None = None,
    ) -> list[ResolvedToolAction]:
        return [self.resolve(action, order=index, context=context) for index, action in enumerate(actions)]

    def _action_context(self, action: ResolvedToolAction) -> ToolExecutionContext:
        context = action.context or self.context
        # Runner binds its context after Agent construction. Refresh the
        # dynamic reference at each action boundary so BACKGROUND tools see
        # the live async registry and cancellation token.
        if context.runner_context is None and self.agent is not None:
            runner_context = getattr(self.agent, "runner_context", None)
            if runner_context is not None:
                context.runner_context = runner_context
        if context.agent is None:
            context.agent = self.agent
        if context.cancel_event is None:
            candidate = getattr(context.runner_context, "cancel_event", None)
            if isinstance(candidate, threading.Event):
                context.cancel_event = candidate
        return context

    def _policy_for(
        self,
        action: ResolvedToolAction,
        context: ToolExecutionContext,
    ) -> ToolExecutionPolicy:
        policy = action.tool.validate_execution_policy(
            strict=self.strict_policies,
            args=dict(action.args),
            context=context,
        )
        if not isinstance(policy, ToolExecutionPolicy):
            raise TypeError(
                f"工具 {action.name!r} execution_policy must return ToolExecutionPolicy"
            )
        return policy

    @staticmethod
    def _is_denial(value: Any) -> tuple[bool, str | None]:
        if value is None:
            return False, None
        behavior = getattr(value, "behavior", None)
        if behavior is not None:
            denied = str(behavior).lower() == "deny"
            return denied, str(getattr(value, "reason", "") or "") or None
        if isinstance(value, Mapping):
            behavior = str(value.get("behavior", value.get("status", ""))).lower()
            if behavior in {"deny", "denied", "false"}:
                return True, str(value.get("reason") or "permission denied")
            if "allowed" in value and not bool(value["allowed"]):
                return True, str(value.get("reason") or "permission denied")
            return False, None
        if value is False:
            return True, "permission denied"
        return False, None

    def _guard(self, action: ResolvedToolAction, context: ToolExecutionContext) -> str | None:
        """Run all fail-closed guards before a worker is submitted."""

        args = dict(action.args)
        # 两个维度各自独立回退到 runner 上的同名属性，互不影响。
        permission_mode = str(
            context.permission_mode
            or getattr(context.runner, "permission_mode", "")
            or "default"
        )
        agent_mode = str(
            context.agent_mode
            or getattr(context.runner, "agent_mode", "")
            or "agent"
        )
        try:
            decision = action.tool.check_permissions(
                args,
                permission_mode=permission_mode,
                agent_mode=agent_mode,
                context=context,
            )
            denied, reason = self._is_denial(decision)
            if denied:
                return reason or f"tool {action.name!r} denied execution"
        except Exception as exc:
            # Permission hooks are fail-closed.  The only exception is a hook
            # explicitly returning a passthrough/allow decision above.
            if type(exc).__name__ in {"PermissionDenied", "PermissionError"}:
                return str(exc) or "permission denied"
            raise

        permission_check = context.permission_check
        if permission_check is None and context.permission_engine is not None:
            engine = context.permission_engine
            require = getattr(engine, "require", None)
            if callable(require):
                def _engine_require(tool: Tool, values: dict[str, Any]) -> Any:
                    return require(tool, values)
                permission_check = _engine_require
        if permission_check is None and context.permission_engine is not None:
            require = getattr(context.permission_engine, "require", None)
            if callable(require):
                permission_check = lambda tool, values: require(tool, values)
        if permission_check is None and context.agent is not None:
            try:
                from juice_agents.core.permissions.policy import check_agent_tool_permission

                permission_check = lambda tool, values: check_agent_tool_permission(context.agent, tool, values)
            except Exception:  # pragma: no cover - optional permissions integration
                permission_check = None
        if permission_check is not None:
            try:
                decision = permission_check(action.tool, args)
                denied, reason = self._is_denial(decision)
                if denied:
                    return reason or "permission denied"
            except Exception as exc:
                if type(exc).__name__ in {"PermissionDenied", "PermissionError"}:
                    return str(exc) or "permission denied"
                raise

        if context.plan_check is not None:
            decision = context.plan_check(action.tool, args)
            denied, reason = self._is_denial(decision)
            if denied:
                return reason or "plan mode denied execution"
        elif agent_mode == "plan" and not bool(getattr(action.tool, "is_read_only", False)):
            return f"plan mode denied execution of {action.name!r}"

        if context.sandbox_check is not None:
            decision = context.sandbox_check(action.tool, args)
            denied, reason = self._is_denial(decision)
            if denied:
                return reason or "sandbox denied execution"
        elif context.sandbox is not None:
            check = getattr(context.sandbox, "check", None)
            if callable(check):
                decision = check(action.tool, args)
                denied, reason = self._is_denial(decision)
                if denied:
                    return reason or "sandbox denied execution"
        return None

    def _cancelled(self, context: ToolExecutionContext) -> bool:
        return context.is_cancelled()

    def _resource_context(self, keys: Iterable[str]):
        normalized = tuple(dict.fromkeys(str(key) for key in keys if str(key)))
        if not normalized:
            return nullcontext()

        locks: list[threading.Lock] = []
        with self._process_resource_locks_guard:
            for key in sorted(normalized):
                locks.append(self._process_resource_locks.setdefault(key, threading.Lock()))

        class _Locks:
            def __enter__(self_nonlocal):
                for lock in locks:
                    lock.acquire()

            def __exit__(self_nonlocal, exc_type: Any, exc: Any, tb: Any):
                for lock in reversed(locks):
                    lock.release()

        return _Locks()

    def _invoke(
        self,
        action: ResolvedToolAction,
        policy: ToolExecutionPolicy,
        context: ToolExecutionContext,
        call_id: str,
    ) -> ToolExecutionResult:
        started = time.time()
        try:
            context.check_cancelled()
            if policy.mode is ToolExecutionMode.BACKGROUND:
                runner_context = context.runner_context
                if runner_context is None:
                    owner = getattr(action.tool, "owner_agent", None)
                    runner_context = getattr(owner, "runner_context", None)
                has_runner = runner_context is not None and (
                    getattr(runner_context, "runner", None) is not None
                    or getattr(runner_context, "async_task_registry", None) is not None
                    or any(
                        callable(getattr(runner_context, method, None))
                        for method in (
                            "launch_local_agent",
                            "launch_local_bash",
                            "launch_local_graph",
                            "launch_async_task",
                        )
                    )
                )
                has_runner = has_runner or context.runner is not None
                if not has_runner and context.background_launcher is None:
                    raise RuntimeError(
                        f"background tool {action.name!r} requires a Runner context"
                    )

            effective_limit = action.max_observation_chars
            limit_cm = (
                action.tool.observation_limit(effective_limit)
                if effective_limit is not None
                else nullcontext()
            )
            # Bind the cancellation-aware context for the duration of the
            # actual call.  Built-in long-running tools and third-party tools
            # that opt in through ``check_execution_cancelled`` can now react
            # while the executor is waiting for ``forward()`` to return.
            with (
                self._resource_context(policy.resource_keys),
                limit_cm,
                action.tool.execution_context_scope(context),
            ):
                if context.background_launcher is not None and policy.mode is ToolExecutionMode.BACKGROUND:
                    raw = self._call_background_launcher(
                        context.background_launcher,
                        action,
                        context,
                    )
                else:
                    raw = action.tool(**dict(action.args))
            context.check_cancelled()
            observation, images, task_id = self._normalize_output(raw)
            status = "background" if policy.mode is ToolExecutionMode.BACKGROUND else "completed"
            error: str | None = None
            receipt_status = (
                raw.get("status") if isinstance(raw, Mapping) else getattr(raw, "status", None)
            )
            receipt_error = (
                raw.get("error") if isinstance(raw, Mapping) else getattr(raw, "error", None)
            )
            receipt_summary = (
                raw.get("summary") if isinstance(raw, Mapping) else getattr(raw, "summary", None)
            )
            if (
                policy.mode is ToolExecutionMode.BACKGROUND
                and str(receipt_status or "").strip().lower() in {"failed", "error", "denied"}
            ):
                status = "failed"
                error = str(receipt_error or receipt_summary or "background task launch failed")
            elif policy.mode is ToolExecutionMode.BACKGROUND and not task_id:
                # BACKGROUND is never an implicit foreground fallback. A
                # successful launch must expose the Runner task handle so the
                # caller can observe/cancel it later.
                status = "failed"
                error = "background tool did not return a task_id"
            finished = time.time()
            return ToolExecutionResult(
                call_id=call_id,
                order=action.order,
                tool_name=action.name,
                status=status,
                observation=observation,
                error=error,
                task_id=task_id,
                policy=policy,
                duration_seconds=finished - started,
                observation_images=images,
                action=action,
                started_at=started,
                finished_at=finished,
            )
        except (
            ToolExecutionCancelled,
            StreamCancelled,
            asyncio.CancelledError,
            KeyboardInterrupt,
        ) as exc:
            finished = time.time()
            return ToolExecutionResult(
                call_id=call_id,
                order=action.order,
                tool_name=action.name,
                status="cancelled",
                error=str(exc) or "tool execution cancelled",
                policy=policy,
                duration_seconds=finished - started,
                action=action,
                started_at=started,
                finished_at=finished,
            )
        except Exception as exc:  # tool errors are observations, never executor crashes
            finished = time.time()
            logger.debug("tool %s failed", action.name, exc_info=True)
            if context.is_cancelled() or type(exc).__name__ in {"StreamCancelled", "CancelledError"}:
                return ToolExecutionResult(
                    call_id=call_id,
                    order=action.order,
                    tool_name=action.name,
                    status="cancelled",
                    error=str(exc) or "tool execution cancelled",
                    policy=policy,
                    duration_seconds=finished - started,
                    action=action,
                    started_at=started,
                    finished_at=finished,
                )
            return ToolExecutionResult(
                call_id=call_id,
                order=action.order,
                tool_name=action.name,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                policy=policy,
                duration_seconds=finished - started,
                action=action,
                started_at=started,
                finished_at=finished,
            )

    @staticmethod
    def _call_background_launcher(
        launcher: Callable[..., Any],
        action: ResolvedToolAction,
        context: ToolExecutionContext,
    ) -> Any:
        """Call an adapter launcher with a tolerant, explicit signature."""

        try:
            signature = inspect.signature(launcher)
        except (TypeError, ValueError):
            return launcher(action.tool, dict(action.args), context)
        candidates = [
            ((action.tool, dict(action.args), context), {}),
            ((action.tool, dict(action.args)), {}),
            ((), {"tool": action.tool, "args": dict(action.args), "context": context}),
            ((), {"action": action, "context": context}),
        ]
        for positional, keyword in candidates:
            try:
                signature.bind(*positional, **keyword)
            except TypeError:
                continue
            return launcher(*positional, **keyword)
        return launcher(action.tool, dict(action.args), context)

    @staticmethod
    def _normalize_output(raw: Any) -> tuple[Any, list[Any], str | None]:
        if isinstance(raw, ToolExecutionResult):
            return raw.observation, list(raw.observation_images), raw.task_id
        if isinstance(raw, Mapping):
            images = raw.get("observation_images", raw.get("images", []))
            images = list(images) if isinstance(images, (list, tuple)) else ([] if images is None else [images])
            task_id = raw.get(
                "task_id",
                raw.get("async_task_id", raw.get("task_handle", raw.get("handle"))),
            )
            return raw, images, None if task_id is None else str(task_id)
        images = getattr(raw, "observation_images", None)
        task_id = getattr(
            raw,
            "task_id",
            getattr(raw, "async_task_id", getattr(raw, "task_handle", None)),
        )
        return raw, list(images or []), None if task_id is None else str(task_id)

    def _denied_result(
        self,
        action: ResolvedToolAction,
        call_id: str,
        reason: str,
        policy: ToolExecutionPolicy | None = None,
    ) -> ToolExecutionResult:
        now = time.time()
        return ToolExecutionResult(
            call_id=call_id,
            order=action.order,
            tool_name=action.name,
            status="denied",
            error=reason,
            policy=policy,
            action=action,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
        )

    def _skipped_result(self, action: ResolvedToolAction, call_id: str) -> ToolExecutionResult:
        now = time.time()
        return ToolExecutionResult(
            call_id=call_id,
            order=action.order,
            tool_name=action.name,
            status="cancelled",
            error="tool execution cancelled before start",
            action=action,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
        )

    def execute_action(
        self,
        action: ResolvedToolAction | Mapping[str, Any] | Tool,
        *,
        order: int = 0,
        context: ToolExecutionContext | None = None,
    ) -> ToolExecutionResult:
        return self.execute([self.resolve(action, order=order, context=context)])[0]

    def execute(
        self,
        actions: Sequence[ResolvedToolAction | Mapping[str, Any] | Tool] | ResolvedToolAction | Mapping[str, Any] | Tool,
        *,
        context: ToolExecutionContext | None = None,
    ) -> list[ToolExecutionResult]:
        """Execute one or more actions, returning records in action order."""

        if isinstance(actions, (ResolvedToolAction, Tool)) or isinstance(actions, Mapping):
            action_list: Sequence[Any] = [actions]
        else:
            action_list = actions
        resolved = self.resolve_actions(action_list, context=context)
        if not resolved:
            return []

        # Preflight every action once, before scheduling any worker.  Apart
        # from avoiding duplicate permission prompts, this guarantees that a
        # later guard cannot turn an already-denied action back into allow.
        prepared: list[
            tuple[int, ResolvedToolAction, ToolExecutionContext, str, ToolExecutionPolicy]
        ] = []
        result_slots: dict[int, ToolExecutionResult] = {}
        for index, action in enumerate(resolved):
            action_context = self._action_context(action)
            call_id = self._next_call_id()
            if self._cancelled(action_context):
                result_slots[index] = self._skipped_result(action, call_id)
                continue
            try:
                denial = self._guard(action, action_context)
            except Exception as exc:
                denial = f"guard error: {type(exc).__name__}: {exc}"
            if denial:
                result_slots[index] = self._denied_result(action, call_id, denial)
                continue
            try:
                policy = self._policy_for(action, action_context)
            except Exception as exc:
                result_slots[index] = self._denied_result(
                    action,
                    call_id,
                    f"invalid execution policy: {exc}",
                )
                continue
            prepared.append((index, action, action_context, call_id, policy))

        futures: dict[int, concurrent.futures.Future[ToolExecutionResult]] = {}

        def wait_prior() -> None:
            if not futures:
                return
            for index in sorted(futures):
                if index not in result_slots:
                    result_slots[index] = futures[index].result()
            futures.clear()

        cursor = 0
        while cursor < len(prepared):
            index, action, action_context, call_id, policy = prepared[cursor]
            if (
                policy.mode is ToolExecutionMode.PARALLEL_SAFE
                and policy.thread_affinity == "any"
            ):
                # Adjacent parallel-safe actions form one bounded batch.  A
                # gap caused by a denied/skipped action is still a model action
                # boundary, so only the next prepared item can join when its
                # original index is adjacent.
                batch = [(index, action, action_context, call_id, policy)]
                batch_cursor = cursor + 1
                expected_index = index + 1
                while batch_cursor < len(prepared):
                    candidate = prepared[batch_cursor]
                    if candidate[0] != expected_index:
                        break
                    candidate_policy = candidate[4]
                    if (
                        candidate_policy.mode is not ToolExecutionMode.PARALLEL_SAFE
                        or candidate_policy.thread_affinity != "any"
                    ):
                        break
                    batch.append(candidate)
                    batch_cursor += 1
                    expected_index += 1
                # A denied/skipped/serial action creates a hard model-order
                # boundary. Do not let a later parallel batch overlap an
                # earlier batch merely because both happen to be safe.
                if futures:
                    wait_prior()
                for batch_index, batch_action, batch_context, batch_call_id, batch_policy in batch:
                    futures[batch_index] = self._pool.submit(
                        self._invoke,
                        batch_action,
                        batch_policy,
                        batch_context,
                        batch_call_id,
                    )
                cursor = batch_cursor
                continue

            # SERIAL, BARRIER, main-affine and BACKGROUND calls all establish a
            # scheduling boundary.  Waiting here prevents later work from
            # overlapping a barrier or an ordinary serial mutation.
            wait_prior()
            if self._cancelled(action_context):
                result_slots[index] = self._skipped_result(action, call_id)
            else:
                result_slots[index] = self._invoke(action, policy, action_context, call_id)
            cursor += 1

        wait_prior()
        final_results = list(result_slots.values())
        final_results.sort(key=lambda item: item.order)
        with self._records_lock:
            self._records.extend(final_results)
            self._records.sort(key=lambda item: item.order)
        return final_results

    # Common adapter aliases.  They intentionally delegate to one code path.
    run = execute
    submit = execute_action
    execute_actions = execute
    resolve_action = resolve


__all__ = [
    "ToolExecutionMode",
    "ToolExecutionPolicy",
    "ToolExecutionContext",
    "ToolExecutionCancelled",
    "ResolvedToolAction",
    "ToolExecutionResult",
    "ToolExecutionRecord",
    "_ToolExecutionEngine",
]
