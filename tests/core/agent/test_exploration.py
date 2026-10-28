"""Per-step candidate selection through the real Runner and AgentManager.

Only the provider is replaced: protocol parsing, tool execution, persistence,
and context projection use production components. All artifacts stay in tmp_path.
"""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from juice_agents.core.agent.context import (
    ActionStep,
    CompactCompressStrategy,
    TaskStep,
    deserialize_context_tree,
    serialize_context_tree,
)
from juice_agents.core.agent.context.nodes import NodeStatus
from juice_agents.core.agent.context.strategies import GreedyStrategy, TreeOfThoughtsStrategy
from juice_agents.core.models.reward_models import BaseRewardModel
from juice_agents.core.registry.agents import registry as agent_registry_module
from juice_agents.core.runner import Runner


def _react(actions: list[dict[str, Any]], thought: str = "candidate") -> str:
    return f"<thought>{thought}</thought><actions>{json.dumps(actions)}</actions>"


def _submit(output: str) -> str:
    return _react([{"name": "submit_output", "args": {"output": output}}])


def _write(path: Path, content: str) -> str:
    return _react([{"name": "write", "args": {"path": str(path), "content": content}}])


def _code(source: str) -> str:
    return f"<thought>candidate</thought><code>{source}</code>"


class _Model:
    """Record immutable request snapshots and give each candidate distinct usage."""

    def __init__(self) -> None:
        self.outputs: list[str] = []
        self.messages: list[Any] = []
        self._lock = threading.Lock()

    def generate(self, messages: Any, **_kwargs: Any) -> dict[str, Any]:
        with self._lock:
            if not self.outputs:
                raise AssertionError("model outputs exhausted")
            self.messages.append(copy.deepcopy(messages))
            return {
                "role": "assistant",
                "content": self.outputs.pop(0),
                "usage": {"output_tokens": len(self.messages)},
            }


class _Scorer(BaseRewardModel):
    """Custom reward model for real strategy selection with deterministic scores."""

    def __init__(self, *batches: list[float], error: Exception | None = None) -> None:
        self.batches = list(batches)
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def evaluate_batch(self, *, context: Any, actions: Any) -> dict[str, Any]:
        self.calls.append(copy.deepcopy({"context": context, "actions": actions}))
        if self.error is not None:
            raise self.error
        if not self.batches:
            raise AssertionError("score batches exhausted")
        return {"scores": self.batches.pop(0), "usage": None, "usage_calls": []}


@pytest.fixture
def make_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., tuple[Runner, Any, _Model]]]:
    runners: list[Runner] = []

    def create(*, agent_type: str = "react") -> tuple[Runner, Any, _Model]:
        workspace = tmp_path / str(len(runners))
        model = _Model()
        monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: model)
        runner = Runner.create(runner_config="agent", permission_mode="accept", base_dir=workspace)
        runner.root_config = replace(runner.root_config, agent_type=agent_type)
        runners.append(runner)
        return runner, runner._root().instance, model

    yield create
    for runner in runners:
        runner.close()


def _round(runner: Runner, task: str = "choose") -> tuple[list[Any], list[ActionStep], dict[str, Any]]:
    events = list(runner.stream(task))
    steps = [event["action_step"] for event in events if event["kind"] == "action_step"]
    end = next(event for event in events if event["kind"] == "round_end")
    return events, steps, end


@pytest.mark.parametrize("strategy", [None, GreedyStrategy(), TreeOfThoughtsStrategy(breadth=4)])
def test_default_and_unscored_strategies_keep_one_model_call(make_harness, strategy) -> None:
    runner, agent, model = make_harness()
    if strategy is not None:
        agent.set_exploration_strategy(strategy)
    model.outputs[:] = [_submit("linear")]

    _events, steps, end = _round(runner)

    assert end["output"] == "linear"
    assert len(model.messages) == len(steps) == 1


def test_breadth_one_never_calls_scorer(make_harness) -> None:
    runner, agent, model = make_harness()
    scorer = _Scorer(error=AssertionError("must not score"))
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=1, reward_model=scorer))
    model.outputs[:] = [_submit("linear")]

    assert _round(runner)[2]["output"] == "linear"
    assert len(model.messages) == 1
    assert scorer.calls == []


@pytest.mark.parametrize("breadth", [0, -1, True, 1.5, [3, 2], "3"])
def test_breadth_requires_a_positive_integer(breadth) -> None:
    with pytest.raises((TypeError, ValueError)):
        TreeOfThoughtsStrategy(breadth=breadth)


