"""Public Agent tool facade contracts."""

import importlib
import unittest


class ToolFacadeTests(unittest.TestCase):
    """The facade must hide implementation layout while preserving public exports."""

    def test_facade_exports_runtime_and_builtin_tools(self):
        facade = importlib.import_module("juice_agents.core.agent.tools")
        runtime = importlib.import_module("juice_agents.core.agent.tools.runtime.base_tools")
        filesystem = importlib.import_module("juice_agents.core.agent.tools.builtin.filesystem.files_tools")

        self.assertIs(facade.Tool, runtime.Tool)
        self.assertIs(facade.ReadTool, filesystem.ReadTool)
        self.assertIn("Tool", facade.__all__)
        self.assertIn("ReadTool", facade.__all__)

    def test_agent_tool_remains_a_lazy_facade_export(self):
        facade = importlib.reload(importlib.import_module("juice_agents.core.agent.tools"))
        subagents = importlib.import_module(
            "juice_agents.core.agent.tools.builtin.agents.subagents_tools"
        )

        self.assertNotIn("AgentTool", facade.__dict__)
        self.assertIs(facade.AgentTool, subagents.AgentTool)
        self.assertIn("AgentTool", facade.__dict__)


if __name__ == "__main__":
    unittest.main()
