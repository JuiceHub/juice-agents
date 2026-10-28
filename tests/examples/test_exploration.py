"""Exploration showcase installs its strategy before the lazy root executes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import openai

from examples import _runtime
from examples.agent_tot import __main__ as example
from juice_agents.core.agent.context.strategies import TreeOfThoughtsStrategy
from juice_agents.core.agent.context.steps import ActionStep
from juice_agents.core.config import model_catalog
from juice_agents.core.config.runtime_config import DEFAULT_RUNTIME_CONFIG_PATH
from juice_agents.core.registry.agents import registry as registry_module


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("with_scorer", [False, True])
def test_showcase_applies_strategy_before_stream(monkeypatch, capsys, resume: bool, with_scorer: bool) -> None:
    strategy = None
    scorer = object() if with_scorer else None

    def set_strategy(value):
        nonlocal strategy
        strategy = value

    def stream(task):
        # This assertion checks the previously missing integration: constructing
        # a strategy and printing it is insufficient unless the root receives it.
        assert isinstance(strategy, TreeOfThoughtsStrategy)
        assert strategy.breadth == 3
        if with_scorer:
            assert strategy.reward_model is scorer
        else:
            assert strategy.reward_model.model_name == "doubao_lite"
        assert task == "choose a design"
        yield {"kind": "round_end", "outcome": "submitted", "output": "done"}

    runner = SimpleNamespace(
        runner_id="example-root", stream=stream, stop=Mock(),
        root_config=SimpleNamespace(model_config_name="doubao_lite"),
        runtime_config_path=DEFAULT_RUNTIME_CONFIG_PATH,
        _root=lambda: SimpleNamespace(instance=SimpleNamespace(set_exploration_strategy=set_strategy)),
    )
    runners = SimpleNamespace(create=Mock(return_value=runner), resume=Mock(return_value=runner))
    monkeypatch.setattr(_runtime, "_juice_from_args", lambda *_args, **_kwargs: SimpleNamespace(runners=runners))
    argv = ["choose a design", "--strategy", "tot", "--breadth", "3"]
    if resume:
        argv.extend(["--resume", "old-runner"])

    assert example.main(argv, reward_model=scorer) == "done"
    runner.stop.assert_called_once()
    assert runners.resume.call_count == int(resume)
    assert runners.create.call_count == int(not resume)
    output = capsys.readouterr().out
    assert "每步候选选择" in output


@pytest.mark.parametrize("args", [
    ["--depth", "2"], ["--beam-width", "2"], ["--breadth", "[3,2]"],
    ["--breadth", "0"], ["--breadth", "-2"], ["--strategy", "beam_search"],
])
def test_showcase_rejects_removed_or_invalid_options(args) -> None:
    with pytest.raises(SystemExit):
        example.build_parser().parse_args(["task", *args])


def test_configuration_error_still_releases_runner(monkeypatch) -> None:
    runner = SimpleNamespace(runner_id="configure-error", stop=Mock())
    monkeypatch.setattr(_runtime, "create_or_resume_runner", lambda *_args, **_kwargs: runner)

    def configure(_runner):
        raise RuntimeError("configuration failed")

    with pytest.raises(RuntimeError, match="configuration failed"):
        _runtime.run_showcase(
            example.build_parser().parse_args(["task"]),
            agent_mode="agent", workspace=example.showcase_workspace(example.__file__),
            task="task", configure_runner=configure, writer=lambda _line: None,
        )
    runner.stop.assert_called_once()


def test_direct_main_file_can_show_help_from_another_directory(tmp_path: Path) -> None:
    entry = Path(example.__file__).resolve()
    completed = subprocess.run(
        [sys.executable, str(entry), "--help"], cwd=tmp_path,
        text=True, capture_output=True, check=True,
    )
    assert "--reward-model" in completed.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("workspace_model", [None, "gpt4o_mini"])
def test_default_demo_scores_real_runner_candidates_before_execution(monkeypatch, tmp_path: Path, capsys, workspace_model) -> None:
    """Only providers are fake: parse, Registry, Manager and ToT all run normally."""
    generation_calls = []
    scoring_calls = []
    resolved_models = []
    model_name = workspace_model or "doubao_lite"
    if workspace_model:
        config_dir = tmp_path / ".juice"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            f"runtime:\n  model_name: {workspace_model}\n", encoding="utf-8",
        )

    class Generator:
        def generate(self, messages, **kwargs):
            generation_calls.append(messages)
            actions = [{"name": "submit_output", "args": {"output": f"answer-{len(generation_calls)}"}}]
            return {"content": f"<thought>done</thought><actions>{json.dumps(actions)}</actions>"}

    class Judge:
        def create(self, *, messages, **kwargs):
            # All three generations finish before the batched review request.
            assert len(generation_calls) == 3
            scoring_calls.append(json.loads(messages[1]["content"]))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"scores": [1, 9, 2]}'))])

    generator, judge = Generator(), Judge()
    def resolve_generator(**kwargs):
        resolved_models.append(kwargs["model_name"])
        return generator

    monkeypatch.setattr(example, "showcase_workspace", lambda _: tmp_path)
    monkeypatch.setattr(registry_module, "resolve_runtime_model", resolve_generator)
    monkeypatch.setattr(model_catalog, "load_runtime_model_config", lambda **kwargs: model_catalog.RuntimeModelConfig(
        model_name=kwargs["model_name"], backend="openai", provider_model_name="provider-judge", api_key="test-key",
    ))
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=judge)))
    monkeypatch.setattr(example, "list_runtime_models", lambda **kwargs: [{"model_name": model_name}])
    assert example.main([]) == "answer-2"
    assert resolved_models == [model_name]
    assert len(generation_calls) == 3 and len(scoring_calls) == 1
    assert len(scoring_calls[0]["candidates"]) == 3
    output = capsys.readouterr().out
    assert "[tot] candidates=3 scores=[1.0, 9.0, 2.0] selected=2" in output
    assert f"奖励模型: {model_name}" in output
    assert "candidate 1:" in output and "candidate 3:" in output


def test_reward_model_flag_selects_an_independent_catalog_model(monkeypatch) -> None:
    applied = []
    root = SimpleNamespace(set_exploration_strategy=applied.append)
    runner = SimpleNamespace(
        root_config=SimpleNamespace(model_config_name="generator"),
        runtime_config_path=DEFAULT_RUNTIME_CONFIG_PATH,
        _root=lambda: SimpleNamespace(instance=root),
    )

    def run(args, **kwargs):
        kwargs["configure_runner"](runner)

    monkeypatch.setattr(example, "run_showcase", run)
    monkeypatch.setattr(example, "list_runtime_models", lambda **kwargs: [{"model_name": "judge"}])
    example.main(["--reward-model", "judge"])
    assert applied[0].reward_model.model_name == "judge"
    assert runner.root_config.model_config_name == "generator"


@pytest.mark.parametrize("argv,scorer", [
    (["--strategy", "greedy", "--reward-model", "judge"], None),
    (["--reward-model", "judge"], object()),
])
def test_conflicting_reward_configuration_is_rejected_before_running(monkeypatch, argv, scorer) -> None:
    run = Mock()
    monkeypatch.setattr(example, "run_showcase", run)
    with pytest.raises(SystemExit):
        example.main(argv, reward_model=scorer)
    run.assert_not_called()


def test_demo_trace_uses_validated_scores_and_actions(capsys) -> None:
    scorer = SimpleNamespace(evaluate_batch=lambda **kwargs: {"scores": [2, 8], "usage": None, "usage_calls": []})
    strategy = example.DemonstrationStrategy(breadth=2, reward_model=scorer)
    candidates = [
        ActionStep(step_num=1, model_output="first", tool_calls=[{"name": "read", "args": {"path": "first"}}]),
        ActionStep(step_num=1, model_output="second", tool_calls=[{"name": "read", "args": {"path": "second"}}]),
    ]
    assert strategy.select(context=[], candidates=candidates) == (1, [2.0, 8.0])
    trace = capsys.readouterr().out
    assert "selected=2" in trace and '"path": "second"' in trace


def test_tot_generation_and_reward_use_repository_credentials(monkeypatch, tmp_path, capsys, caplog) -> None:
    """Reproduce a direct launch with only root .env; replace only the provider.

    Unlike tests that replace Registry.resolve_runtime_model, this traverses the
    real catalog, API-key lookup and both generation and scoring model setup.
    """

    workspace = tmp_path / "examples" / "agent_tot"
    workspace.mkdir(parents=True)
    credential = "test-repository-credential"
    (tmp_path / ".env").write_text(f"DOUBAO_API_KEY={credential}\n", encoding="utf-8")
    monkeypatch.setattr(_runtime, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(example, "showcase_workspace", lambda _: workspace)
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path.parent)
    resolved_keys = []
    generation_calls = []
    review_calls = []

    class Provider:
        def __init__(self, **kwargs):
            resolved_keys.append(kwargs["api_key"])

        def generate(self, messages, **kwargs):
            generation_calls.append(messages)
            actions = [{"name": "submit_output", "args": {"output": f"demo-{len(generation_calls)}"}}]
            return {"content": f"<thought>done</thought><actions>{json.dumps(actions)}</actions>"}

    def create_review(*, messages, **kwargs):
        assert len(generation_calls) == 3
        review_calls.append(json.loads(messages[1]["content"]))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"scores": [1, 8, 2]}'))])

    def create_ark(**kwargs):
        # 保留真实目录/.env解析，只替换最终SDK边界，避免评分请求访问网络。
        resolved_keys.append(kwargs["api_key"])
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_review)))

    monkeypatch.setattr(model_catalog, "ChatModel", Provider)
    monkeypatch.setitem(sys.modules, "volcenginesdkarkruntime", SimpleNamespace(Ark=create_ark))
    assert example.main([]) == "demo-2"
    assert resolved_keys == [credential, credential]
    assert len(generation_calls) == 3 and len(review_calls) == 1
    assert "DOUBAO_API_KEY" not in os.environ
    output = capsys.readouterr()
    assert credential not in output.out + output.err + caplog.text