@pytest.mark.parametrize("removed", ["depth", "beam_width", "depth_limit", "keep_top_k", "temperature"])
def test_removed_strategy_parameters_are_rejected(removed: str) -> None:
    with pytest.raises(TypeError):
        TreeOfThoughtsStrategy(**{removed: 2})


def test_candidates_share_context_and_only_selected_branch_continues(make_harness) -> None:
    runner, agent, model = make_harness()
    first = [_react([], "REJECTED_A"), _react([], "SELECTED_B"), _react([], "REJECTED_C")]
    last = [_submit("lose"), _submit("winner"), _submit("also lose")]
    model.outputs[:] = first + last
    scorer = _Scorer([0.1, 0.9, 0.2], [-1.0, 2.0, 0.0])
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))

    _events, steps, end = _round(runner)

    assert end["output"] == "winner"
    assert [step.model_output for step in steps] == [first[1], last[1]]
    assert len(model.messages) == 6
    assert model.messages[0] == model.messages[1] == model.messages[2]
    assert model.messages[3] == model.messages[4] == model.messages[5]
    follow_up = json.dumps(model.messages[3], ensure_ascii=False)
    assert "SELECTED_B" in follow_up
    assert "REJECTED_A" not in follow_up and "REJECTED_C" not in follow_up
    assert scorer.calls[0] == {
        "context": model.messages[0],
        "actions": [{"role": "assistant", "content": raw} for raw in first],
    }

    # All six real generations survive, but only the two chosen nodes carry
    # execution results; no extra node is appended after an execution finishes.
    nodes = [node for node in agent.context.nodes.values() if isinstance(node.step, ActionStep)]
    assert len(nodes) == 6
    assert len(agent.context.active_leaf_ids) == 1
    assert sorted(node.score for node in nodes) == [-1.0, 0.0, 0.1, 0.2, 0.9, 2.0]
    rejected = [node for node in nodes if node.status is NodeStatus.REJECTED]
    assert len(rejected) == 4
    assert all(node.step.usage and not node.step.observations for node in rejected)
    assert sorted(node.step.usage["output_tokens"] for node in nodes) == list(range(1, 7))
    assert all(any(node.step is step for node in nodes) for step in steps)


def test_react_executes_selected_write_once_and_persists_stream_result(make_harness, tmp_path, monkeypatch) -> None:
    runner, agent, model = make_harness()
    # Candidates may only mutate the Runner workspace; tmp_path itself is its
    # parent and is deliberately outside the file-tool root after binding.
    rejected, chosen = runner.base_dir / "rejected.txt", runner.base_dir / "chosen.txt"
    model.outputs[:] = [_write(rejected, "bad"), _write(chosen, "good"), _submit("done"), _submit("lose")]
    scorer = _Scorer([0.0, 1.0], [1.0, 0.0])
    evaluate = scorer.evaluate_batch

    def score_before_execution(**kwargs: Any) -> dict[str, Any]:
        # Generation must finish before either candidate can mutate the world.
        if not scorer.calls:
            assert len(model.messages) == 2
            assert not rejected.exists() and not chosen.exists()
        return evaluate(**kwargs)

    monkeypatch.setattr(scorer, "evaluate_batch", score_before_execution)
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=scorer))
    tool = agent.tools["write"]
    original_forward = tool.forward
    calls: list[dict[str, Any]] = []

    def forward(**kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        return original_forward(**kwargs)

    monkeypatch.setattr(tool, "forward", forward)
    events, steps, end = _round(runner)

    assert end["output"] == "done"
    assert len(calls) == 1
    assert chosen.read_text() == "good" and not rejected.exists()
    # The event and checkpoint must reuse the exact same execution outcome.
    tool_end = next(event["data"] for event in events if event["kind"] == "action_event" and event["event_type"] == "tool_end" and event["data"]["tool_name"] == "write")
    assert tool_end["observation"] == steps[0].observations[0]


def test_codeact_does_not_execute_rejected_code(make_harness, tmp_path) -> None:
    runner, agent, model = make_harness(agent_type="codeact")
    rejected, chosen = runner.base_dir / "rejected-code.txt", runner.base_dir / "chosen-code.txt"
    model.outputs[:] = [
        _code(f"write(path={str(rejected)!r}, content='bad')\nsubmit_output(output='bad')"),
        _code(f"write(path={str(chosen)!r}, content='good')\nsubmit_output(output='done')"),
    ]
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=_Scorer([0.0, 1.0])))

    _events, steps, end = _round(runner)

    assert end["output"] == "done"
    assert len(steps) == 1 and len(model.messages) == 2
    assert chosen.read_text() == "good" and not rejected.exists()


