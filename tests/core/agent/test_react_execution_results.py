"""One execution result must feed streaming, logs and persisted action steps."""

from __future__ import annotations

import json
from typing import Any

import pytest

from juice_agents.core.agent.agent_type import ObservationImage
from juice_agents.core.agent.context import ActionStep
from juice_agents.core.agent.context.strategies import TreeOfThoughtsStrategy
from juice_agents.core.permissions.policy import PermissionResult

from .test_exploration import _code, _react, _round, _submit, _write, make_harness


def _tool_ends(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [event["data"] for event in events
            if event["kind"] == "action_event" and event["event_type"] == "tool_end"
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
        return ["first result", image] if len(calls) == 1 else None

    monkeypatch.setattr(tool, "forward", with_mixed_results)
    events, steps, end = _round(runner)

    assert end["output"] == "done"
    assert len(calls) == 2
    assert [target.read_text() for target in targets] == ["0", "1"]
    assert [event["observation"] for event in _tool_ends(events, "write")] == steps[0].observations
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
    result = _tool_ends(events, "write")[0]
    assert result["observation"] == steps[0].observations[0]
    assert result["error"] is not None
    assert steps[0].tool_calls[0]["status"] == failure


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
    assert any(event["kind"] == "stream_cancelled" for event in events)
    assert not any(event["kind"] == "action_event" and event["event_type"] == "tool_start" for event in events)
    assert len([node for node in agent.context.nodes.values() if isinstance(node.step, ActionStep)]) == 2
