import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from juice_agents.core.agent import CodeActAgent, ReActAgent
from juice_agents.core.agent.prompts import (
    CODEACT_PROMPT_SECTIONS,
    DEFAULT_CODEACT_SYSTEM_PROMPT,
    DEFAULT_CODEACT_SYSTEM_PROMPT_ZH,
    DEFAULT_REACT_SYSTEM_PROMPT,
    DEFAULT_REACT_SYSTEM_PROMPT_ZH,
    REACT_PROMPT_SECTIONS,
)
from juice_agents.core.agent.prompts.preview import render_prompt_preview
from juice_agents.core.agent.prompts.sections import PromptSectionScope
from juice_agents.core.agent.tools.builtin.code_execution.code_tools import ShellTool
from juice_agents.core.registry.agents.store import AgentConfigStore
from juice_agents.core.agent.tools.builtin.agents.subagents_tools import AgentTool
from juice_agents.core.agent.tools.runtime.base_tools import Tool
from juice_agents.core.registry import AgentRegistry

ROOT_DIR = Path(__file__).resolve().parents[4]


class DummyModel:
    """Prompt-only model double kept local to the prompt contract tests."""

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply

    def generate(self, _messages, stop_sequence=None):
        del stop_sequence
        return {"role": "assistant", "content": self.reply}


def _attach_prompt_agent(agent, tmp_dir: str | Path, name: str, description: str) -> None:
    """把 prompt 测试用子 agent 写入 registry，并只暴露名称引用。"""

    config_dir = Path(tmp_dir) / "agents"
    AgentConfigStore(config_dir).save(
        {
            "name": name,
            "agent_type": "react",
            "model_config_name": "runtime.shared_model",
            "description": description,
            "tools": [],
            "lifecycle": "functional",
        }
    )
    agent._runtime_managed_agent_refs[name] = {"agent_config_dir": str(config_dir)}
    agent.system_prompt = agent.init_system_prompt()
    agent.session.system_prompt = agent.system_prompt


class _PromptBrowserTool(Tool):
    name = "browser_search"
    description = "Search the web through the browser."
    inputs = {}
    outputs = {}

    def forward(self):
        return {}


class _PromptAddImageTool(Tool):
    name = "add_image"
    description = "Record an image."
    inputs = {}
    outputs = {}

    def forward(self):
        return {}


class _PromptGenerateImageTool(Tool):
    name = "generate_edit_image"
    description = "Generate or edit an image."
    inputs = {}
    outputs = {}

    def forward(self):
        return {}


