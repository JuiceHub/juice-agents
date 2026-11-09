"""Default foreground scheduling, worker bounds and explicit boundaries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
from typing import Any, Callable

import pytest

from juice_agents.core.agent.tools.runtime import (
    Tool,
    ToolExecutionContext,
    ToolExecutionMode,
    ToolExecutionPolicy,
)
from juice_agents.core.managers import ToolManager
from juice_agents.core.agent.tools.runtime.executor import _ToolExecutionEngine


class _ProbeTool(Tool):
    """A custom tool deliberately relying on the framework's default policy."""

    name = "probe"

    def __init__(self, callback: Callable[[str], Any]) -> None:
        super().__init__()
        self.callback = callback

    def forward(self, value: str) -> Any:
        return self.callback(value)


class _PolicyProbeTool(_ProbeTool):
    def __init__(self, callback: Callable[[str], Any], policy: Callable[[str], ToolExecutionPolicy]) -> None:
        super().__init__(callback)
        self.policy = policy

    def execution_policy(self, args: dict[str, Any], context: Any) -> ToolExecutionPolicy:
        del context
        return self.policy(args["value"])


def _actions(*values: str) -> list[dict[str, Any]]:
    return [{"name": "probe", "args": {"value": value}} for value in values]


def test_foreground_policy_is_default_and_replaces_parallel_opt_in() -> None:
    assert ToolExecutionPolicy().mode is ToolExecutionMode.FOREGROUND
    assert {mode.value for mode in ToolExecutionMode} == {"foreground", "barrier", "background"}
    for removed in ("serial", "parallel_safe"):
        with pytest.raises(ValueError):
            ToolExecutionPolicy(mode=removed)


def test_default_custom_tool_overlaps_with_bounded_workers() -> None:
    """Hold the first two calls until the controlling thread checks capacity."""

    saturated = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0
    started: list[str] = []

    def forward(value: str) -> str:
        nonlocal active, peak
        with lock:
            started.append(value)
            active += 1
            peak = max(peak, active)
            if active == 2:
                saturated.set()
        try:
            assert release.wait(5), "default foreground tools did not occupy two workers"
            return value
        finally:
            with lock:
                active -= 1

    manager = ToolManager([_ProbeTool(forward)], max_workers=2, strict_policies=False)
    try:
        # The extra executor only drives the synchronous manager API. Its
        # worker is independent from the bounded pool being tested.
        with ThreadPoolExecutor(max_workers=1) as caller:
            pending = caller.submit(manager.execute, _actions("a", "b", "c", "d"))
            try:
                assert saturated.wait(5), "tools were serialized despite the default policy"
                with lock:
                    assert len(started) == active == peak == 2
            finally:
                release.set()
            results = pending.result(timeout=5)
        assert peak == 2
        assert [result.status for result in results] == ["completed"] * 4
        assert [result.observation for result in results] == ["a", "b", "c", "d"]
        assert [result.order for result in results] == list(range(4))
    finally:
        release.set()
        manager.release()


@pytest.mark.parametrize("boundary", ["barrier", "main"])
def test_explicit_boundary_waits_for_prior_batch_and_runs_on_caller(boundary: str) -> None:
    caller_id = threading.get_ident()
    siblings = threading.Barrier(2)
    finished: set[str] = set()
    lock = threading.Lock()
    boundary_finished = threading.Event()

    def policy(value: str) -> ToolExecutionPolicy:
        if value != "boundary":
            return ToolExecutionPolicy()
        if boundary == "barrier":
            return ToolExecutionPolicy(mode=ToolExecutionMode.BARRIER)
        return ToolExecutionPolicy(thread_affinity="main")

    def forward(value: str) -> str:
        if value == "boundary":
            assert threading.get_ident() == caller_id
            assert finished == {"before-a", "before-b"}
            boundary_finished.set()
        else:
            if value.startswith("after"):
                assert boundary_finished.is_set()
            siblings.wait(timeout=5)
            with lock:
                finished.add(value)
        return value

    manager = ToolManager([_PolicyProbeTool(forward, policy)], max_workers=2)
    try:
        values = ("before-a", "before-b", "boundary", "after-a", "after-b")
        results = manager.execute(_actions(*values))
        assert [result.status for result in results] == ["completed"] * len(values)
        assert [result.observation for result in results] == list(values)
    finally:
        manager.release()


