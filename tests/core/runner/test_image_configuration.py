"""Image catalog isolation and configuration reload through real Managers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest

from juice_agents.core.agent.builtin.builder import build_general_agent
from juice_agents.core.agent.context.strategies import TreeOfThoughtsStrategy
from juice_agents.core.agent.tools.builtin.images.images_tools import GenerateEditImageTool
from juice_agents.core.config.model_catalog import load_runtime_model_config
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.models.reward_models import GenerativeRewardModel
from juice_agents.core.registry.agents import registry as registry_module
from juice_agents.core.registry.tools import ToolRegistry
from juice_agents.core.runner import Runner
from juice_agents.core.runner.errors import RunnerBusyError


def _catalog(version=1, *, enabled=True):
    return {
        "providers": {
            "language_service": {"backend": "openai", "api_base": f"https://chat{version}.test/v1", "api_key_env": "TEST_CHAT_KEY"},
            "image_service": {"backend": "doubao", "api_base": f"https://image{version}.test/api/v3", "api_key_env": "TEST_IMAGE_KEY"},
        },
        "models": {
            "chat": {"provider": "language_service", "provider_model_name": f"chat-v{version}"},
            "judge": {"provider": "language_service", "provider_model_name": f"judge-v{version}"},
        },
        "image_models": {"draw": {"provider": "image_service", "provider_model_name": f"image-v{version}"}},
        "runtime": {"model_name": "chat", "agent_type": "react", "model_effort": "disabled"},
        "image": {"enabled": enabled, "model_name": "draw"},
        "browser": {"enabled": False},
    }


def _write_config(workspace: Path, payload):
    path = workspace / ".juice" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def offline_models(tmp_path, monkeypatch):
    """Replace only generation and SDK connections; keep catalog resolution."""

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TEST_CHAT_KEY", "chat-secret")
    monkeypatch.setenv("TEST_IMAGE_KEY", "image-secret")

    class Model:
        def __init__(self, connection):
            self.connection = connection

        def generate(self, messages, **kwargs):
            action = {"name": "submit_output", "args": {"output": self.connection.provider_model_name}}
            return {"role": "assistant", "content": "<thought>answer</thought><actions>" + json.dumps([action]) + "</actions>"}

    def resolve(**kwargs):
        # Registry passes effort separately; it is not part of connection
        # resolution. Every fresh Agent must resolve the current provider.
        return Model(load_runtime_model_config(**{
            name: value for name, value in kwargs.items() if name != "model_effort"
        }))

    monkeypatch.setattr(registry_module, "resolve_runtime_model", resolve)
    monkeypatch.setattr(GenerateEditImageTool, "_init_client", lambda self: SimpleNamespace(images=SimpleNamespace()))


@pytest.mark.parametrize("enabled", [False, True])
def test_builder_and_runner_share_image_switch_and_plan_restriction(tmp_path, offline_models, enabled):
    _write_config(tmp_path, _catalog(enabled=enabled))
    agent = build_general_agent(workspace_dir=tmp_path)
    runner = Runner.create(base_dir=tmp_path)
    try:
        assert ("generate_edit_image" in agent.tools) is enabled
        assert ("generate_edit_image" in {tool.name for tool in runner.root_config.tools}) is enabled
        assert not any(name.startswith("browser_") for name in agent.tools)
        if enabled:
            image_tool = agent.tools["generate_edit_image"]
            assert image_tool.model_name == "image-v1"
            assert image_tool.api_base == "https://image1.test/api/v3"
            assert image_tool.api_key == "image-secret"
            assert agent.model.connection.api_key == "chat-secret"
        runner.reconfigure(mode_id="plan")
        assert "generate_edit_image" not in {tool.name for tool in runner.root_config.tools}
        assert "generate_edit_image" not in runner._root().instance.tools
        runner.reconfigure(mode_id="agent")
        assert ("generate_edit_image" in runner._root().instance.tools) is enabled
        # Feature/mode overlays must not create portable root declarations.
        assert not (tmp_path / ".juice" / "agents" / "root.yaml").exists()
    finally:
        runner.close()


def test_image_tool_uses_context_overrides_and_never_chat_credentials(tmp_path, offline_models, monkeypatch):
    _write_config(tmp_path, _catalog())
    context = ConfigurationContext.from_workspace(tmp_path, overrides={
        "image_models": {"draw": {"provider_model_name": "ep-image-override"}},
    })
    registry = ToolRegistry(config_context=context)
    tool = registry.instantiate("generate_edit_image", model_config_name="judge")
    assert tool.model_name == "ep-image-override"
    assert tool.api_base == "https://image1.test/api/v3"
    assert tool.api_key == "image-secret"
    # A valid chat credential cannot silently repair missing image credentials.
    monkeypatch.delenv("TEST_IMAGE_KEY")
    with pytest.raises(ValueError, match="TEST_IMAGE_KEY"):
        registry.instantiate("generate_edit_image", model_config_name="chat")


def test_explicit_reload_refreshes_connections_and_preserves_session_and_strategy(tmp_path, offline_models, monkeypatch, caplog):
    path = _write_config(tmp_path, _catalog())
    sdk_options = []

    def create_sdk(**options):
        sdk_options.append(options)

        async def score(**payload):
            candidates = json.loads(payload["messages"][-1]["content"])["candidates"]
            content = json.dumps({"scores": list(range(len(candidates)))})
            if payload.get("stream"):
                async def chunks():
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))], usage=None)

                return chunks()
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)

        async def close():
            pass

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=score)), close=close)

    monkeypatch.setattr(openai, "AsyncOpenAI", create_sdk)
    runner = Runner.create(base_dir=tmp_path, permission_mode="accept")
    try:
        assert runner.run("remember this turn") == "chat-v1"
        old_root = runner._root().instance
        history_ids = set(old_root.context.nodes)
        tree_id = old_root.context.tree_id
        scorer = GenerativeRewardModel.from_config("judge", runner.config_context)
        from juice_agents.core.runner.execution.async_bridge import run_async
        run_async(scorer.evaluate_batch(context=[], actions=[{}]), owner=scorer)
        first_client = scorer._client
        strategy = TreeOfThoughtsStrategy(breadth=3, reward_model=scorer)
        old_root.set_exploration_strategy(strategy)
        runner_id = runner.runner_id

        _write_config(tmp_path, _catalog(2))
        canonical = path.read_text()
        runner.reconfigure()
        assert runner._root().instance is old_root
        # Gateways can hold the same boundary while writing a config file;
        # reconfigure nests safely under the reentrant request gate.
        with runner.configuration_boundary():
            runner.reconfigure(reload_config=True)
        new_root = runner._root().instance
        assert new_root is not old_root
        assert runner.runner_id == runner_id
        assert new_root.context.tree_id == tree_id
        assert history_ids <= set(new_root.context.nodes)
        assert new_root.exploration_strategy is strategy
        assert strategy.breadth == 3 and strategy.reward_model is scorer
        assert scorer.model_name == "judge" and scorer._client is None
        from juice_agents.core.runner.execution.async_bridge import run_async
        run_async(scorer.evaluate_batch(context=[], actions=[{}]), owner=scorer)
        assert scorer._client is not first_client
        assert sdk_options[-1]["base_url"] == "https://chat2.test/v1"
        assert scorer._provider_model_name == "judge-v2"
        assert new_root.model.connection.api_base == "https://chat2.test/v1"
        image = new_root.tools["generate_edit_image"]
        assert image.api_base == "https://image2.test/api/v3"
        assert image.model_name == "image-v2"
        assert runner.run("continue") == "chat-v2"
        assert path.read_text() == canonical
        assert "chat-secret" not in caplog.text and "image-secret" not in caplog.text
    finally:
        runner.close()


def test_configuration_boundary_rejects_busy_runner_before_external_write(tmp_path, offline_models):
    path = _write_config(tmp_path, _catalog(enabled=False))
    runner = Runner.create(base_dir=tmp_path)
    try:
        original = path.read_text()
        stream = runner.stream("reserved request")
        try:
            with pytest.raises(RunnerBusyError):
                with runner.configuration_boundary():
                    path.write_text("must never be committed")
            assert path.read_text() == original
        finally:
            list(stream)
            stream.close()
    finally:
        runner.close()