class PromptTests(unittest.TestCase):
    def setUp(self):
        self._skills_tmp = tempfile.TemporaryDirectory()
        self.skill_prompt_kwargs = {
            "skill_names": ["joke-expert"],
            "skills_config_path": (
                ROOT_DIR
                / "sdk"
                / "src"
                / "juice_agents"
                / "_assets"
                / "config.example.yaml"
            ),
            "local_skills_path": Path(self._skills_tmp.name) / ".juice" / "skills",
        }

    def tearDown(self):
        self._skills_tmp.cleanup()

    def test_react_prompt_contains_tags_and_jinja(self):
        for text in [
            "<thought>",
            "<actions>",
            "JSON array",
            "submit_output",
            "call exactly one action",
            "# ReAct Protocol",
            "ReAct loop",
            '"name": "tool_name"',
            '"args": {...}',
            "</actions>",
            "{{",
        ]:
            self.assertIn(text, DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertNotIn("action_idx", DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertIn("<attachments>", DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertNotIn('"execution": "async"', DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertIn("Do not read task files or poll after launch", DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertIn("async_task_notification.summary", DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertIn("async_task_notification.result", DEFAULT_REACT_SYSTEM_PROMPT)

    def test_react_prompt_rejects_non_json_array_action_syntax(self):
        self.assertIn("Only a JSON array is valid inside `<actions>`", DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertIn("Do not write XML tags", DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertIn("function calls", DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertIn("bare JSON objects", DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertIn('[{"name": "submit_output", "args": {"output": "..."}}]', DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertNotIn('<name="submit_output"', DEFAULT_REACT_SYSTEM_PROMPT)

    def test_codeact_prompt_contains_code_guidance(self):
        self.assertIn("<thought>", DEFAULT_CODEACT_SYSTEM_PROMPT)
        self.assertIn("<code>", DEFAULT_CODEACT_SYSTEM_PROMPT)
        self.assertIn("submit_output", DEFAULT_CODEACT_SYSTEM_PROMPT)
        self.assertIn("{{", DEFAULT_CODEACT_SYSTEM_PROMPT)
        self.assertIn("<attachments>", DEFAULT_CODEACT_SYSTEM_PROMPT)
        self.assertIn("Do not read task files or poll after launch", DEFAULT_CODEACT_SYSTEM_PROMPT)
        self.assertIn("async_task_notification.summary", DEFAULT_CODEACT_SYSTEM_PROMPT)
        self.assertIn("async_task_notification.result", DEFAULT_CODEACT_SYSTEM_PROMPT)

    def test_prompt_section_alignment(self):
        english_headings = [
            "# System",
            "# Engineering Execution",
            "# Action Safety",
            "# Tool Use",
            "# Communication and Verification",
            "# Tools",
            "# Skills",
        ]
        for heading in english_headings:
            self.assertIn(heading, DEFAULT_REACT_SYSTEM_PROMPT)
            self.assertIn(heading, DEFAULT_CODEACT_SYSTEM_PROMPT)
        self.assertIn("# ReAct Protocol", DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertIn("# ReAct Limits", DEFAULT_REACT_SYSTEM_PROMPT)
        self.assertIn("# CodeAct Protocol", DEFAULT_CODEACT_SYSTEM_PROMPT)
        self.assertIn("# CodeAct Limits", DEFAULT_CODEACT_SYSTEM_PROMPT)

    def test_zh_prompt_section_alignment(self):
        headings = [
            "# 系统",
            "# 工程执行",
            "# 行动安全",
            "# 工具使用",
            "# 沟通与核验",
            "# 工具列表",
            "# Skills",
        ]
        for heading in headings:
            self.assertIn(heading, DEFAULT_REACT_SYSTEM_PROMPT_ZH)
            self.assertIn(heading, DEFAULT_CODEACT_SYSTEM_PROMPT_ZH)
        self.assertIn("# ReAct 协议", DEFAULT_REACT_SYSTEM_PROMPT_ZH)
        self.assertIn("# ReAct 限制", DEFAULT_REACT_SYSTEM_PROMPT_ZH)
        self.assertIn("# CodeAct 协议", DEFAULT_CODEACT_SYSTEM_PROMPT_ZH)
        self.assertIn("# CodeAct 限制", DEFAULT_CODEACT_SYSTEM_PROMPT_ZH)

    def test_prompt_sections_have_unique_names_and_dynamic_boundary(self):
        for sections in (REACT_PROMPT_SECTIONS, CODEACT_PROMPT_SECTIONS):
            names = [section.name for section in sections]
            self.assertEqual(len(names), len(set(names)))
            self.assertTrue(all(section.template.strip() for section in sections))
            first_dynamic_index = next(
                index
                for index, section in enumerate(sections)
                if section.scope == PromptSectionScope.DYNAMIC
            )
            self.assertTrue(
                all(section.scope == PromptSectionScope.STATIC for section in sections[:first_dynamic_index])
            )
            self.assertTrue(
                all(section.scope == PromptSectionScope.DYNAMIC for section in sections[first_dynamic_index:])
            )

    def test_common_engineering_constraints_are_rendered_in_default_prompts(self):
        expected_constraints = [
            "Read the relevant code",
            "Avoid unrelated refactors",
            "Do not blindly repeat",
            "prompt injection",
            "hard-to-reverse actions",
            "For web search tasks",
            "hidden element, captcha, or security-verification block",
            "Use verification appropriate to the task",
            "Report outcomes faithfully",
        ]
        for text in expected_constraints:
            self.assertIn(text, DEFAULT_REACT_SYSTEM_PROMPT)
            self.assertIn(text, DEFAULT_CODEACT_SYSTEM_PROMPT)

    def test_browser_prompt_guidance_follows_browser_tool_visibility(self):
        without_browser = ReActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
        )
        with_browser = ReActAgent(
            tools=[_PromptBrowserTool()],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
        )

        for text in ("browser_search", "browser input action", "browser console"):
            self.assertNotIn(text, without_browser.system_prompt)
            self.assertIn(text, with_browser.system_prompt)
        self.assertIn("listed search API tools", without_browser.system_prompt)

    def test_image_prompt_guidance_follows_image_tool_visibility(self):
        without_image = ReActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
        )
        with_add_image = ReActAgent(
            tools=[_PromptAddImageTool()],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
        )
        with_generation = ReActAgent(
            tools=[_PromptAddImageTool(), _PromptGenerateImageTool()],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
        )

        self.assertNotIn("# Image Tools", without_image.system_prompt)
        self.assertIn("# Image Tools", with_add_image.system_prompt)
        self.assertIn("add_image", with_add_image.system_prompt)
        self.assertNotIn("generate_edit_image", with_add_image.system_prompt)
        self.assertIn("generate_edit_image", with_generation.system_prompt)

    def test_render_prompt_preview_returns_complete_rendered_prompt(self):
        prompt = render_prompt_preview(
            agent_type="react",
            mode="rendered",
            instructions="先检索再总结，答案简洁",
        )

        self.assertIn("# System", prompt)
        self.assertIn("# Engineering Execution", prompt)
        self.assertIn("# Task Instructions", prompt)
        self.assertIn("先检索再总结，答案简洁", prompt)
        self.assertIn("submit_output", prompt)
        self.assertNotIn("{{", prompt)

    def test_render_prompt_preview_defaults_to_complete_template(self):
        prompt = render_prompt_preview(agent_type="react")

        self.assertIn("{%- if memory_enabled %}", prompt)
        self.assertIn("# Workspace Memory", prompt)
        self.assertIn("{{ memory_dir }}", prompt)
        self.assertIn("{{ memory_index }}", prompt)
        self.assertIn("{{ instructions }}", prompt)

    def test_render_prompt_preview_can_show_sections(self):
        prompt = render_prompt_preview(agent_type="codeact", show_sections=True)

        self.assertIn("[static] identity", prompt)
        self.assertIn("[dynamic] tools", prompt)
        self.assertIn("[dynamic] memory", prompt)
        self.assertIn("# CodeAct Protocol", prompt)
        self.assertIn("<code>", prompt)

    def test_render_prompt_preview_supports_chinese_language(self):
        prompt = render_prompt_preview(agent_type="react", language="zh", show_sections=True)

        self.assertIn("# 系统", prompt)
        self.assertIn("# ReAct 协议", prompt)
        self.assertIn("# 工作区记忆", prompt)
        self.assertIn("[dynamic] tools", prompt)

    def test_prompt_preview_cli_outputs_rendered_prompt(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "juice_agents.core.agent.prompts.preview",
                "--agent-type",
                "react",
                "--mode",
                "rendered",
                "--instructions",
                "保持答案简洁",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("# Engineering Execution", result.stdout)
        self.assertIn("保持答案简洁", result.stdout)
        self.assertNotIn("{{", result.stdout)

    def test_instructions_injection_in_react_agent(self):
        instructions = "这是一个测试任务指令：请完成数据分析任务。"
        agent = ReActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            instructions=instructions,
        )
        self.assertIn(instructions, agent.system_prompt)
        self.assertIn("# Task Instructions", agent.system_prompt)

    def test_instructions_injection_in_codeact_agent(self):
        instructions = "这是一个测试任务指令：请完成代码编写任务。"
        agent = CodeActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            instructions=instructions,
        )
        self.assertIn(instructions, agent.system_prompt)
        self.assertIn("# Task Instructions", agent.system_prompt)

    def test_output_schema_injection_in_react_agent(self):
        output_schema = {
            "type": "object",
            "properties": {
                "result": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["result"],
        }
        agent = ReActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            output_schema=output_schema,
        )
        self.assertIn("The final `submit_output` value must conform to this schema", agent.system_prompt)
        self.assertIn('"type": "object"', agent.system_prompt)
        self.assertIn('"result"', agent.system_prompt)
        self.assertIn("validation fails", agent.system_prompt)

    def test_output_schema_injection_in_codeact_agent(self):
        output_schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "steps": {"type": "array"},
            },
            "required": ["answer"],
        }
        agent = CodeActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            output_schema=output_schema,
        )
        self.assertIn("The final `submit_output` value must conform to this schema", agent.system_prompt)
        self.assertIn('"type": "object"', agent.system_prompt)
        self.assertIn('"answer"', agent.system_prompt)
        self.assertIn("Runtime validates", agent.system_prompt)

    def test_instructions_and_output_schema_together(self):
        instructions = "请分析数据并返回结果"
        output_schema = {"type": "object", "properties": {"data": {"type": "string"}}}
        agent = ReActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            instructions=instructions,
            output_schema=output_schema,
        )
        self.assertIn(instructions, agent.system_prompt)
        self.assertIn("# Task Instructions", agent.system_prompt)
        self.assertIn("The final `submit_output` value must conform to this schema", agent.system_prompt)
        self.assertIn('"data"', agent.system_prompt)

    def test_no_instructions_or_schema_when_none(self):
        agent = ReActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            instructions=None,
            output_schema=None,
        )
        self.assertIsNotNone(agent.system_prompt)
        self.assertIn("# System", agent.system_prompt)

    def test_react_skills_prompt_without_shell_treats_skills_as_hints_only(self):
        agent = ReActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            **self.skill_prompt_kwargs,
        )
        self.assertIn("# Skills", agent.system_prompt)
        self.assertNotIn('shell(command="cat <skill_location>")', agent.system_prompt)
        self.assertIn("Only `skill_view` returns the complete SKILL.md", agent.system_prompt)

    def test_react_skills_prompt_with_shell_uses_react_action_protocol(self):
        agent = ReActAgent(
            tools=[ShellTool()],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            **self.skill_prompt_kwargs,
        )
        self.assertIn("# Skills", agent.system_prompt)
        self.assertIn("skill_view", agent.system_prompt)
        self.assertNotIn("cat <skill_location>", agent.system_prompt)
        self.assertNotIn('shell(command="cat <skill_location>")', agent.system_prompt)

    def test_react_skills_prompt_instructs_dollar_capability_loading(self):
        agent = ReActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            **self.skill_prompt_kwargs,
        )

        self.assertIn("If the user message starts with `$<known-skill>`", agent.system_prompt)
        self.assertIn("call `skill_view` before handling the remaining request", agent.system_prompt)
        self.assertIn("If the leading `$<name>` is not a known Skill", agent.system_prompt)
        self.assertIn("call `plugin_view(name=<name>)`", agent.system_prompt)

    def test_codeact_skills_prompt_without_shell_does_not_assume_shell_execution_path(self):
        agent = CodeActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            **self.skill_prompt_kwargs,
        )
        self.assertIn("# Skills", agent.system_prompt)
        self.assertNotIn("你需要使用shell工具去导入python函数和执行", agent.system_prompt)
        self.assertIn('skill_view(name="...")', agent.system_prompt)

    def test_codeact_skills_prompt_with_shell_mentions_shell_as_optional_path(self):
        agent = CodeActAgent(
            tools=[ShellTool()],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            **self.skill_prompt_kwargs,
        )
        self.assertIn("# Skills", agent.system_prompt)
        self.assertIn("skill_view", agent.system_prompt)
        self.assertNotIn("shell(command=\"cat <skill_location>\")", agent.system_prompt)
        self.assertNotIn("你需要使用shell工具去导入python函数和执行", agent.system_prompt)

    def test_codeact_skills_prompt_instructs_dollar_capability_loading(self):
        agent = CodeActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            **self.skill_prompt_kwargs,
        )

        self.assertIn("If the user message starts with `$<known-skill>`", agent.system_prompt)
        self.assertIn("call `skill_view(name=\"...\")` before handling the remaining request", agent.system_prompt)
        self.assertIn("If the leading `$<name>` is not a known Skill", agent.system_prompt)
        self.assertIn("call `plugin_view(name=<name>)`", agent.system_prompt)

    def test_skills_prompt_follows_skill_tool_enable_flag(self):
        enabled_react = ReActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            **self.skill_prompt_kwargs,
            enable_skill_tools=True,
        )
        disabled_react = ReActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            **self.skill_prompt_kwargs,
            enable_skill_tools=False,
        )
        enabled_codeact = CodeActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            **self.skill_prompt_kwargs,
            enable_skill_tools=True,
        )
        disabled_codeact = CodeActAgent(
            tools=[],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
            **self.skill_prompt_kwargs,
            enable_skill_tools=False,
        )

        for agent in (enabled_react, enabled_codeact):
            self.assertIn("skill_view", agent.tools)
            self.assertIn("# Skills", agent.system_prompt)
            self.assertIn("skill_view", agent.system_prompt)
            self.assertTrue(agent.skills_enabled)
        for agent in (disabled_react, disabled_codeact):
            self.assertNotIn("skill_view", agent.tools)
            self.assertNotIn("# Skills", agent.system_prompt)
            self.assertNotIn("skill_view", agent.system_prompt)
            self.assertFalse(agent.skills_enabled)

    def test_react_prompt_prefers_agent_tool_for_team_members(self):
        agent = ReActAgent(
            tools=[AgentTool()],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            _attach_prompt_agent(agent, tmp_dir, "research_worker", "research tasks")
        self.assertIn("agent_tool", agent.system_prompt)
        self.assertIn("Collaborate only through `agent_tool`", agent.system_prompt)
        self.assertIn('{"name": "agent_tool"', agent.system_prompt)
        self.assertIn("research_worker", agent.system_prompt)
        self.assertIn("background async task", agent.system_prompt)

    def test_managed_agent_prompts_do_not_reserve_plan_name(self):
        """A user-defined ``plan`` agent is described generically, never as a built-in role."""
        agents = [
            ReActAgent(tools=[AgentTool()], model=DummyModel(), system_prompt=None, max_steps=1),
            CodeActAgent(tools=[AgentTool()], model=DummyModel(), system_prompt=None, max_steps=1),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            for agent in agents:
                _attach_prompt_agent(agent, tmp_dir, "general", "general execution")
                _attach_prompt_agent(agent, tmp_dir, "explore", "read-only exploration")
                _attach_prompt_agent(agent, tmp_dir, "plan", "custom planning support")

        for agent in agents:
            self.assertIn("`explore`", agent.system_prompt)
            self.assertIn("read-only codebase searches", agent.system_prompt)
            self.assertIn("plan: custom planning support", agent.system_prompt)
            self.assertIn("`general`", agent.system_prompt)
            self.assertIn("isolated multi-step execution", agent.system_prompt)
            self.assertNotIn("Use `plan` for read-only implementation planning", agent.system_prompt)
            self.assertNotIn("`plan` 用于基于代码证据进行只读实施规划", agent.system_prompt)
            self.assertNotIn("内置 `explore`", agent.system_prompt)
            self.assertNotIn("内置 `general`", agent.system_prompt)

    def test_codeact_prompt_prefers_agent_tool_for_team_members(self):
        agent = CodeActAgent(
            tools=[AgentTool()],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            _attach_prompt_agent(agent, tmp_dir, "research_worker", "research tasks")
        self.assertIn("agent_tool", agent.system_prompt)
        self.assertIn("Collaborate only through `agent_tool", agent.system_prompt)
        self.assertIn('agent_tool(name="research_worker", task="', agent.system_prompt)
        self.assertIn("background async task", agent.system_prompt)

    def test_react_prompt_mentions_background_shell_instead_of_generic_async(self):
        agent = ReActAgent(
            tools=[ShellTool()],
            model=DummyModel(),
            system_prompt=None,
            max_steps=1,
        )
        self.assertIn("background=true", agent.system_prompt)
        self.assertNotIn("execution\": \"async", agent.system_prompt)


if __name__ == "__main__":
    unittest.main()