def test_denied_action_does_not_split_independent_foreground_batch() -> None:
    siblings = threading.Barrier(2)

    def forward(value: str) -> str:
        siblings.wait(timeout=5)
        return value

    context = ToolExecutionContext(
        permission_check=lambda _tool, args: (
            {"allowed": False, "reason": "test denial"} if args["value"] == "denied" else True
        )
    )
    manager = ToolManager([_ProbeTool(forward)], context=context, max_workers=2, strict_policies=False)
    try:
        results = manager.execute(_actions("left", "denied", "right"))
        assert [result.status for result in results] == ["completed", "denied", "completed"]
        assert [results[index].observation for index in (0, 2)] == ["left", "right"]
        assert results[1].error == "test denial"
    finally:
        manager.release()


def test_cancellation_prevents_queued_foreground_tools_from_starting() -> None:
    cancel = threading.Event()
    called: list[str] = []

    def forward(value: str) -> str:
        called.append(value)
        cancel.set()
        return value

    manager = ToolManager(
        [_ProbeTool(forward)], context=ToolExecutionContext(cancel_event=cancel),
        max_workers=1, strict_policies=False,
    )
    try:
        results = manager.execute(_actions("first", "queued-a", "queued-b"))
        assert called == ["first"]
        assert [result.status for result in results] == ["cancelled"] * 3
        assert [result.order for result in results] == [0, 1, 2]
    finally:
        manager.release()


def test_pre_cancelled_calls_still_publish_one_correlated_terminal_pair() -> None:
    """An interrupted call keeps an identity even though forward never starts."""

    cancel = threading.Event()
    cancel.set()
    called: list[str] = []
    manager = ToolManager(
        [_ProbeTool(lambda value: called.append(value))],
        context=ToolExecutionContext(cancel_event=cancel), strict_policies=False,
    )
    try:
        events = list(manager.execute_streaming(_actions("cancelled-a", "cancelled-b")))
        assert called == []
        starts = [event for event in events if event.result is None]
        terminals = [event for event in events if event.result is not None]
        assert len(starts) == len(terminals) == 2
        assert len({event.call_id for event in starts}) == 2
        for start, terminal in zip(starts, terminals):
            assert start.call_id == terminal.call_id == terminal.result.call_id
            assert terminal.result.status == "cancelled"
            assert terminal.result.duration_seconds == 0.0
    finally:
        manager.release()


def test_background_tool_returns_launch_receipt() -> None:
    launches: list[str] = []

    def forward(_value: str) -> None:
        raise AssertionError("background launch must use the supplied launcher")

    def launcher(*, action: Any, context: Any) -> dict[str, str]:
        del context
        launches.append(action.args["value"])
        return {"task_id": "background-1", "status": "running"}

    tool = _PolicyProbeTool(forward, lambda _value: ToolExecutionPolicy(mode=ToolExecutionMode.BACKGROUND))
    manager = ToolManager([tool], context=ToolExecutionContext(background_launcher=launcher))
    try:
        result = manager.execute(_actions("launch"))[0]
        assert launches == ["launch"]
        assert result.status == "background"
        assert result.task_id == "background-1"
    finally:
        manager.release()


@pytest.mark.parametrize("shared_manager", [True, False])
def test_cancelled_resource_waiter_never_enters_tool(monkeypatch, shared_manager: bool) -> None:
    """Cancel after a worker starts waiting, before it acquires a shared lock.

    The observed lock only reports the second acquire attempt; its real Lock
    preserves normal scheduling. This removes the race between cancellation
    and a worker entering resource acquisition without relying on sleeps.
    """

    first_started = threading.Event()
    release_first = threading.Event()
    second_waiting = threading.Event()
    cancel_second = threading.Event()
    called: list[str] = []
    resource_key = "test:cancelled-resource-waiter"

    class ObservedLock:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.counter_lock = threading.Lock()
            self.attempts = 0

        def acquire(self) -> bool:
            with self.counter_lock:
                self.attempts += 1
                if self.attempts == 2:
                    second_waiting.set()
            return self.lock.acquire()

        def release(self) -> None:
            self.lock.release()

    monkeypatch.setitem(_ToolExecutionEngine._process_resource_locks, resource_key, ObservedLock())

    def forward(value: str) -> str:
        called.append(value)
        if value == "first":
            first_started.set()
            assert release_first.wait(5), "test did not release the resource owner"
        return value

    tool = _PolicyProbeTool(forward, lambda _value: ToolExecutionPolicy(resource_keys=(resource_key,)))
    manager = ToolManager([tool], max_workers=2)
    second_manager = manager if shared_manager else ToolManager([tool], max_workers=2)
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            first = callers.submit(manager.execute, _actions("first"))
            try:
                assert first_started.wait(5)
                second = callers.submit(
                    second_manager.execute, _actions("second"),
                    context=ToolExecutionContext(cancel_event=cancel_second),
                )
                assert second_waiting.wait(5), "second call did not wait on its declared resource"
                assert called == ["first"]
                cancel_second.set()
            finally:
                release_first.set()
            assert first.result(timeout=5)[0].status == "completed"
            assert second.result(timeout=5)[0].status == "cancelled"
        assert called == ["first"]
    finally:
        release_first.set()
        if second_manager is not manager:
            second_manager.release()
        manager.release()


