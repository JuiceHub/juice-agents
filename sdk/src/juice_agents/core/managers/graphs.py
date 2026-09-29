"""Durable graph-run ownership independent of the Runner facade."""

from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.graph.types import (
    GraphAgentDispatcher,
    GraphBuildContext,
    GraphCancelledError,
    GraphIncompleteError,
    GraphPausedError,
)
from juice_agents.core.registry.graphs import GraphRegistry

if TYPE_CHECKING:
    from juice_agents.core.graph.runs import GraphRunStore

logger = logging.getLogger(__name__)


GraphLifecycleCallback = Callable[[dict[str, Any]], None]


class GraphRunManager:
    """Own graph instances, durable checkpoints and cooperative control.

    A graph definition is resolved afresh through ``GraphRegistry`` for each
    run (or from that run's immutable source snapshot).  Live graph objects
    are retained only while their run executes; persistence contains data and
    source, never Python instances.  That boundary makes a process recovery
    deterministic and prevents a registry from accidentally acquiring runtime
    state.
    """

    def __init__(
        self,
        *,
        store: GraphRunStore,
        registry: GraphRegistry,
        config_context: ConfigurationContext,
        runner_id: str = "",
        callbacks: dict[str, Any] | None = None,
        model: Any | None = None,
        model_name: str | None = None,
        model_effort: str | None = None,
        agent_dispatcher: GraphAgentDispatcher | None = None,
        owner_agent_name: str = "",
        lifecycle_callback: GraphLifecycleCallback | None = None,
        concurrency_limit: int = 4,
    ) -> None:
        if isinstance(concurrency_limit, bool) or not isinstance(concurrency_limit, int) or concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be a positive integer")
        self.store = store
        self.registry = registry
        self.config_context = config_context
        self.runner_id = str(runner_id or "")
        self.callbacks = dict(callbacks or {})
        self.model = model
        self.model_name = model_name
        self.model_effort = model_effort
        # A graph receives only this narrow capability bridge.  In particular
        # the Manager never stores a Runner, AgentManager, or live Agent.
        self.agent_dispatcher = agent_dispatcher
        self.owner_agent_name = str(owner_agent_name or "")
        self._lifecycle_callback = lifecycle_callback
        self._lock = threading.RLock()
        self._run_locks: dict[str, threading.Lock] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._instances: dict[str, Any] = {}
        self._futures: dict[str, concurrent.futures.Future[dict[str, Any]]] = {}
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=concurrency_limit,
            thread_name_prefix="juice-graph",
        )
        self._released = False

    @property
    def live_run_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._instances))

    def create_run(
        self,
        graph_name: str,
        payload: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        source_run_id: str = "",
        owner_agent_name: str = "",
    ) -> dict[str, Any]:
        """Create a pending run and snapshot its resolved definition source."""

        self._ensure_open()
        meta = self.registry.get_metadata(graph_name)
        manifest = self.store.create(
            graph_name=meta.name,
            payload=dict(payload or {}),
            config=dict(config or {}),
            source=meta.to_dict(),
            source_run_id=source_run_id,
            owner_agent_name=owner_agent_name or self.owner_agent_name,
        )
        logger.info("graph run created: graph_run_id=%s graph=%s", manifest["graph_run_id"], meta.name)
        return manifest

    def submit(self, graph_run_id: str) -> concurrent.futures.Future[dict[str, Any]]:
        """Schedule one durable run once, returning its manager-owned future."""

        self._ensure_open()
        normalized = str(graph_run_id or "").strip()
        if not normalized:
            raise ValueError("graph_run_id 不能为空")
        with self._lock:
            active = self._futures.get(normalized)
            if active is not None and not active.done():
                return active
            future = self._pool.submit(self.execute, normalized)
            self._futures[normalized] = future
            return future

    def run(
        self,
        graph_name: str,
        payload: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        owner_agent_name: str = "",
    ) -> dict[str, Any]:
        manifest = self.create_run(graph_name, payload, config, owner_agent_name=owner_agent_name)
        return self.execute(str(manifest["graph_run_id"]))

    def execute(self, graph_run_id: str) -> dict[str, Any]:
        """Execute or continue a run in the calling thread.

        Calls for the same run are serialized.  Checkpoint/event callbacks are
        attached before graph construction, so even a graph that fails in its
        first node has a complete state transition in its manifest.
        """

        self._ensure_open()
        normalized = str(graph_run_id or "").strip()
        if not normalized:
            raise ValueError("graph_run_id 不能为空")
        lock = self._lock_for(normalized)
        with lock:
            return self._execute_locked(normalized)

    def request(self, graph_run_id: str, action: str) -> dict[str, Any]:
        """Persist a pause/resume/stop request and wake live cancellation."""

        normalized = str(action or "").strip().lower()
        manifest = self.store.request(graph_run_id, normalized)
        if normalized == "stop":
            with self._lock:
                event = self._cancel_events.get(str(graph_run_id or "").strip())
            if event is not None:
                event.set()
        logger.info("graph run control requested: graph_run_id=%s action=%s", graph_run_id, normalized)
        return manifest

    def stop(self, graph_run_id: str, *, reason: str = "user_cancelled") -> dict[str, Any]:
        """Cooperatively stop one graph and record a stable terminal reason."""

        manifest = self.request(graph_run_id, "stop")
        changes: dict[str, Any] = {"closed_reason": str(reason or "user_cancelled")}
        # A paused graph has no live executor left to observe the request; it
        # must therefore converge synchronously rather than remain paused
        # forever with ``requested_status=stop``.
        if str(manifest.get("status") or "") == "paused":
            changes.update(status="stopped", requested_status="", finished_at=time.time())
        return self.store.update(str(manifest["graph_run_id"]), **changes)

    def resume(self, graph_run_id: str) -> dict[str, Any]:
        self._ensure_open()
        self.request(graph_run_id, "resume")
        return self.execute(graph_run_id)

    def restart(self, graph_run_id: str) -> dict[str, Any]:
        """Start a new run from durable input instead of mutating history."""

        source = self.store.get(graph_run_id)
        manifest = self.create_run(
            str(source["graph_name"]),
            dict(source.get("payload") or {}),
            dict(source.get("config") or {}),
            source_run_id=str(source.get("graph_run_id") or ""),
            owner_agent_name=str(source.get("owner_agent_name") or ""),
        )
        return self.execute(str(manifest["graph_run_id"]))

    def recover(self, *, reason: str = "stale_on_resume") -> list[dict[str, Any]]:
        """Converge runs left active by a prior process to explicit stopped state."""

        recovered = self.store.reconcile_stale_runs(reason=reason)
        if recovered:
            logger.info("recovered stale graph runs: count=%d", len(recovered))
        return recovered

    def release(self, *, wait: bool = True) -> None:
        """Request cancellation for live work and release the manager pool."""

        with self._lock:
            if self._released:
                return
            self._released = True
            events = list(self._cancel_events.values())
            futures = list(self._futures.values())
        for event in events:
            event.set()
        for future in futures:
            future.cancel()
        self._pool.shutdown(wait=wait, cancel_futures=True)
        with self._lock:
            self._instances.clear()
            self._cancel_events.clear()
        logger.info("graph run manager released")

    close = release

    def _execute_locked(self, graph_run_id: str) -> dict[str, Any]:
        manifest = self.store.get(graph_run_id)
        graph_name = str(manifest["graph_name"])
        status = str(manifest.get("status") or "")
        if status in {"completed", "failed"}:
            return manifest
        if status == "stopped" and str(manifest.get("closed_reason") or "") != "stale_on_recovery":
            return manifest

        config = dict(manifest.get("config") or {})
        configurable = dict(config.get("configurable") or {})
        cancel_event = threading.Event()
        context = GraphBuildContext(
            config_context=self.config_context,
            workspace_dir=self.config_context.workspace_dir,
            runner_id=self.runner_id,
            graph_run_id=graph_run_id,
            artifacts_dir=self.store.run_dir(graph_run_id) / "artifacts",
            model=self.model,
            model_name=self.model_name,
            model_effort=self.model_effort,
            agent_dispatcher=self.agent_dispatcher,
            owner_agent_name=str(manifest.get("owner_agent_name") or self.owner_agent_name),
            configurable=dict(configurable),
            callbacks=dict(self.callbacks),
        )
        snapshot_path = self.store.run_dir(graph_run_id) / "source.py"
        graph = (
            self.registry.instantiate_from_path(snapshot_path, context=context)
            if snapshot_path.exists()
            else self.registry.instantiate(graph_name, context=context)
        )
        checkpoint = self.store.load_checkpoint(graph_run_id)
        if checkpoint:
            configurable["checkpoint"] = checkpoint
        configurable["checkpoint_callback"] = lambda data: self.store.checkpoint(graph_run_id, data)
        configurable["event_callback"] = lambda event: self.store.append_event(graph_run_id, event)
        configurable["control_callback"] = lambda: str(self.store.get(graph_run_id).get("requested_status") or "")
        configurable["lifecycle_callback"] = lambda event: self._emit_lifecycle(
            str(event.get("event") or ""),
            graph_name=graph_name,
            graph_run_id=graph_run_id,
            **{key: value for key, value in event.items() if key != "event"},
        )
        config["configurable"] = configurable
        config["cancel_event"] = cancel_event
        with self._lock:
            self._instances[graph_run_id] = graph
            self._cancel_events[graph_run_id] = cancel_event
        self.store.update(
            graph_run_id,
            status="running",
            requested_status="",
            started_at=manifest.get("started_at") or time.time(),
            error="",
            closed_reason="",
        )
        self._emit_lifecycle("graph_started", graph_name=graph_name, graph_run_id=graph_run_id)
        logger.info("graph run started: graph_run_id=%s graph=%s", graph_run_id, graph_name)
        final_state: dict[str, Any] = {}
        try:
            streamer = getattr(graph, "stream", None)
            if callable(streamer):
                for event in streamer(dict(manifest.get("payload") or {}), config=config):
                    final_state = dict(event.get("state") or final_state)
                result_builder = getattr(graph, "result_from_state", None)
                result = result_builder(final_state) if callable(result_builder) else final_state
            else:
                result = graph.invoke(dict(manifest.get("payload") or {}), config=config)
            self.store.result(graph_run_id, result)
            completed = self.store.update(
                graph_run_id,
                status="completed",
                requested_status="",
                finished_at=time.time(),
                error="",
                closed_reason="",
            )
            self._emit_lifecycle("graph_completed", graph_name=graph_name, graph_run_id=graph_run_id)
            logger.info("graph run completed: graph_run_id=%s", graph_run_id)
            return completed
        except GraphPausedError:
            paused = self.store.update(graph_run_id, status="paused", requested_status="", error="")
            self._emit_lifecycle("graph_paused", graph_name=graph_name, graph_run_id=graph_run_id)
            return paused
        except Exception as exc:
            if isinstance(exc, GraphCancelledError) or type(exc).__name__ in {"StreamCancelled", "CancelledError"}:
                stopped = self.store.update(
                    graph_run_id,
                    status="stopped",
                    requested_status="",
                    finished_at=time.time(),
                    error=str(exc),
                    closed_reason=str(manifest.get("closed_reason") or "user_cancelled"),
                )
                self._emit_lifecycle("graph_stopped", graph_name=graph_name, graph_run_id=graph_run_id)
                logger.info("graph run stopped: graph_run_id=%s", graph_run_id)
                return stopped
            failed = self.store.update(
                graph_run_id,
                status="failed",
                requested_status="",
                finished_at=time.time(),
                error=str(exc),
                closed_reason="" if not isinstance(exc, GraphIncompleteError) else "incomplete",
            )
            self._emit_lifecycle(
                "graph_failed", graph_name=graph_name, graph_run_id=graph_run_id, detail=str(exc)
            )
            logger.exception("graph run failed: graph_run_id=%s", graph_run_id)
            return failed
        finally:
            with self._lock:
                self._instances.pop(graph_run_id, None)
                self._cancel_events.pop(graph_run_id, None)

    def _lock_for(self, graph_run_id: str) -> threading.Lock:
        with self._lock:
            return self._run_locks.setdefault(graph_run_id, threading.Lock())

    def _emit_lifecycle(self, event: str, **details: Any) -> None:
        callback = self._lifecycle_callback
        if callback is None:
            return
        try:
            callback({"scope": "graph", "event": str(event), **details})
        except Exception:
            logger.exception("graph lifecycle callback failed: event=%s", event)

    def _ensure_open(self) -> None:
        if self._released:
            raise RuntimeError("GraphRunManager 已释放")


__all__ = ["GraphRunManager", "GraphLifecycleCallback"]
