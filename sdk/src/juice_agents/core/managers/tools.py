"""Tool runtime ownership: binding, scheduling, cancellation and audit.

``Tool`` is intentionally a small domain object.  This manager is the only
runtime owner of its worker pool and execution records.  It accepts explicit
callbacks/context rather than reaching into a Runner, which keeps permission
and cancellation boundaries testable and makes one manager usable by any
request host.
"""

from __future__ import annotations

from dataclasses import replace
from contextlib import aclosing
import json
import logging
from pathlib import Path
import threading
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from juice_agents.core.agent.tools.runtime.base_tools import Tool
from juice_agents.core.agent.tools.runtime.executor import (
    ResolvedToolAction,
    ToolExecutionContext,
    ToolExecutionEvent,
    ToolExecutionResult,
    _ToolExecutionEngine,
)

from juice_agents.core.runner.execution.async_io import blocking

logger = logging.getLogger(__name__)


class ToolManager:
    """Own the live tool runtime for one runner scope.

    The scheduling implementation remains a private collaborator while the
    manager owns its lifetime and durable audit projection.  A persisted call
    record deliberately stores plain data only: rehydrating a live ``Tool``
    from an audit trail would violate the Registry -> Manager boundary.
    """

    def __init__(
        self,
        tools: Mapping[str, Tool] | Iterable[Tool] | None = None,
        *,
        context: ToolExecutionContext | None = None,
        max_workers: int = 8,
        strict_policies: bool = True,
        state_dir: str | Path | None = None,
        record_callback: Callable[[dict[str, Any]], None] | None = None,
        policy_check: Callable[[Tool, dict[str, Any]], Any] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._released = False
        self._record_callback = record_callback
        # The manager owns capability projection as well as scheduling.  The
        # callable is supplied by RunnerConfig composition, never by Agent
        # code or a model-produced action.
        self._policy_check = policy_check
        self._state_dir = None if state_dir is None else Path(state_dir).expanduser().resolve()
        self._audit_path = None if self._state_dir is None else self._state_dir / "tool_calls.jsonl"
        self._persisted_call_ids: set[str] = set()
        # A Runner owns one ToolManager.  Each acquired Agent gets a private
        # executor view because its declared tool set and permission context
        # are distinct.  Keeping those views here prevents Agent instances
        # from becoming accidental owners of worker pools.
        self._agent_managers: dict[str, "ToolManager"] = {}
        self._executor = _ToolExecutionEngine(
            tools,
            context=context,
            max_workers=max_workers,
            strict_policies=strict_policies,
        )
        self._restore_audit_index()

    @property
    def context(self) -> ToolExecutionContext:
        """The currently bound runtime context (permissions/cancellation)."""

        return self._executor.context

    @property
    def tools(self) -> Mapping[str, Tool]:
        return self._executor.tools

    @property
    def records(self) -> list[ToolExecutionResult]:
        return self._executor.records

    @property
    def audit_path(self) -> Path | None:
        return self._audit_path

    def bind(
        self,
        *,
        context: ToolExecutionContext | None = None,
        record_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Bind request-scoped services without recreating registered tools."""

        with self._lock:
            self._ensure_open()
            if context is not None:
                self._executor.context = context
            if record_callback is not None:
                self._record_callback = record_callback

    def bind_agent(
        self,
        agent: Any,
        *,
        agent_id: str,
        context: ToolExecutionContext,
    ) -> "ToolManager":
        """Register one Agent's tools under this manager's ownership.

        The child manager is an internal implementation detail.  It is *not*
        assigned to the Agent: Agent code reaches it only through
        ``RunnerContext.execute_tools``.  This prevents an Agent from owning a
        worker pool or from bypassing the Runner's permission/cancel context.
        """

        normalized = str(agent_id or "").strip()
        if not normalized:
            raise ValueError("agent_id 不能为空")
        with self._lock:
            self._ensure_open()
            previous = self._agent_managers.pop(normalized, None)
            if previous is not None:
                previous.release()
            child_state_dir = None if self._state_dir is None else self._state_dir / normalized
            child = ToolManager(
                getattr(agent, "tools", {}),
                context=context,
                max_workers=getattr(agent, "tool_executor_max_workers", 8),
                strict_policies=False,
                state_dir=child_state_dir,
                record_callback=self._record_callback,
                policy_check=self._policy_check,
            )
            self._agent_managers[normalized] = child
            return child

    def execute_for_agent(
        self,
        *,
        agent_id: str,
        actions: Sequence[ResolvedToolAction | Mapping[str, Any] | Tool]
        | ResolvedToolAction
        | Mapping[str, Any]
        | Tool,
        context: ToolExecutionContext,
    ) -> list[ToolExecutionResult]:
        """Execute through the private binding registered for ``agent_id``."""

        with self._lock:
            child = self._agent_managers.get(str(agent_id or "").strip())
        if child is None:
            raise RuntimeError("当前 Agent 未绑定 ToolManager")
        return child.execute(actions, context=context)

    def execute_action_for_agent(
        self,
        *,
        agent_id: str,
        action: ResolvedToolAction | Mapping[str, Any] | Tool,
        order: int = 0,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """Single-action form used by the CodeAct callable adapter."""

        with self._lock:
            child = self._agent_managers.get(str(agent_id or "").strip())
        if child is None:
            raise RuntimeError("当前 Agent 未绑定 ToolManager")
        return child.execute_action(action, order=order, context=context)

    def execute_streaming_for_agent(
        self,
        *,
        agent_id: str,
        actions: Sequence[ResolvedToolAction | Mapping[str, Any] | Tool],
        context: ToolExecutionContext,
    ) -> Iterator[ToolExecutionEvent]:
        """Stream the same manager-owned batch used by non-streaming callers."""

        with self._lock:
            child = self._agent_managers.get(str(agent_id or "").strip())
        if child is None:
            raise RuntimeError("当前 Agent 未绑定 ToolManager")
        return child.execute_streaming(actions, context=context)

    def release_agent(self, agent_id: str) -> None:
        """Release one per-agent executor without affecting other Agents."""

        with self._lock:
            child = self._agent_managers.pop(str(agent_id or "").strip(), None)
        if child is not None:
            child.release()

    def register(self, name: str, tool: Tool) -> Tool:
        with self._lock:
            self._ensure_open()
            return self._executor.register(name, tool)

    def resolve(
        self,
        action: ResolvedToolAction | Mapping[str, Any] | Tool,
        *,
        order: int = 0,
        context: ToolExecutionContext | None = None,
    ) -> ResolvedToolAction:
        self._ensure_open()
        return self._executor.resolve(action, order=order, context=context)

    def execute(
        self,
        actions: Sequence[ResolvedToolAction | Mapping[str, Any] | Tool]
        | ResolvedToolAction
        | Mapping[str, Any]
        | Tool,
        *,
        context: ToolExecutionContext | None = None,
    ) -> list[ToolExecutionResult]:
        """Execute a batch and return terminal records in action order."""

        records = [
            event.result
            for event in self.execute_streaming(actions, context=context)
            if event.result is not None
        ]
        return sorted(records, key=lambda item: item.order)

    def execute_streaming(
        self,
        actions: Sequence[ResolvedToolAction | Mapping[str, Any] | Tool]
        | ResolvedToolAction
        | Mapping[str, Any]
        | Tool,
        *,
        context: ToolExecutionContext | None = None,
    ) -> Iterator[ToolExecutionEvent]:
        """Stream scheduling events, then persist terminal calls exactly once.

        Guard and scheduling code runs on this request thread. Closing the
        stream drains already submitted workers before publishing their audit,
        so interrupted consumers cannot leave invisible calls behind.
        """

        with self._lock:
            self._ensure_open()
        # ``ResolvedToolAction`` may carry a context from a previous caller.
        # Rebind it here instead of trusting that embedded value: otherwise a
        # model-facing adapter could accidentally retain a context that was
        # created before the RunnerConfig policy was composed.
        effective_context = self._context_with_policy(context)
        # The finalizer receives this batch only, including calls drained on
        # early iterator close. Avoid rescanning all historical audit records.
        yield from self._executor.execute_streaming(
            self._bind_actions_to_context(actions, effective_context),
            context=effective_context,
            on_complete=self._persist_records,
        )

    run = execute
    execute_actions = execute

    def execute_action(
        self,
        action: ResolvedToolAction | Mapping[str, Any] | Tool,
        *,
        order: int = 0,
        context: ToolExecutionContext | None = None,
    ) -> ToolExecutionResult:
        # Resolve with the request context so action metadata and the executor
        # agree, then delegate to ``execute`` for the policy rebinding/audit
        # path shared by batch execution.
        effective_context = self._context_with_policy(context)
        resolved = self.resolve(action, order=order, context=effective_context)
        return self.execute([resolved], context=effective_context)[0]

    def cancel(self) -> None:
        """Request cooperative cancellation for calls sharing this context."""

        event = self.context.cancel_event
        if event is not None:
            event.set()
            logger.info("tool manager cancellation requested")

    def release(self, *, wait: bool = True) -> None:
        """Release the worker pool.  A released manager cannot be reused."""

        with self._lock:
            if self._released:
                return
            self._released = True
            children = list(self._agent_managers.values())
            self._agent_managers.clear()
        # 工具的收尾可能回调 Manager；等待线程池时不能持有它需要的锁。
        self._executor.close(wait=wait)
        for child in children:
            child.release(wait=wait)
        logger.info("tool manager released")

    close = release

    async def aexecute(self, actions: Any, **kwargs: Any) -> list[ToolExecutionResult]:
        results = []
        async with aclosing(self.aexecute_streaming(actions, **kwargs)) as events:
            async for event in events:
                if event.result is not None:
                    results.append(event.result)
        return sorted(results, key=lambda item: item.order)

    async def aexecute_for_agent(self, *, agent_id: str, actions: Any, **kwargs: Any) -> list[ToolExecutionResult]:
        child = self._agent_runtime(agent_id)
        return await child.aexecute(actions, **kwargs)

    def _agent_runtime(self, agent_id: str) -> "ToolManager":
        with self._lock:
            child = self._agent_managers.get(agent_id)
        if child is None:
            raise RuntimeError("当前 Agent 未绑定 ToolManager")
        return child

    async def arun_code_for_agent(self, agent_id: str, execute: Callable, code: str) -> Any:
        child = self._agent_runtime(agent_id)
        return await blocking(execute, code, executor=child._executor._affinity_pool,
                              on_cancel=child.cancel)

    async def aexecute_streaming_for_agent(self, *, agent_id: str, actions: Any, **kwargs: Any):
        child = self._agent_runtime(agent_id)
        async with aclosing(child.aexecute_streaming(actions, **kwargs)) as events:
            async for event in events:
                yield event

    async def aexecute_streaming(self, actions: Any, **kwargs: Any):
        with self._lock:
            self._ensure_open()
        effective_context = self._context_with_policy(kwargs.pop("context", None))
        async with aclosing(self._executor.aexecute_streaming(
            self._bind_actions_to_context(actions, effective_context),
            context=effective_context, on_complete=self._persist_records, **kwargs,
        )) as events:
            try:
                async for event in events:
                    yield event
            except BaseException:
                # 先发协作信号，再由引擎 finally 等工具真实收尾。
                if effective_context is not None and effective_context.cancel_event is not None:
                    effective_context.cancel_event.set()
                raise

    async def aclose(self, *, wait: bool = True) -> None:
        await blocking(self.release, wait=wait)

    def __enter__(self) -> "ToolManager":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()

    def _ensure_open(self) -> None:
        if self._released:
            raise RuntimeError("ToolManager 已释放")

    def _context_with_policy(self, context: ToolExecutionContext | None) -> ToolExecutionContext | None:
        """Compose the Runner's immutable policy before Agent permission hooks.

        A configuration denial has priority and cannot be relaxed by an
        Agent-supplied permission callback.  When policy permits the call, the
        existing Agent permission hook still enforces approvals and sandbox
        rules, preserving the single execution contract.
        """

        if self._policy_check is None:
            return context
        original = self.context if context is None else context
        inherited_check = original.permission_check

        def combined(tool: Tool, args: dict[str, Any]) -> Any:
            decision = self._policy_check(tool, dict(args))
            if decision is not True and decision is not None:
                return decision
            return True if inherited_check is None else inherited_check(tool, dict(args))

        return replace(original, permission_check=combined)

    @staticmethod
    def _bind_actions_to_context(
        actions: Sequence[ResolvedToolAction | Mapping[str, Any] | Tool]
        | ResolvedToolAction
        | Mapping[str, Any]
        | Tool,
        context: ToolExecutionContext | None,
    ) -> Sequence[ResolvedToolAction | Mapping[str, Any] | Tool] | ResolvedToolAction | Mapping[str, Any] | Tool:
        """Return actions whose resolved entries use the effective context.

        Raw mappings and ``Tool`` instances are resolved by the private engine
        with its ``context`` argument.  Only already-resolved entries need an
        explicit replacement.  A tuple/list is copied deliberately so callers
        keep ownership of their request object.
        """

        if context is None:
            return actions
        if isinstance(actions, ResolvedToolAction):
            return replace(actions, context=context)
        if isinstance(actions, (Mapping, Tool)):
            return actions
        return [
            replace(action, context=context) if isinstance(action, ResolvedToolAction) else action
            for action in actions
        ]

    def _restore_audit_index(self) -> None:
        """Read only call ids; audit recovery must never instantiate tools."""

        path = self._audit_path
        if path is None or not path.exists():
            return
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                payload = json.loads(line)
                if isinstance(payload, dict) and str(payload.get("call_id") or ""):
                    self._persisted_call_ids.add(str(payload["call_id"]))
        except (OSError, json.JSONDecodeError):
            logger.warning("tool audit 恢复失败，将从新记录开始: %s", path, exc_info=True)
            self._persisted_call_ids.clear()

    def _persist_records(self, records: Iterable[ToolExecutionResult]) -> None:
        outgoing: list[dict[str, Any]] = []
        with self._lock:
            for record in records:
                if record.call_id in self._persisted_call_ids:
                    continue
                payload = record.to_dict()
                payload["started_at"] = record.started_at
                payload["finished_at"] = record.finished_at
                outgoing.append(payload)
                self._persisted_call_ids.add(record.call_id)
            if not outgoing:
                return
            if self._audit_path is not None:
                self._audit_path.parent.mkdir(parents=True, exist_ok=True)
                with self._audit_path.open("a", encoding="utf-8") as handle:
                    for payload in outgoing:
                        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        for payload in outgoing:
            if self._record_callback is not None:
                try:
                    self._record_callback(dict(payload))
                except Exception:
                    # Audit callbacks are observers. A broken observer cannot
                    # retroactively change a completed tool outcome.
                    logger.exception("tool audit callback failed: call_id=%s", payload["call_id"])


__all__ = ["ToolManager"]