def test_closing_stream_drains_submitted_calls_and_audits_them_once(tmp_path) -> None:
    """Close while two submitted tools run and the third only has a start event.

    The scheduler yields a start event before submitting its corresponding
    call. Closing at the third start therefore gives an exact boundary: two
    workers must finish, and the final tool must never be invoked or audited.
    """

    both_started = threading.Event()
    release = threading.Event()
    close_started = threading.Event()
    lock = threading.Lock()
    called: list[str] = []
    finished: set[str] = set()
    audits: list[dict[str, Any]] = []

    def forward(value: str) -> str:
        with lock:
            called.append(value)
            if len(called) == 2:
                both_started.set()
        assert release.wait(5), "stream close did not release its submitted tools"
        with lock:
            finished.add(value)
        return value

    manager = ToolManager(
        [_ProbeTool(forward)], max_workers=2, strict_policies=False,
        state_dir=tmp_path / "tools", record_callback=audits.append,
    )
    stream = manager.execute_streaming(_actions("first", "second", "unsubmitted"))
    try:
        starts = [next(stream) for _ in range(3)]
        assert [event.action.order for event in starts] == [0, 1, 2]
        assert all(event.result is None for event in starts)
        assert both_started.wait(5)

        def close_stream() -> None:
            close_started.set()
            stream.close()
            assert finished == {"first", "second"}, "close returned while submitted calls were running"

        with ThreadPoolExecutor(max_workers=1) as closer:
            closing = closer.submit(close_stream)
            try:
                assert close_started.wait(5)
                assert not closing.done()
                assert not manager.audit_path.exists()
            finally:
                release.set()
            closing.result(timeout=5)

        # A repeated close is harmless; it must not repeat tool execution or
        # append another terminal audit for an already persisted call_id.
        stream.close()
        assert sorted(called) == ["first", "second"]
        assert len(manager.records) == len(audits) == 2
        persisted = [json.loads(line) for line in manager.audit_path.read_text(encoding="utf-8").splitlines()]
        assert [record["order"] for record in persisted] == [0, 1]
        assert [record["observation"] for record in persisted] == ["first", "second"]
        assert [record["status"] for record in persisted] == ["completed", "completed"]
        assert len({record["call_id"] for record in persisted}) == 2
        assert [record["call_id"] for record in persisted] == [record["call_id"] for record in audits]
    finally:
        release.set()
        stream.close()
        manager.release()


def test_all_permissions_run_once_on_caller_before_any_worker_starts(monkeypatch) -> None:
    caller_id = threading.get_ident()
    worker_started = threading.Event()
    siblings = threading.Barrier(3)
    checked: list[tuple[str, str]] = []
    values = ("first", "second", "third")
    layers = ("tool", "runner", "permission", "plan", "sandbox")
    expected = [(value, layer) for value in values for layer in layers]

    def check(layer: str, args: dict[str, Any]) -> bool:
        assert threading.get_ident() == caller_id, "permission checks moved to a worker thread"
        assert not worker_started.is_set(), "a worker started before every action was preflighted"
        checked.append((args["value"], layer))
        return True

    def forward(value: str) -> str:
        worker_started.set()
        assert checked == expected
        siblings.wait(timeout=5)
        return value

    tool = _ProbeTool(forward)
    monkeypatch.setattr(tool, "check_permissions", lambda args, **_context: check("tool", args))
    context = ToolExecutionContext(
        permission_check=lambda _tool, args: check("permission", args),
        plan_check=lambda _tool, args: check("plan", args),
        sandbox_check=lambda _tool, args: check("sandbox", args),
    )
    manager = ToolManager(
        [tool], context=context, max_workers=3, strict_policies=False,
        policy_check=lambda _tool, args: check("runner", args),
    )
    try:
        results = manager.execute(_actions(*values))
        assert checked == expected
        assert [result.status for result in results] == ["completed"] * 3
        assert [result.observation for result in results] == list(values)
    finally:
        manager.release()
