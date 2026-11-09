"""One execution result must feed streaming, logs and persisted action steps."""

from __future__ import annotations

import json
from typing import Any

import pytest

from juice_agents.core.agent.agent_type import ObservationImage
from juice_agents.core.agent.context import (
    ActionStep,
    SubmitOutputValidationError,
    deserialize_context_tree,
    serialize_context_tree,
)
from juice_agents.core.agent.context.strategies import TreeOfThoughtsStrategy
from juice_agents.core.permissions.policy import PermissionResult

from .test_exploration import _code, _react, _round, _submit, _write, make_harness


def _tool_ends(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [event["data"] for event in events
            if event["kind"] == "tool_end"
            and event["data"]["tool_name"] == name]


def test_multiple_actions_execute_once_and_keep_observation_indices_and_images(make_harness, tmp_path, monkeypatch) -> None:
    runner, agent, model = make_harness()
    targets = [runner.base_dir / "first.txt", runner.base_dir / "second.txt"]
    model.outputs[:] = [
        _react([{"name": "write", "args": {"path": str(path), "content": str(index)}}
                for index, path in enumerate(targets)]),
        _submit("done"),
    ]
    image = ObservationImage(image_url="https://example.invalid/result.png", description="first result")
    tool = agent.tools["write"]
    forward = tool.forward
    calls: list[dict[str, Any]] = []

    def with_mixed_results(**kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        forward(**kwargs)
        # Exercise both an image alongside text and an empty tool result.
        return ["first result", image] if kwargs["path"] == str(targets[0]) else None

    monkeypatch.setattr(tool, "forward", with_mixed_results)
    events, steps, end = _round(runner)

    assert end["output"] == "done"
    assert len(calls) == 2
    assert [target.read_text() for target in targets] == ["0", "1"]
    # Completion events follow runtime completion, while the saved observations
    # follow model action order. Match them through their stable action index.
    ends = sorted(_tool_ends(events, "write"), key=lambda event: event["tool_index"])
    starts = {
        event["data"]["tool_index"]: event["data"]
        for event in events
        if event["kind"] == "tool_start"
        and event["data"]["tool_name"] == "write"
    }
    # Repeated calls to the same tool keep their identity from admission through
    # out-of-order completion and the durable ActionStep projection.
    for end, call in zip(ends, steps[0].tool_calls):
        start = starts[end["tool_index"]]
        assert start["call_id"] == end["call_id"] == call["call_id"]
        assert start["tool_args"] == end["tool_args"] == call["args"]
        assert end["status"] == "completed"
        assert call["error"] is None
        assert call["execution_time"] == end["execution_time"]
    assert [event["observation"] for event in ends] == steps[0].observations
    assert [call["observation_index"] for call in steps[0].tool_calls] == [0, 1]
    assert len({call["call_id"] for call in steps[0].tool_calls}) == 2
    assert steps[0].observations_images == [image]
    assert steps[0].observations[1] == "执行完成，工具无返回值"


@pytest.mark.parametrize("failure", ["denied", "failed"])
def test_tool_denial_and_failure_use_same_saved_and_streamed_result(make_harness, tmp_path, monkeypatch, failure: str) -> None:
    runner, agent, model = make_harness()
    target = tmp_path / "must-not-exist.txt"
    model.outputs[:] = [_write(target, "blocked"), _submit("recovered")]
    tool = agent.tools["write"]
    calls: list[dict[str, Any]] = []

    def broken_forward(**kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        raise RuntimeError("broken tool")

    monkeypatch.setattr(tool, "forward", broken_forward)
    if failure == "denied":
        monkeypatch.setattr(tool, "check_permissions", lambda *_args, **_kwargs: PermissionResult("deny", reason="test denial"))
    events, steps, end = _round(runner)

    assert end["output"] == "recovered"
    assert len(calls) == (0 if failure == "denied" else 1)
    assert not target.exists()
    if failure == "denied":
        # Preflight denial never entered the tool; its durable result is shown
        # with the committed step without inventing a tool execution boundary.
        assert _tool_ends(events, "write") == []
        assert steps[0].tool_calls[0]["status"] == "denied"
        assert steps[0].tool_calls[0]["error"] == "test denial"
        assert steps[0].observations == ["test denial"]
        return
    result = _tool_ends(events, "write")[0]
    assert result["observation"] == steps[0].observations[0]
    assert result["error"] is not None
    start = next(event["data"] for event in events
                 if event["kind"] == "tool_start"
                 and event["data"]["tool_name"] == "write")
    assert start["call_id"] == result["call_id"] == steps[0].tool_calls[0]["call_id"]
    assert result["status"] == failure
    assert result["tool_args"] == start["tool_args"]
    assert steps[0].tool_calls[0]["error"] == result["error"]
    assert steps[0].tool_calls[0]["execution_time"] == result["execution_time"]
    assert steps[0].tool_calls[0]["status"] == failure
    assert steps[0].error is None
    restored = deserialize_context_tree(serialize_context_tree(agent.context))
    saved = next(step for step in restored.steps if isinstance(step, ActionStep)).tool_calls[0]
    assert saved == steps[0].tool_calls[0]


def test_background_launch_keeps_its_lifecycle_and_task_identity_in_history(make_harness, monkeypatch) -> None:
    """A successful launch must not appear as a completed foreground call."""

    runner, agent, model = make_harness()
    args = {"command": "echo background", "background": True}
    model.outputs[:] = [_react([{"name": "shell", "args": args}]), _submit("launched")]
    launches: list[dict[str, Any]] = []

    def launch(**kwargs: Any) -> dict[str, str]:
        launches.append(kwargs)
        return {"status": "launched", "task_id": "background-lifecycle-test"}

    monkeypatch.setattr(agent.tools["shell"], "forward", launch)
    events, steps, end = _round(runner)

    assert end["output"] == "launched"
    assert launches == [args]
    result = _tool_ends(events, "shell")[0]
    call = steps[0].tool_calls[0]
    assert result["status"] == call["status"] == "background"
    assert result["task_id"] == call["task_id"] == "background-lifecycle-test"
    assert result["call_id"] == call["call_id"]
    assert result["tool_args"] == call["args"] == args


@pytest.mark.parametrize("source, status", [("print('ok')", "completed"), ("1 / 0", "failed")])
def test_codeact_wrapper_end_exposes_code_and_terminal_status(make_harness, source: str, status: str) -> None:
    runner, agent, model = make_harness(agent_type="codeact")
    model.outputs[:] = [_code(source), _code("submit_output(output='done')")]
    events, steps, end = _round(runner)

    assert end["output"] == "done"
    result = _tool_ends(events, "Python")[0]
    assert result["tool_args"] == {"code": source}
    assert result["status"] == status
    assert bool(result["error"]) == (status == "failed")
    # Code errors belong to the Python call, rather than an additional flow
    # error. Its durable record must retain the streamed lifecycle identity.
    assert steps[0].error is None
    call = next(call for call in steps[0].tool_calls if call["name"] == "Python")
    start = next(event["data"] for event in events
                 if event["kind"] == "tool_start"
                 and event["data"]["tool_name"] == "Python")
    assert start["call_id"] == result["call_id"] == call["call_id"]
    assert call["status"] == status
    assert call["error"] == result["error"]
    assert call["execution_time"] == result["execution_time"]
    assert call["args"] == result["tool_args"]
    assert call["is_terminal"] == result["is_terminal"] is False
    assert steps[0].observations[call["observation_index"]] == result["observation"]
    assert len({event["call_id"] for event in _tool_ends(events, "Python")}) == len(steps)
    assert _tool_ends(events, "Python")[-1]["is_terminal"] is True
    assert steps[-1].tool_calls[0]["is_terminal"] is True
    restored = deserialize_context_tree(serialize_context_tree(agent.context))
    saved = next(step for step in restored.steps if isinstance(step, ActionStep))
    assert saved.tool_calls == steps[0].tool_calls
    assert saved.error is None
    assert steps[0].round_outcome == "continue"


@pytest.mark.parametrize("returned", [
    "Error: file could not be read",
    {"stdout": "partial result", "stderr": "command failed", "returncode": 2},
    {"error": "service is unavailable", "details": "retry later"},
])
def test_tool_returned_errors_keep_output_without_guessing_execution_status(make_harness, monkeypatch, returned: Any) -> None:
    runner, agent, model = make_harness()
    model.outputs[:] = [_react([{"name": "shell", "args": {"command": "unused"}}]), _submit("done")]
    monkeypatch.setattr(agent.tools["shell"], "forward", lambda **_kwargs: returned)

    events, steps, end = _round(runner)

    assert end["output"] == "done"
    result = _tool_ends(events, "shell")[0]
    # Returning an error string/dictionary is still a completed tool invocation;
    # the output remains available for display without inventing a new status.
    assert result["status"] == "completed"
    assert result["error"] is None
    assert steps[0].error is None
    assert result["observation"] == steps[0].observations[0]
    if isinstance(returned, str):
        assert result["observation"] == returned
    else:
        for value in returned.values():
            assert str(value) in result["observation"]


def test_failed_background_receipt_retains_error_and_original_tool_output(make_harness, monkeypatch) -> None:
    runner, agent, model = make_harness()
    model.outputs[:] = [
        _react([{"name": "shell", "args": {"command": "unused", "background": True}}]),
        _submit("recovered"),
    ]
    receipt = {"status": "failed", "error": "launch failed", "details": "original receipt"}
    monkeypatch.setattr(agent.tools["shell"], "forward", lambda **_kwargs: receipt)

    events, steps, end = _round(runner)

    assert end["output"] == "recovered"
    result = _tool_ends(events, "shell")[0]
    assert result["status"] == "failed"
    assert result["error"] == "launch failed"
    assert "original receipt" in result["observation"]
    assert result["observation"] == steps[0].observations[0]
    assert steps[0].error is None


def test_codeact_executor_exception_finishes_its_tool_record_and_continues(make_harness, monkeypatch) -> None:
    runner, agent, model = make_harness(agent_type="codeact")
    source = "print('unexpected executor failure')"
    model.outputs[:] = [_code(source), _code("submit_output(output='recovered')")]
    execute = type(agent.executor).__call__

    def raise_once(executor, code: str):
        if code == source:
            raise RuntimeError("executor exploded")
        return execute(executor, code)

    monkeypatch.setattr(type(agent.executor), "__call__", raise_once)
    events, steps, end = _round(runner)

    assert end["output"] == "recovered"
    result = _tool_ends(events, "Python")[0]
    assert result["status"] == "failed"
    assert result["error"] == "RuntimeError: executor exploded"
    assert result["call_id"] == steps[0].tool_calls[0]["call_id"]
    assert "executor exploded" in steps[0].observations[0]
    assert steps[0].error is None
    assert steps[0].round_outcome == "continue"


def test_codeact_submission_schema_error_keeps_completed_python_record(make_harness) -> None:
    runner, agent, model = make_harness(agent_type="codeact")
    agent.output_schema = {"type": "integer"}
    model.outputs[:] = [_code("submit_output(output='invalid integer')")]

    events, steps, end = _round(runner)

    assert end["status"] == "failed"
    result = _tool_ends(events, "Python")[0]
    call = next(call for call in steps[0].tool_calls if call["name"] == "Python")
    # Execution has succeeded; submission validation retains its existing flow
    # failure behavior. Both results survive streaming and restoration.
    assert result["status"] == call["status"] == "completed"
    assert result["error"] is call["error"] is None
    assert result["call_id"] == call["call_id"]
    assert steps[0].observations[call["observation_index"]] == result["observation"]
    assert "output_schema" in str(steps[0].error)
    assert any(isinstance(node.step.error, SubmitOutputValidationError)
               for node in agent.context.nodes.values() if isinstance(node.step, ActionStep))
    assert steps[0].round_outcome == "failed"
    assert "<error>" in json.dumps(steps[0].to_messages(), ensure_ascii=False)
    restored = deserialize_context_tree(serialize_context_tree(agent.context))
    saved = next(step for step in restored.steps if isinstance(step, ActionStep))
    assert saved.tool_calls == steps[0].tool_calls
    assert saved.observations == steps[0].observations
    assert str(saved.error) == str(steps[0].error)


@pytest.mark.parametrize("output", [{"ok": True, "items": [1, 2]}, [None, False, {"text": "你好"}]])
def test_structured_submit_output_has_one_text_projection_for_stream_logs_and_saved_step(make_harness, monkeypatch, tmp_path, output: Any) -> None:
    outputs = []
    expected_text = json.dumps(output, ensure_ascii=False)
    for streaming in (False, True):
        runner, agent, model = make_harness()
        log_path = tmp_path / f"structured-{streaming}.log"
        agent.set_log_file_path(log_path)
        model.outputs[:] = [_submit(output)]
        tool = agent.tools["submit_output"]
        forward = tool.forward
        calls: list[dict[str, Any]] = []

        def counted_submit(**kwargs: Any) -> Any:
            calls.append(dict(kwargs))
            return forward(**kwargs)

        monkeypatch.setattr(tool, "forward", counted_submit)
        if streaming:
            events, steps, end = _round(runner)
            outputs.append(end["output"])
            assert _tool_ends(events, "submit_output")[0]["observation"] == expected_text
        else:
            outputs.append(runner.run("choose"))
            steps = [step for step in agent.context.steps if isinstance(step, ActionStep)]
        assert len(calls) == 1
        assert steps[-1].observations == [expected_text]
        assert f"\n{expected_text}\n" in log_path.read_text(encoding="utf-8")
    assert outputs == [output, output]


def test_codeact_cancellation_during_scoring_stops_before_raw_python_execution(make_harness, monkeypatch) -> None:
    runner, agent, model = make_harness(agent_type="codeact")
    model.outputs[:] = [_code("print('must not execute')"), _code("submit_output(output='unused')")]
    executions: list[str] = []
    executor_call = type(agent.executor).__call__

    def record_execution(executor, source: str):
        executions.append(source)
        return executor_call(executor, source)

    class CancellingScorer:
        def evaluate_batch(self, *, context, actions):
            runner.request_stop_stream(reason="cancelled during scoring")
            return {"scores": [1.0, 0.0], "usage": None, "usage_calls": []}

    monkeypatch.setattr(type(agent.executor), "__call__", record_execution)
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=CancellingScorer()))
    events = list(runner.stream("choose"))

    assert executions == []
    assert len(model.messages) == 2
    assert events[-1]["kind"] == "round_end"
    assert events[-1]["data"]["result"]["status"] == "cancelled"
    assert not any(event["kind"] == "tool_start" for event in events)
    assert len([node for node in agent.context.nodes.values() if isinstance(node.step, ActionStep)]) == 2
