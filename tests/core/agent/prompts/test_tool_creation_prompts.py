"""Exercise tool-creation guidance and execute the examples shown to the model."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from juice_agents.core.agent import CodeActAgent, ReActAgent
from juice_agents.core.agent.tools.builtin.evolution.agent_manage import AgentManageTool
from juice_agents.core.agent.tools.builtin.evolution.tool_manage import ToolManageTool
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.tools.registry import ToolRegistry
from juice_agents.core.registry.tools.types import ToolConfig


class _PromptOnlyModel:
    """Render real Agent prompts while making accidental model calls fail locally."""

    def generate(self, _messages, stop_sequence=None):
        del stop_sequence
        raise AssertionError("Tool-creation prompt tests must not call a model")


@pytest.fixture(params=["react", "codeact"])
def agent_type(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(params=["en", "zh"])
def language(request: pytest.FixtureRequest) -> str:
    return request.param


def _render_prompt(agent_type: str, language: str, *, with_tool_manage: bool) -> str:
    """Use the same Agent assembly path as a run, including both manage tools.

    Keep agent_manage visible when tool_manage is hidden to verify that the
    tool-creation contract belongs only to tool_manage's own description.
    """

    agent_class = ReActAgent if agent_type == "react" else CodeActAgent
    tools = [AgentManageTool()]
    if with_tool_manage:
        tools.append(ToolManageTool())
    agent = agent_class(
        tools=tools,
        model=_PromptOnlyModel(),
        prompt_language=language,
        max_steps=1,
        enable_skill_tools=False,
        enable_workflow_tools=False,
    )
    return agent.system_prompt


def _extract_example_args(prompt: str) -> dict[str, Any]:
    """Parse the rendered example instead of copying its source-level fixture.

    Both protocols expose the same JSON argument object. Parsing it from the
    final prompt catches broken escaping and incomplete placeholder configs.
    """

    for line in prompt.splitlines():
        text = line.strip()
        if text.startswith("save_args:"):
            return json.loads(text.partition(":")[2].strip())
    pytest.fail("The final system prompt lacks a parseable tool_manage example")


def test_rendered_prompt_explains_tool_schema_and_activation(agent_type: str, language: str) -> None:
    prompt = _render_prompt(agent_type, language, with_tool_manage=True)

    tool = ToolManageTool()
    definition = tool.to_react_prompt() if agent_type == "react" else tool.to_code_prompt()
    assert definition in prompt

    # Check this tool's definition, since another manage tool can legitimately
    # have a different activation contract and is outside this change's scope.
    for term in (
        "description", "inputs", "outputs", "imports", "forward", "def forward(self",
        "init_params", "runtime", "safe/trusted", "max_observation_chars",
        "runtime_refresh", "effective_from", "scheduled", "next_step", "fresh_acquire", "unsupported",
        "临时执行 Python/Shell 代码不等于创建工具",
        "分别核验声明保存、引用登记和调用结果",
        "新工具出现在可用工具列表后才可调用验证",
        "确认 YAML 是否已写入",
        "不要重写 YAML 绕过绑定错误",
        "root 与其他 Agent 一样持久化工具引用",
    ):
        assert term in definition
    assert "{{" not in prompt
    assert "下一模型 step 生效" not in definition
    assert "effective on the next model step" not in definition


def test_tool_creation_guidance_requires_visible_tool_manage(agent_type: str, language: str) -> None:
    prompt = _render_prompt(agent_type, language, with_tool_manage=False)

    assert "agent_manage" in prompt
    assert "tool_manage" not in prompt
    assert "print_current_time" not in prompt
    assert "def forward(self" not in prompt


def test_rendered_creation_example_saves_and_executes_as_a_fresh_tool(
    agent_type: str,
    language: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt = _render_prompt(agent_type, language, with_tool_manage=True)
    args = _extract_example_args(prompt)
    assert args["action"] == "save"
    assert args["name"] == "print_current_time"
    inspect.signature(ToolManageTool.forward).bind(None, **args)

    # The outer action name is authoritative, just as in ToolManageTool. Use
    # Registry directly so this example test never impersonates a root owner
    # or confuses declaration construction with live Agent reference binding.
    config = ToolConfig.from_dict(args["config"], config_name=args["name"])
    context = ConfigurationContext.from_workspace(tmp_path)
    registry = ToolRegistry(config_context=context, config_dir=tmp_path / "tools")
    registry.save_config(config)
    assert registry.load_config(config.name) == config
    assert (tmp_path / "tools" / f"{config.name}.yaml").is_file()

    tool = registry.instantiate(config.name)
    assert registry.instantiate(config.name) is not tool
    before = datetime.now(timezone.utc)
    result = tool()
    after = datetime.now(timezone.utc)

    # Running the actual source catches missing self/imports and mismatches
    # between declared outputs and returned data, beyond prompt string checks.
    assert set(result) == set(config.outputs) == {"current_time"}
    observed = datetime.fromisoformat(result["current_time"])
    assert observed.tzinfo is not None
    assert before - timedelta(seconds=1) <= observed <= after
    assert capsys.readouterr().out.strip() == result["current_time"]