@pytest.mark.parametrize("invalid", ["", "plain text", _react([{"name": "missing_tool", "args": {}}])])
def test_invalid_candidates_are_excluded_from_scoring(make_harness, invalid: str) -> None:
    runner, agent, model = make_harness()
    valid = _submit("valid")
    model.outputs[:] = [invalid, valid]
    scorer = _Scorer([0.7])
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=scorer))

    assert _round(runner)[2]["output"] == "valid"
    assert scorer.calls[0]["actions"] == [{"role": "assistant", "content": valid}]
    invalid_node = next(node for node in agent.context.nodes.values() if isinstance(node.step, ActionStep) and node.step.model_output == invalid)
    assert invalid_node.status is NodeStatus.REJECTED and invalid_node.score is None


def test_all_invalid_candidates_recover_using_first_output(make_harness) -> None:
    runner, agent, model = make_harness()
    model.outputs[:] = ["first malformed", "second malformed", _submit("fixed"), _submit("lose")]
    scorer = _Scorer([1.0, 0.0])
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=scorer))

    _events, steps, end = _round(runner)

    assert end["output"] == "fixed"
    assert steps[0].model_output == "first malformed"
    assert steps[0].error is not None and steps[0].round_outcome == "continue"
    assert len(scorer.calls) == 1
    assert "first malformed" in json.dumps(model.messages[2])
    assert "second malformed" not in json.dumps(model.messages[2])


@pytest.mark.parametrize("invalid", ["", "plain text", _code("   ")])
def test_codeact_scores_only_protocol_valid_candidates(make_harness, invalid: str) -> None:
    runner, agent, model = make_harness(agent_type="codeact")
    valid = _code("submit_output(output='valid')")
    model.outputs[:] = [invalid, valid]
    scorer = _Scorer([1.0])
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=scorer))

    assert _round(runner)[2]["output"] == "valid"
    assert scorer.calls[0]["actions"] == [{"role": "assistant", "content": valid}]


def test_ties_choose_first_generated_candidate(make_harness) -> None:
    runner, agent, model = make_harness()
    model.outputs[:] = [_submit("first"), _submit("second")]
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=_Scorer([0.0, 0.0])))
    assert _round(runner)[2]["output"] == "first"


def test_step_budget_counts_executions_and_keeps_existing_submission_fallback(make_harness) -> None:
    runner, agent, model = make_harness()
    agent.max_steps = 1
    model.outputs[:] = [_react([], "lose"), _react([], "continue"), _submit("final"), _submit("lose")]
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(
        breadth=2, reward_model=_Scorer([0.0, 1.0], [1.0, 0.0]),
    ))

    _events, steps, end = _round(runner)

    assert end["output"] == "final"
    assert [step.step_num for step in steps] == [1, 2]  # one execution + existing final submission
    assert len(model.messages) == 4
    assert "[runtime]" in json.dumps(model.messages[2], ensure_ascii=False)
    assert len([node for node in agent.context.nodes.values() if isinstance(node.step, ActionStep)]) == 4


@pytest.mark.parametrize("scores,error,expected", [
    ([], None, "候选评分数量不匹配"),
    ([1.0], None, "候选评分数量不匹配"),
    ([1.0, float("nan")], None, "候选评分必须是有限数值"),
    ([1.0, float("inf")], None, "候选评分必须是有限数值"),
    ([1.0, "1.0"], None, "候选评分必须是有限数值"),
    ([1.0, True], None, "候选评分必须是有限数值"),
    (None, RuntimeError("judge unavailable"), "候选评分失败: judge unavailable"),
])
def test_scoring_failure_stops_before_any_action(make_harness, tmp_path, scores, error, expected) -> None:
    runner, agent, model = make_harness()
    target = tmp_path / "must-not-execute.txt"
    model.outputs[:] = [_write(target, "bad"), _submit("unscored")]
    scorer = _Scorer(*([] if scores is None else [scores]), error=error)
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=scorer))

    _events, steps, end = _round(runner)

    assert end["outcome"] == "failed"
    assert steps[-1].error is not None
    assert expected in str(steps[-1].error)
    assert not target.exists()
    assert len(model.messages) == 2 and len(scorer.calls) == 1


