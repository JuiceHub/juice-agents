"""Token accounting through real SDK adapters, exploration and persistence.

Only the provider's ``create`` call is replaced. Keeping the actual ChatModel,
GRM, AgentManager and Runner catches usage lost between those boundaries.
"""

from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from juice_agents.core.agent.context import (
    ActionStep,
    CompactCompressStrategy,
    ContextTree,
    deserialize_context_tree,
    serialize_context_tree,
)
from juice_agents.core.agent.context.strategies import GreedyStrategy, TreeOfThoughtsStrategy
from juice_agents.core.agent.manager import AgentManager
from juice_agents.core.models import ChatModel, GenerativeRewardModel
from juice_agents.core.registry.agents import registry as registry_module
from juice_agents.core.runner import Runner


def _submit(output: str) -> str:
    actions = [{"name": "submit_output", "args": {"output": output}}]
    return f"<thought>choose</thought><actions>{json.dumps(actions)}</actions>"


class _Stream:
    """Native stream protocol, including the trailing usage-only chunk."""

    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks

    def __iter__(self) -> Iterator[Any]:
        return iter(self.chunks)

    def close(self) -> None:
        pass


class _Endpoint:
    """Thread-safe fake SDK endpoint; no Agent/model behavior is replaced."""

    def __init__(self, outputs: list[str], usage: dict[str, int] | None) -> None:
        self.outputs = outputs
        self.usage = usage
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def create(self, **kwargs: Any) -> Any:
        with self.lock:
            self.requests.append(copy.deepcopy(kwargs))
            if not self.outputs:
                raise AssertionError("Unexpected extra SDK request")
            content = self.outputs.pop(0)
        usage = SimpleNamespace(**self.usage) if self.usage is not None else None
        if kwargs.get("stream"):
            return _Stream([
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))], usage=None),
                SimpleNamespace(choices=[], usage=usage),
            ])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(role="assistant", content=content))],
            usage=usage,
        )

    def client(self) -> Any:
        return SimpleNamespace(chat=SimpleNamespace(completions=self))


def _counts(requests: int, input_tokens: int, output_tokens: int, missing: int = 0) -> dict[str, int]:
    return {
        "requests": requests,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "missing_usage_requests": missing,
    }


def _expected(*, generations: int = 3, missing: bool = False) -> dict[str, dict[str, int]]:
    return {
        "generation": _counts(generations, 0 if missing else 10 * generations, 0 if missing else 2 * generations, generations if missing else 0),
        "reward": _counts(1, 0 if missing else 20, 0 if missing else 4, 1 if missing else 0),
        "total": _counts(generations + 1, 0 if missing else 10 * generations + 20, 0 if missing else 2 * generations + 4, generations + 1 if missing else 0),
    }


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runners: list[Runner] = []

    def create(*, missing: bool = False, judge: _Endpoint | None = None, empty_retry: bool = False):
        workspace = tmp_path / str(len(runners))
        outputs = [_submit("discarded A"), _submit("selected B"), _submit("discarded C")]
        if empty_retry:
            outputs.insert(0, "")
        generation = _Endpoint(outputs, None if missing else {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
        judge = judge or _Endpoint(['{"scores":[1,9,2]}'], None if missing else {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24})
        model = ChatModel(model_name="generation-model", client=generation.client(), wait_time=0)
        scorer = GenerativeRewardModel(model_name="reward-model", client=judge.client())
        monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: model)
        runner = Runner.create(runner_config="agent", permission_mode="accept", base_dir=workspace)
        runners.append(runner)
        return runner, generation, scorer, judge, workspace

    yield create, runners
    for runner in runners:
        runner.close()


def _configure(runner: Runner, scorer: GenerativeRewardModel) -> Any:
    agent = runner._root().instance
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))
    return agent


def test_usage_query_keeps_new_runner_root_lazy(harness) -> None:
    create, _runners = harness
    runner, generation, _scorer, judge, _workspace = create()

    assert runner.token_usage() == {kind: _counts(0, 0, 0) for kind in ("generation", "reward", "total")}
    assert not runner.agent_manager.live_agents
    assert generation.requests == judge.requests == []


