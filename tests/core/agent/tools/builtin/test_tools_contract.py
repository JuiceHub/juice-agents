"""Cross-domain contracts shared by built-in tools."""

import unittest

from juice_agents.core.agent.tools.builtin.code_execution.code_tools import ShellTool
from juice_agents.core.agent.tools.runtime.base_tools import Tool
from juice_agents.core.agent.tools.builtin.web.browser.inspection_tools import BrowserGetTextTool
from juice_agents.core.agent.tools.builtin.mcp.mcp_tools import MCPRemoteTool
from juice_agents.core.agent.tools.builtin.web.web_search_tools import WebSearchTool
from juice_agents.core.agent.tools.builtin.user_interaction.ask_tools import AskTool
from juice_agents.core.agent.tools.builtin.graphs.graphs_tools import GraphTool
from juice_agents.core.agent.tools.builtin.agents.subagents_tools import AgentTool
from juice_agents.core.agent.tools.builtin.filesystem.files_tools import GlobTool, ReadTool


class _DummyTool(Tool):
    name = "dummy"
    description = "test"
    inputs = {"value": {"type": "number"}}
    outputs = {"value": {"type": "number"}}

    def __init__(self):
        super().__init__()
        self.called = False

    def forward(self, value):
        self.called = True
        return value + 1


class ToolTests(unittest.TestCase):
    def test_tool_is_abstract(self):
        with self.assertRaises(TypeError):
            Tool()

    def test_tool_call_delegates_forward(self):
        tool = _DummyTool()
        result = tool(1)
        self.assertTrue(tool.called)
        self.assertEqual(result, 2)

    def test_tool_to_react_prompt_uses_name_and_args_shape(self):
        tool = _DummyTool()
        prompt = tool.to_react_prompt()
        self.assertIn("- action: dummy", prompt)
        self.assertIn('"name": "dummy"', prompt)
        self.assertIn('"args"', prompt)
        self.assertNotIn("def dummy(", prompt)

    def test_tool_to_code_prompt_keeps_function_style(self):
        tool = _DummyTool()
        prompt = tool.to_code_prompt()
        self.assertIn("def dummy(value: number)", prompt)
        self.assertIn("Returns:", prompt)

    def test_builtin_tools_declare_balanced_observation_limits(self):
        self.assertEqual(_DummyTool.max_observation_chars, 4_000)
        self.assertEqual(GlobTool.max_observation_chars, 8_000)
        self.assertEqual(WebSearchTool.max_observation_chars, 8_000)
        self.assertEqual(ReadTool.max_observation_chars, 12_000)
        self.assertEqual(ShellTool.max_observation_chars, 12_000)
        self.assertEqual(BrowserGetTextTool.max_observation_chars, 12_000)
        self.assertEqual(MCPRemoteTool.max_observation_chars, 12_000)
        self.assertEqual(AgentTool.max_observation_chars, 16_000)
        self.assertEqual(GraphTool.max_observation_chars, 16_000)
        self.assertEqual(AskTool.max_observation_chars, 32_000)

    def test_invocation_observation_limit_is_scoped_and_restored(self):
        tool = _DummyTool()

        self.assertEqual(tool.current_max_observation_chars, 4_000)
        with tool.observation_limit(200):
            self.assertEqual(tool.current_max_observation_chars, 200)
            with tool.observation_limit(100):
                self.assertEqual(tool.current_max_observation_chars, 100)
            self.assertEqual(tool.current_max_observation_chars, 200)
        self.assertEqual(tool.current_max_observation_chars, 4_000)

    def test_tool_rejects_invalid_declared_observation_limit(self):
        class InvalidLimitTool(_DummyTool):
            max_observation_chars = 0

        with self.assertRaisesRegex(ValueError, "max_observation_chars"):
            InvalidLimitTool()


if __name__ == "__main__":
    unittest.main()
