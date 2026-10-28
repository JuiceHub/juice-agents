"""Per-step strategy configuration at the real gateway/Runner boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from adapters.stdio_gateway.handlers import RpcHandlers
from adapters.stdio_gateway.runtime import DirectRunnerRuntime
from juice_agents.core.agent.context.strategies import GreedyStrategy, TreeOfThoughtsStrategy
from juice_agents.core.registry.agents import registry as registry_module


class _Model:
    """A deterministic provider double; no network or external model reviews."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {
            "role": "assistant",
            "content": '<thought>done</thought><actions>[{"name":"submit_output","args":{"output":"done"}}]</actions>',
        }


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> _Model:
    model = _Model()
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **kwargs: model)
    return model


@pytest.mark.parametrize("params", [{}, {"breadth": 1}, {"reset": True}])
def test_default_and_reset_are_linear_without_workspace_writes(tmp_path: Path, params: dict) -> None:
    runtime = DirectRunnerRuntime(base_dir=tmp_path)
    result = runtime.switch_strategy(params)
    assert result["status"] == "pending"
    assert result["strategy"] == {
        "type": "greedy", "params": {}, "execution": "linear", "scorer_configured": False,
    }
    assert "线性执行" in result["message"]
    assert not (tmp_path / ".juice").exists()


@pytest.mark.parametrize(
    "params",
    [
        {"breadth": 0}, {"breadth": -2}, {"breadth": True}, {"breadth": 2.5},
        {"breadth": "3"}, {"breadth": [3, 2]}, {"breadth": None},
        {"depth": 2}, {"beam_width": 2}, {"temperature": 0.5},
        {"reset": "true"}, {"reset": True, "breadth": 3},
    ],
)
def test_gateway_rejects_removed_or_invalid_parameters(tmp_path: Path, params: dict) -> None:
    runtime = DirectRunnerRuntime(base_dir=tmp_path)
    with pytest.raises(ValueError):
        runtime.switch_strategy(params)
    assert runtime.pending_strategy_configuration is None
    assert not (tmp_path / ".juice").exists()


def test_pending_breadth_applies_on_first_stream_and_after_mode_change(tmp_path: Path, model: _Model) -> None:
    runtime = DirectRunnerRuntime(base_dir=tmp_path)
    result = runtime.switch_strategy({"breadth": 3})
    assert result["strategy"]["params"] == {"breadth": 3}
    assert result["strategy"]["execution"] == "linear"
    try:
        runtime.start_session()
        assert not runtime._runner.agent_manager.live_agents
        for index, mode in enumerate(["agent", "plan"]):
            if mode != runtime.agent_mode:
                runtime.switch_agent_mode(mode)
            events = list(runtime.stream_message("hello"))
            root = runtime._resolve_root_agent()
            assert isinstance(root.exploration_strategy, TreeOfThoughtsStrategy)
            assert root.exploration_strategy.breadth == 3
            assert root.exploration_strategy.reward_model is None
            assert next(event for event in events if event["kind"] == "round_end")["output"] == "done"
            assert model.calls == index + 1  # no scorer => one generation per step
    finally:
        runtime._close_runner(reason="test_finished")


def test_lazy_live_runner_accepts_strategy_and_reset(tmp_path: Path, model: _Model) -> None:
    runtime = DirectRunnerRuntime(base_dir=tmp_path)
    try:
        runtime.start_session()
        assert runtime.switch_strategy({"breadth": 4})["status"] == "pending"
        list(runtime.stream_message("first"))
        result = runtime.switch_strategy({"reset": True})
        assert result["status"] == "applied"
        assert isinstance(runtime._resolve_root_agent().exploration_strategy, GreedyStrategy)
        list(runtime.stream_message("second"))
        assert model.calls == 2
    finally:
        runtime._close_runner(reason="test_finished")


def test_live_breadth_change_preserves_injected_scorer(tmp_path: Path, model: _Model) -> None:
    class Scorer:
        def evaluate_batch(self, *, context: Any, actions: list[Any]) -> dict[str, Any]:
            return {"scores": [float(index) for index in range(len(actions))], "usage": None, "usage_calls": []}

    runtime = DirectRunnerRuntime(base_dir=tmp_path)
    try:
        list(runtime.stream_message("first"))
        scorer = Scorer()
        root = runtime._resolve_root_agent()
        root.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=2, reward_model=scorer))
        result = runtime.switch_strategy({"breadth": 4})
        assert result["status"] == "applied"
        assert result["strategy"]["execution"] == "candidate_selection"
        assert result["strategy"]["scorer_configured"] is True
        assert root.exploration_strategy.reward_model is scorer
        assert root.exploration_strategy.breadth == 4
        runtime.switch_strategy({"reset": True})
        assert isinstance(root.exploration_strategy, GreedyStrategy)
        assert model.calls == 1  # configuration does not make review requests
    finally:
        runtime._close_runner(reason="test_finished")


@pytest.mark.parametrize("resume", [False, True])
def test_rpc_pending_strategy_survives_start_or_resume(
    tmp_path: Path, model: _Model, resume: bool,
) -> None:
    runner_id = ""
    if resume:
        initial = DirectRunnerRuntime(base_dir=tmp_path)
        runner_id = initial.start_session().runner_id
        initial._close_runner(reason="test_finished")
    handlers = RpcHandlers()
    result = handlers.handle_switch_strategy({"base_dir": str(tmp_path), "breadth": 5})
    assert result["status"] == "pending"
    assert result["strategy"]["execution"] == "linear"
    try:
        if resume:
            handlers.handle_resume_session({"base_dir": str(tmp_path), "runner_id": runner_id})
        else:
            handlers.handle_start_session({"base_dir": str(tmp_path)})
        assert not handlers._runtime._runner.agent_manager.live_agents
        list(handlers.handle_stream_message({"message": "hello"}))
        root = handlers._runtime._resolve_root_agent()
        assert root.exploration_strategy.breadth == 5
        assert root.exploration_strategy.reward_model is None
        assert model.calls == 1
    finally:
        handlers._runtime._close_runner(reason="test_finished")


def test_pending_strategy_does_not_cross_workspaces(tmp_path: Path) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    handlers = RpcHandlers()
    handlers.handle_switch_strategy({"base_dir": str(tmp_path / "first"), "breadth": 3})
    try:
        handlers.handle_start_session({"base_dir": str(tmp_path / "second")})
        assert handlers._runtime._strategy is None
    finally:
        handlers._runtime._close_runner(reason="test_finished")