def test_exploration_counts_rejected_candidates_and_reward_after_cold_resume(harness) -> None:
    create, runners = harness
    runner, generation, scorer, judge, workspace = create()
    agent = _configure(runner, scorer)

    assert runner.run("choose") == "selected B"
    assert len(generation.requests) == 3 and len(judge.requests) == 1
    assert runner.agent_manager.token_usage() == runner.token_usage() == _expected()
    calls = runner.agent_manager.model_usage_records()
    assert [call["kind"] for call in calls].count("generation") == 3
    assert [call["kind"] for call in calls].count("reward") == 1
    assert {call["model_name"] for call in calls} == {"generation-model", "reward-model"}
    assert {call["backend"] for call in calls} == {"openai"}
    assert {call["agent_id"] for call in calls} == {"root"}
    assert {call["agent_name"] for call in calls} == {"root"}
    # Accounting metadata must never become conversation content.
    assert "model_calls" not in json.dumps(agent.context.to_messages())

    snapshot = runner.agent_manager.load_snapshot("root")
    assert "model_calls" not in snapshot["session"]
    assert "usage_calls" not in snapshot["session"]
    assert not hasattr(agent.context, "model_calls")
    assert not hasattr(agent.context, "token_usage")
    runner.agent_manager.release(runner.agent_manager.get("root"))
    assert runner.token_usage() == _expected()
    resumed = Runner.resume(runner_id=runner.runner_id, base_dir=workspace)
    runners.append(resumed)
    assert not resumed.agent_manager.live_agents
    assert resumed.token_usage() == _expected()
    # Reading a cold aggregate must not revive an Agent or count its snapshot
    # twice when the same session is acquired again.
    assert not resumed.agent_manager.live_agents
    resumed._root()
    assert resumed.token_usage() == _expected()
    assert resumed.agent_manager.model_usage_records() == calls


def test_invalid_reward_scores_are_charged_and_checkpointed(harness) -> None:
    create, _runners = harness
    judge = _Endpoint(['{"scores":[1]}'], {"prompt_tokens": 20, "completion_tokens": 4})
    runner, _generation, scorer, _judge, _workspace = create(judge=judge)
    agent = _configure(runner, scorer)

    events = list(runner.stream("choose"))

    assert events[-1]["outcome"] == "failed"
    assert runner.agent_manager.token_usage() == runner.token_usage() == _expected()
    runner.agent_manager.release(runner.agent_manager.get("root"))
    assert runner.token_usage() == _expected()


def test_runner_aggregates_released_worker_and_live_root_once(harness) -> None:
    create, _runners = harness
    runner, _generation, scorer, _judge, _workspace = create()
    root = _configure(runner, scorer)
    assert runner.run("choose") == "selected B"
    worker_endpoint = _Endpoint([_submit("worker done")], {"prompt_tokens": 7, "completion_tokens": 3})
    worker = runner.agent_manager.acquire(
        replace(runner.root_config, name="worker"),
        lifecycle="functional",
        role="worker",
        model=ChatModel(model_name="worker-model", client=worker_endpoint.client()),
    )

    assert runner.agent_manager.run(worker, "work") == "worker done"
    assert worker.instance is None
    expected = _expected()
    expected["generation"] = _counts(4, 37, 9)
    expected["total"] = _counts(5, 57, 13)
    assert runner.token_usage() == expected
    assert len([call for call in runner.agent_manager.model_usage_records() if call["agent_id"] == "root"]) == 4
    assert not any(managed.agent_name == "worker" for managed in runner.agent_manager.live_agents.values())


def test_missing_provider_usage_remains_visible_as_missing_requests(harness) -> None:
    create, _runners = harness
    runner, _generation, scorer, _judge, _workspace = create(missing=True)
    agent = _configure(runner, scorer)

    assert runner.run("choose") == "selected B"
    assert runner.agent_manager.token_usage() == runner.token_usage() == _expected(missing=True)
    assert all(call["usage"] is None for call in runner.agent_manager.model_usage_records())


def test_empty_response_retry_is_counted_in_addition_to_action_steps(harness) -> None:
    create, _runners = harness
    runner, generation, scorer, _judge, _workspace = create(empty_retry=True)
    agent = _configure(runner, scorer)

    assert runner.run("choose") == "selected B"
    assert len(generation.requests) == 4
    assert len([node for node in agent.context.nodes.values() if isinstance(node.step, ActionStep)]) == 3
    assert runner.token_usage() == _expected(generations=4)


