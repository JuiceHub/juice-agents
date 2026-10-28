"""Reward-model configuration through the real gateway and lazy Runner path."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import openai

from adapters.stdio_gateway import runtime as runtime_module
from adapters.stdio_gateway.handlers import RpcHandlers
from adapters.stdio_gateway.runtime import DirectRunnerRuntime
from juice_agents.core.config import model_catalog
from juice_agents.core.registry.agents import registry as registry_module


class _Generator:
    """Distinct final answers let the test identify the selected candidate."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        actions = [{"name": "submit_output", "args": {"output": f"answer-{self.calls}"}}]
        return {"content": f"<thought>done</thought><actions>{json.dumps(actions)}</actions>"}


class _Judge:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, *, messages: Any, **kwargs: Any) -> Any:
        payload = json.loads(messages[1]["content"])
        self.calls.append(payload)
        # Prefer the last candidate. Selection must happen before execution.
        content = json.dumps({"scores": list(range(len(payload["candidates"])))})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch):
    generator, judge = _Generator(), _Judge()
    resolutions: list[dict[str, Any]] = []

    def resolve(**kwargs: Any):
        resolutions.append(kwargs)
        return model_catalog.RuntimeModelConfig(
            model_name=kwargs["model_name"], backend="openai", provider_model_name="provider-judge",
            api_key="test-key",
        )

    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **kwargs: generator)
    monkeypatch.setattr(model_catalog, "load_runtime_model_config", resolve)
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=judge)))
    monkeypatch.setattr(runtime_module, "list_runtime_models", lambda **kwargs: [{"model_name": "judge"}])
    return generator, judge, resolutions


def test_cold_reward_configuration_is_read_only_and_independent(tmp_path: Path, providers) -> None:
    generator, judge, resolutions = providers
    runtime = DirectRunnerRuntime(base_dir=tmp_path)
    assert runtime.configure_reward_model() == {
        "model_name": None, "scorer_configured": False, "breadth": 1, "execution": "linear",
    }
    assert runtime.pending_strategy_configuration is None
    selected = runtime.configure_reward_model({"model_name": "judge"})
    assert selected == {
        "model_name": "judge", "scorer_configured": True, "breadth": 1, "execution": "linear",
    }
    assert runtime.model_name == "doubao_lite"
    assert runtime.switch_strategy({"breadth": 3})["strategy"]["execution"] == "candidate_selection"
    assert runtime.switch_strategy({"reset": True})["strategy"]["execution"] == "linear"
    assert runtime.configure_reward_model()["model_name"] == "judge"
    runtime.switch_strategy({"breadth": 4})
    assert runtime.configure_reward_model({"reset": True}) == {
        "model_name": None, "scorer_configured": False, "breadth": 4, "execution": "linear",
    }
    assert runtime.pending_reward_model_configuration is None
    assert runtime._runner is None
    assert generator.calls == 0 and judge.calls == [] and resolutions == []
    assert list(tmp_path.iterdir()) == []


def test_first_reward_rpc_does_not_select_the_generation_model(tmp_path: Path, providers) -> None:
    handlers = RpcHandlers()
    configured = handlers.dispatch("configure_reward_model", {"base_dir": str(tmp_path), "model_name": "judge"})
    assert configured["model_name"] == "judge"
    assert handlers._runtime.model_name == "doubao_lite"
    assert handlers._runtime._runner is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("params", [
    {"model_name": "missing"}, {"model_name": ""}, {"model_name": True},
    {"model_name": None}, {"reset": "true"}, {"reset": True, "model_name": "judge"},
    {"breadth": 3},
])
def test_invalid_reward_updates_preserve_previous_choice(tmp_path: Path, providers, params) -> None:
    runtime = DirectRunnerRuntime(base_dir=tmp_path)
    previous = runtime.configure_reward_model({"model_name": "judge"})
    strategy = runtime._strategy
    with pytest.raises(ValueError):
        runtime.configure_reward_model(params)
    assert runtime.configure_reward_model() == previous
    assert runtime._strategy is strategy


