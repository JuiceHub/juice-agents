import unittest

from juice_agents.core.agent.builtin.configs import (
    GENERAL_BROWSER_TOOL_NAMES,
    GENERAL_CONFIG,
    GENERAL_ROOT_TOOLS,
    GENERAL_SUBAGENT_CONFIG,
    GENERAL_SUBAGENT_TOOLS,
    GENERAL_TEAM_TOOLS,
    get_general_config,
)
from juice_agents.core.registry.agents.types import AgentConfig


class AgentBuiltinConfigTests(unittest.TestCase):
    def test_builtin_declarations_are_strongly_typed(self):
        self.assertIsInstance(GENERAL_CONFIG, AgentConfig)
        self.assertIsInstance(GENERAL_SUBAGENT_CONFIG, AgentConfig)
        self.assertEqual(GENERAL_CONFIG.agent_type, "default")

    def test_general_configs_keep_tool_boundaries(self):
        root_tools = {tool.name for tool in GENERAL_ROOT_TOOLS}
        subagent_tools = {tool.name for tool in GENERAL_SUBAGENT_TOOLS}
        team_tools = {tool.name for tool in GENERAL_TEAM_TOOLS}

        self.assertIn("agent_tool", root_tools)
        self.assertNotIn("agent_tool", subagent_tools)
        self.assertIn("agent_tool", team_tools)
        self.assertTrue(GENERAL_BROWSER_TOOL_NAMES.issubset(root_tools))
        self.assertTrue(GENERAL_BROWSER_TOOL_NAMES.issubset(subagent_tools))
        self.assertTrue(GENERAL_BROWSER_TOOL_NAMES.isdisjoint(team_tools))

    def test_copy_with_does_not_mutate_builtin(self):
        derived = get_general_config(max_steps=3, tools=[])

        self.assertEqual(derived.max_steps, 3)
        self.assertEqual(derived.tools, ())
        self.assertEqual(GENERAL_CONFIG.max_steps, 20)
        self.assertTrue(GENERAL_CONFIG.tools)


if __name__ == "__main__":
    unittest.main()