def test_serialization_resume_and_reset_keep_only_selected_context(make_harness) -> None:
    runner, agent, model = make_harness()
    model.outputs[:] = [_submit("discarded"), _submit("kept")]
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=_Scorer([0.0, 1.0])))
    assert _round(runner, "first task")[2]["output"] == "kept"
    payload = serialize_context_tree(agent.context)
    restored = deserialize_context_tree(payload)
    assert serialize_context_tree(restored) == payload
    assert "discarded" not in json.dumps(restored.to_messages())
    assert "kept" in json.dumps(restored.to_messages())
    assert "reward_model" not in json.dumps(payload)

    # Releasing and acquiring uses the actual persisted snapshot and constructs
    # a fresh Agent. A runtime scorer must never be revived by deserialization.
    runner.agent_manager.release(runner.agent_manager.get("root"))
    fresh = runner._root().instance
    assert fresh is not agent
    model.outputs[:] = [_submit("resumed")]
    assert _round(runner, "second task")[2]["output"] == "resumed"
    assert len(model.messages) == 3
    second_context = json.dumps(model.messages[-1])
    assert "kept" in second_context and "discarded" not in second_context
    assert [step.task for step in fresh.context.steps if isinstance(step, TaskStep)] == ["first task", "second task"]

    model.outputs[:] = [_submit("reset")]
    assert runner.agent_manager.run(runner.agent_manager.get("root"), "clean task", reset_session=True) == "reset"
    assert [step.task for step in fresh.context.steps if isinstance(step, TaskStep)] == ["clean task"]
    assert all("discarded" not in node.step.model_output for node in fresh.context.nodes.values() if isinstance(node.step, ActionStep))
    assert len(fresh.context.active_leaf_ids) == 1


def test_compaction_summarizes_selected_branch_and_preserves_rejected_nodes(make_harness) -> None:
    runner, agent, model = make_harness()
    model.outputs[:] = [_submit("REJECTED_HISTORY"), _submit("SELECTED_HISTORY")]
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=_Scorer([0.0, 1.0])))
    _round(runner)
    summary_model = _Model()
    summary_model.outputs[:] = ["summary of selected history"]
    strategy = CompactCompressStrategy(step_threshold=1, compression_model=summary_model, preserve_recent_actions=0)

    compressed = strategy.compress(agent.context)

    summary_request = json.dumps(summary_model.messages[0])
    assert "SELECTED_HISTORY" in summary_request and "REJECTED_HISTORY" not in summary_request
    assert any(node.status is NodeStatus.REJECTED for node in compressed.nodes.values())
    assert len(compressed.active_leaf_ids) == 1
    assert "REJECTED_HISTORY" not in json.dumps(compressed.to_messages())


@pytest.mark.parametrize("incremental", [True, False])
def test_compaction_preserves_selected_candidate_metadata_without_mutating_history(make_harness, incremental: bool) -> None:
    runner, agent, model = make_harness()
    model.outputs[:] = [_submit("discarded"), _submit("selected")]
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=_Scorer([0.1, 0.9])))
    _round(runner)
    original = agent.context.get_active_leaves()[0]
    original.generated_by = "provider/model"
    original.generation_params["details"] = {"sample": 1}
    original.annotations["review"] = {"accepted": True}
    original_payload = serialize_context_tree(agent.context)["nodes"][original.node_id]
    summary_model = _Model()
    summary_model.outputs[:] = ["<summary>earlier task</summary>"]
    strategy = CompactCompressStrategy(
        step_threshold=1, compression_model=summary_model,
        preserve_recent_actions=1, incremental=incremental,
    )

    compacted = strategy.compress(agent.context)

    preserved = compacted.get_active_leaves()[0]
    assert preserved.node_id != original.node_id
    assert preserved.step.model_output == original.step.model_output
    assert preserved.score == 0.9
    assert preserved.generated_by == "provider/model"
    assert preserved.generation_strategy == "TreeOfThoughtsStrategy"
    assert preserved.generation_params == original.generation_params
    assert preserved.status is original.status
    assert preserved.annotations == {**original.annotations, "source_node_id": original.node_id}
    assert preserved.children_ids == []
    assert serialize_context_tree(compacted)["nodes"][original.node_id] == original_payload

    # Editing the current branch must not rewrite the archived candidate,
    # including when both nodes belong to the same incrementally updated tree.
    preserved.generation_params["details"]["sample"] = 2
    preserved.annotations["review"]["accepted"] = False
    assert compacted.nodes[original.node_id].generation_params["details"] == {"sample": 1}
    assert compacted.nodes[original.node_id].annotations["review"] == {"accepted": True}
    assert serialize_context_tree(agent.context)["nodes"][original.node_id] == original_payload


def test_run_and_stream_return_same_selected_output(make_harness) -> None:
    outputs = []
    for streaming in (False, True):
        runner, agent, model = make_harness()
        model.outputs[:] = [_submit("lose"), _submit("same result")]
        agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=_Scorer([0.0, 1.0])))
        outputs.append(_round(runner)[2]["output"] if streaming else runner.run("choose"))
        assert len(model.messages) == 2
    assert outputs == ["same result", "same result"]