def test_failed_root_update_does_not_publish_reward_choice(tmp_path: Path, providers, monkeypatch) -> None:
    runtime = DirectRunnerRuntime(base_dir=tmp_path)
    runtime.switch_strategy({"breadth": 3})
    previous = runtime._strategy

    def fail(strategy: Any) -> None:
        raise RuntimeError("root rejected update")

    root = SimpleNamespace(exploration_strategy=previous, set_exploration_strategy=fail)
    monkeypatch.setattr(runtime, "_resolve_root_agent", lambda: root)
    with pytest.raises(RuntimeError, match="rejected"):
        runtime.configure_reward_model({"model_name": "judge"})
    assert runtime._reward_model is None
    assert runtime._strategy is previous


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("reward_first", [False, True])
def test_cold_reward_and_breadth_survive_start_or_resume(
    tmp_path: Path, providers, resume: bool, reward_first: bool,
) -> None:
    generator, judge, resolutions = providers
    runner_id = ""
    if resume:
        initial = DirectRunnerRuntime(base_dir=tmp_path)
        try:
            runner_id = initial.start_session().runner_id
        finally:
            initial._close_runner(reason="test_finished")
    handlers = RpcHandlers()
    reward = lambda: handlers.dispatch("configure_reward_model", {"base_dir": str(tmp_path), "model_name": "judge"})
    breadth = lambda: handlers.handle_switch_strategy({"base_dir": str(tmp_path), "breadth": 3})
    for configure in ([reward, breadth] if reward_first else [breadth, reward]):
        configure()
    assert resolutions == []
    try:
        if resume:
            handlers.handle_resume_session({"base_dir": str(tmp_path), "runner_id": runner_id})
        else:
            handlers.handle_start_session({"base_dir": str(tmp_path)})
        runtime = handlers._runtime
        assert not runtime._runner.agent_manager.live_agents
        assert resolutions == []
        events = list(handlers.handle_stream_message({"message": "finish"}))
        assert next(event for event in events if event["kind"] == "round_end")["output"] == "answer-3"
        assert generator.calls == 3 and len(judge.calls) == 1
        assert len(resolutions) == 1
        configuration = resolutions[0]
        assert configuration["model_name"] == "judge" and configuration["workspace_dir"] == tmp_path
        assert configuration["runtime_config_path"] == runtime.runtime_config_path

        # Mode changes reacquire root; the same explicit scorer must bind there.
        runtime.switch_agent_mode("plan")
        list(runtime.stream_message("finish again"))
        assert generator.calls == 6 and len(judge.calls) == 2
        assert len(resolutions) == 1
        runtime.configure_reward_model({"reset": True})
        list(runtime.stream_message("linear again"))
        assert generator.calls == 7 and len(judge.calls) == 2
    finally:
        handlers._runtime._close_runner(reason="test_finished")


def test_reward_model_uses_the_same_project_and_workspace_catalog(tmp_path: Path) -> None:
    project = tmp_path / "catalog.yaml"
    project.write_text("models: {}\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    (workspace / ".juice").mkdir(parents=True)
    (workspace / ".juice" / "config.yaml").write_text(
        "models:\n  local_judge:\n    backend: openai\n    api_base: https://example.test/v1\n"
        "    api_key_env: UNUSED_TEST_KEY\n    provider_model_name: judge-provider\n", encoding="utf-8",
    )
    runtime = DirectRunnerRuntime(base_dir=workspace, runtime_config_path=project)
    names = [entry["model_name"] for entry in runtime.list_models()]
    assert names == ["local_judge"]
    assert runtime.configure_reward_model({"model_name": names[0]})["model_name"] == "local_judge"
    handlers = RpcHandlers()
    handlers.handle_configure_reward_model({
        "base_dir": str(workspace), "runtime_config_path": str(project), "model_name": "local_judge",
    })
    try:
        handlers.handle_start_session({"base_dir": str(workspace)})
        assert handlers._runtime.runtime_config_path == project
        assert handlers._runtime.describe_reward_model()["model_name"] == "local_judge"
    finally:
        handlers._runtime._close_runner(reason="test_finished")


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("root_acquired", [False, True])
def test_existing_session_keeps_reward_choice_when_replaced(
    tmp_path: Path, providers, resume: bool, root_acquired: bool,
) -> None:
    handlers = RpcHandlers()
    status = handlers.handle_start_session({"base_dir": str(tmp_path)})
    original = handlers._runtime
    try:
        handlers.handle_switch_strategy({"base_dir": str(tmp_path), "breadth": 3})
        handlers.handle_configure_reward_model({"base_dir": str(tmp_path), "model_name": "judge"})
        if root_acquired:
            list(original.stream_message("finish"))
        expected = original.describe_reward_model()
        if resume:
            handlers.handle_resume_session({"base_dir": str(tmp_path), "runner_id": status["runner_id"]})
        else:
            handlers.handle_start_session({"base_dir": str(tmp_path)})
        assert handlers._runtime is not original
        assert handlers._runtime.describe_reward_model() == expected
        # A replacement gets its own lazy scorer, never the old provider object.
        assert handlers._runtime._reward_model is not original._reward_model
    finally:
        original._close_runner(reason="test_finished")
        if handlers._runtime is not original:
            handlers._runtime._close_runner(reason="test_finished")


def test_cold_reward_model_does_not_transfer_to_another_workspace(tmp_path: Path, providers) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    handlers = RpcHandlers()
    handlers.handle_configure_reward_model({"base_dir": str(first), "model_name": "judge"})
    try:
        handlers.handle_start_session({"base_dir": str(second)})
        assert handlers._runtime.configure_reward_model()["scorer_configured"] is False
    finally:
        handlers._runtime._close_runner(reason="test_finished")