@pytest.mark.parametrize("incremental", [True, False])
def test_compaction_preserves_ledger_without_counting_cloned_actions_twice(harness, incremental: bool) -> None:
    create, _runners = harness
    runner, generation, scorer, _judge, _workspace = create()
    agent = _configure(runner, scorer)
    assert runner.run("choose") == "selected B"
    summary = _Endpoint(["<summary>selected history</summary>"], {"prompt_tokens": 5, "completion_tokens": 1})
    agent.set_session_compression_strategy(CompactCompressStrategy(
        step_threshold=1,
        preserve_recent_actions=1,
        incremental=incremental,
        compression_model=ChatModel(model_name="summary-model", client=summary.client()),
    ))
    agent.set_exploration_strategy(GreedyStrategy())
    generation.outputs.append(_submit("after compression"))

    # Trigger compression inside a real managed step: its summary call is a
    # generation request owned by this Agent, even when compression clones the
    # tree before receiving the response.
    assert runner.run("continue") == "after compression"
    compressed = agent.context
    expected = _expected()
    expected["generation"] = _counts(5, 45, 9)
    expected["total"] = _counts(6, 65, 13)
    assert runner.token_usage() == expected
    calls = runner.agent_manager.model_usage_records()
    assert len(calls) == 6
    payload = serialize_context_tree(compressed)
    assert "model_calls" not in payload
    restored = deserialize_context_tree(payload)
    assert serialize_context_tree(restored) == payload
    # A restored or compressed transcript contributes no additional requests.
    assert runner.token_usage() == expected
    assert runner.agent_manager.model_usage_records() == calls


def test_ephemeral_worker_usage_survives_release_and_cold_restore(harness) -> None:
    create, runners = harness
    runner, _generation, _scorer, _judge, workspace = create()
    endpoint = _Endpoint([_submit("ephemeral done")], {"prompt_tokens": 7, "completion_tokens": 3})
    worker = runner.agent_manager.acquire(
        replace(runner.root_config, name="ephemeral-worker"),
        lifecycle="functional",
        role="worker",
        ephemeral=True,
        model=ChatModel(model_name="ephemeral-model", client=endpoint.client()),
    )

    assert runner.agent_manager.run(worker, "work") == "ephemeral done"
    assert worker.instance is None
    assert runner.agent_manager.load_snapshot(worker.agent_id) is None
    expected = {
        "generation": _counts(1, 7, 3),
        "reward": _counts(0, 0, 0),
        "total": _counts(1, 7, 3),
    }
    assert runner.token_usage() == expected
    calls = runner.agent_manager.model_usage_records()
    assert calls[0]["agent_id"] == worker.agent_id
    assert calls[0]["agent_name"] == "ephemeral-worker"
    # Persist only call metadata: ephemeral prompt/response content is absent.
    usage_path = runner.layout.agents_dir / "model_usage.jsonl"
    ledger_text = usage_path.read_text(encoding="utf-8")
    assert "ephemeral done" not in ledger_text
    assert "messages" not in ledger_text
    resumed = Runner.resume(runner_id=runner.runner_id, base_dir=workspace)
    runners.append(resumed)
    assert not resumed.agent_manager.live_agents
    assert resumed.token_usage() == expected
    assert resumed.agent_manager.model_usage_records() == calls


def test_shared_reward_sdk_client_does_not_mix_concurrent_runner_usage(harness) -> None:
    create, _runners = harness
    judge = _Endpoint(['{"scores":[1,9,2]}'] * 2, {"prompt_tokens": 20, "completion_tokens": 4})
    first, _generation, scorer, _judge, _workspace = create(judge=judge)
    first_agent = _configure(first, scorer)
    second, _generation, _unused, _judge, _workspace = create(judge=judge)
    second_agent = _configure(second, scorer)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda runner: runner.run("choose"), [first, second]))

    assert results == ["selected B", "selected B"]
    assert first.token_usage() == second.token_usage() == _expected()
    assert len(first.agent_manager.model_usage_records()) == len(second.agent_manager.model_usage_records()) == 4
    assert len(judge.requests) == 2


