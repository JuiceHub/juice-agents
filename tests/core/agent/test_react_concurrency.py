"""ReAct batches overlap inside a step and retain stable action identities."""

from __future__ import annotations

import threading
from typing import Any

from juice_agents.core.agent.agent_type import ObservationImage

from .test_exploration import _react, _round, _submit, make_harness


def _writes(runner: Any, *values: str) -> str:
    """Use different resources so file locks cannot hide batch scheduling."""

    return _react([
        {"name": "write", "args": {"path": str(runner.base_dir / f"{value}.txt"), "content": value}}
        for value in values
    ])


def test_react_streams_completed_sibling_before_batch_finishes(make_harness, monkeypatch) -> None:
    """A consumer can observe the fast tool while another tool still runs.

    Releasing the first tool only after receiving the second tool's event
    proves both actual overlap and live streaming without elapsed-time checks.
    """

    runner, agent, model = make_harness()
    model.outputs[:] = [_writes(runner, "first", "second"), _submit("done")]
    first_started = threading.Event()
    release_first = threading.Event()
    first_finished = threading.Event()
    images = {
        value: ObservationImage(image_url=f"https://example.invalid/{value}.png", description=value)
        for value in ("first", "second")
    }

    def forward(*, path: str, content: str) -> list[Any]:
        del path
        if content == "first":
            first_started.set()
            assert release_first.wait(5), "completed sibling was not streamed while first tool ran"
            first_finished.set()
        else:
            assert first_started.wait(5), "first tool never started"
        return [content, images[content]]

    monkeypatch.setattr(agent.tools["write"], "forward", forward)
    events: list[dict[str, Any]] = []
    try:
        for event in runner.stream("choose"):
            events.append(event)
            if (
                event["kind"] == "tool_end"
                and event["data"]["tool_name"] == "write"
                and event["data"]["tool_index"] == 1
            ):
                assert not first_finished.is_set()
                release_first.set()
    finally:
        # A failed assertion must also release workers before fixture teardown.
        release_first.set()

    tool_events = [event for event in events
                   if event["kind"] in {"tool_start", "tool_end"} and event["data"].get("tool_name") == "write"]
    starts = [event["data"] for event in tool_events if event["kind"] == "tool_start"]
    ends = [event["data"] for event in tool_events if event["kind"] == "tool_end"]
    assert sorted(event["tool_index"] for event in starts) == [0, 1]
    assert [event["tool_index"] for event in ends] == [1, 0]
    from juice_agents.core.agent.context.serialization import _deserialize_step

    step = next(_deserialize_step(event["data"]["step"]) for event in events if event["kind"] == "step_end")
    assert [observation.splitlines()[0] for observation in step.observations] == ["first", "second"]
    assert step.observations_images == [images["first"], images["second"]]
    assert [call["observation_index"] for call in step.tool_calls] == [0, 1]
    assert len({call["call_id"] for call in step.tool_calls}) == 2
    for event in ends:
        assert event["observation"] == step.observations[event["tool_index"]]
        assert event["error"] is None
    assert events[-1]["data"]["result"]["output"] == "done"


def test_react_waits_for_all_actions_before_next_step(make_harness, monkeypatch) -> None:
    runner, agent, model = make_harness()
    model.outputs[:] = [
        _writes(runner, "left", "right"),
        _writes(runner, "dependent"),
        _submit("done"),
    ]
    siblings = threading.Barrier(2)
    finished: set[str] = set()
    lock = threading.Lock()
    generate = model.generate

    def generate_after_batch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Check the model boundary itself: generating the dependent step must
        # wait for both results, even if that step would only use one of them.
        if model.messages:
            assert {"left", "right"} <= finished
        return generate(*args, **kwargs)

    def forward(*, path: str, content: str) -> str:
        del path
        if content != "dependent":
            siblings.wait(timeout=5)
        else:
            assert finished == {"left", "right"}
        with lock:
            finished.add(content)
        return content

    monkeypatch.setattr(model, "generate", generate_after_batch)
    monkeypatch.setattr(agent.tools["write"], "forward", forward)
    _events, steps, end = _round(runner)

    assert end["output"] == "done"
    assert finished == {"left", "right", "dependent"}
    assert [step.observations for step in steps[:2]] == [["left", "right"], ["dependent"]]
    assert len(model.messages) == 3


def test_react_keeps_successful_sibling_when_parallel_tool_fails(make_harness, monkeypatch) -> None:
    runner, agent, model = make_harness()
    model.outputs[:] = [_writes(runner, "broken", "healthy"), _submit("recovered")]
    siblings = threading.Barrier(2)

    def forward(*, path: str, content: str) -> str:
        del path
        siblings.wait(timeout=5)
        if content == "broken":
            raise RuntimeError("broken sibling")
        return content

    monkeypatch.setattr(agent.tools["write"], "forward", forward)
    events, steps, end = _round(runner)

    assert end["output"] == "recovered"
    assert "broken sibling" in steps[0].observations[0]
    assert steps[0].observations[1] == "healthy"
    assert [call["status"] for call in steps[0].tool_calls] == ["failed", "completed"]
    ends = [event["data"] for event in events
            if event["kind"] == "tool_end"
            and event["data"]["tool_name"] == "write"]
    assert len(ends) == 2
    for event in ends:
        assert event["observation"] == steps[0].observations[event["tool_index"]]