def test_standalone_agent_manager_collects_and_restores_without_runner(tmp_path: Path) -> None:
    """Manager owns accounting even when no Runner/callback composition exists."""
    endpoint = _Endpoint(["standalone done"], {"prompt_tokens": 7, "completion_tokens": 3})
    model = ChatModel(model_name="standalone-model", client=endpoint.client())

    class Agent:
        def __init__(self) -> None:
            self.name = "worker"
            self.agent_id = ""
            self.max_steps = 1
            self.context = ContextTree(tree_id="standalone", system_prompt="test")

        def step(self, step: ActionStep) -> ActionStep:
            response = model.generate([{"role": "user", "content": "do work"}])
            self.model_usage_handler(kind="generation", model=model, response=response)
            step.model_output = response["content"]
            step.usage = response["usage"]
            step.round_outcome = "submitted"
            step.output = response["content"]
            return step

    registry = SimpleNamespace(instantiate=lambda *_args, **_kwargs: Agent())
    manager = AgentManager(registry=registry, runner_id="standalone", state_dir=tmp_path)
    managed = manager.acquire({"name": "worker"}, lifecycle="functional")
    assert manager.run(managed, "work") == "standalone done"
    expected = {
        "generation": _counts(1, 7, 3),
        "reward": _counts(0, 0, 0),
        "total": _counts(1, 7, 3),
    }
    assert manager.token_usage() == expected
    assert managed.instance is None
    records = manager.model_usage_records()
    assert len(records) == 1
    assert records[0]["agent_name"] == "worker"
    resumed = AgentManager(registry=registry, runner_id="standalone", state_dir=tmp_path)
    assert resumed.token_usage() == expected
    assert not resumed.live_agents
    assert resumed.model_usage_records() == records


def test_cancelled_stream_usage_reaches_manager_and_survives_cold_restore(harness) -> None:
    """Cancellation still reports response usage through the exception path."""
    create, runners = harness
    runner, generation, scorer, judge, workspace = create()
    _configure(runner, scorer)
    closed = threading.Event()

    class CancelledStream:
        def __iter__(self) -> Iterator[Any]:
            # Interrupt at the SDK boundary, after the request has started.
            # The arrived usage chunk must be counted before cancellation is
            # propagated through ChatModel and Agent's explicit error report.
            runner.request_stop_stream(reason="user cancelled")
            yield SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10),
            )

        def close(self) -> None:
            closed.set()

    def create_cancelled(**kwargs: Any) -> CancelledStream:
        generation.requests.append(copy.deepcopy(kwargs))
        return CancelledStream()

    generation.create = create_cancelled
    events = list(runner.stream("cancel this request"))

    assert events[-1]["kind"] == "stream_cancelled"
    assert closed.is_set()
    assert len(generation.requests) == 1
    assert judge.requests == []
    expected = {
        "generation": _counts(1, 7, 3),
        "reward": _counts(0, 0, 0),
        "total": _counts(1, 7, 3),
    }
    assert runner.agent_manager.token_usage() == runner.token_usage() == expected
    records = runner.agent_manager.model_usage_records()
    assert len(records) == 1 and records[0]["agent_id"] == "root"
    resumed = Runner.resume(runner_id=runner.runner_id, base_dir=workspace)
    runners.append(resumed)
    assert not resumed.agent_manager.live_agents
    assert resumed.token_usage() == expected
    assert resumed.agent_manager.model_usage_records() == records


def test_exhausted_empty_candidate_retains_last_response_usage_and_all_requests(harness) -> None:
    """The rejected step owns its last response; Manager owns all retries."""
    create, _runners = harness
    judge = _Endpoint(['{"scores":[9]}'], {"prompt_tokens": 20, "completion_tokens": 4})
    runner, generation, scorer, _judge, _workspace = create(judge=judge)
    generation.outputs[:] = ["", "", "", _submit("valid after exhausted retries")]
    agent = runner._root().instance
    agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=scorer))

    assert runner.run("choose valid candidate") == "valid after exhausted retries"
    assert len(generation.requests) == 4
    assert runner.token_usage() == _expected(generations=4)
    empty = next(
        node.step for node in agent.context.nodes.values()
        if isinstance(node.step, ActionStep) and node.step.model_output == ""
    )
    assert empty.error is not None
    assert empty.usage["input_tokens"] == 10
    assert empty.usage["output_tokens"] == 2
    assert empty.usage["total_tokens"] == 12
    # Snapshot round trips preserve response metadata without treating it as
    # another request or summing all retry usage onto a single ActionStep.
    snapshot = runner.agent_manager.load_snapshot("root")
    restored = runner.agent_manager.deserialize_snapshot(snapshot)
    restored_empty = next(
        node.step for node in restored.nodes.values()
        if isinstance(node.step, ActionStep) and node.step.model_output == ""
    )
    assert restored_empty.usage == empty.usage
    assert len(runner.agent_manager.model_usage_records()) == 5
    assert runner.token_usage() == _expected(generations=4)
