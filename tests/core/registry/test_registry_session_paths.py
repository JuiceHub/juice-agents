"""
Tests for ConfigStore directory isolation.
"""

import tempfile
import unittest
from pathlib import Path

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.agents.store import AgentConfigStore
from juice_agents.core.registry.tools.store import ToolConfigStore


class ConfigStoreDirIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_tool_config_store_isolated_by_config_dir(self):
        dir_a = self.base / "a" / "tools"
        dir_b = self.base / "b" / "tools"
        store_a = ToolConfigStore(dir_a)
        store_b = ToolConfigStore(dir_b)

        store_a.save(
            {
                "name": "tool_a",
                "description": "tool a",
                "inputs": {},
                "outputs": {"result": {"type": "string"}},
                "forward": "def forward(self, text: str = '') -> dict:\n    return {'result': text}",
            }
        )
        store_b.save(
            {
                "name": "tool_b",
                "description": "tool b",
                "inputs": {},
                "outputs": {"result": {"type": "string"}},
                "forward": "def forward(self, text: str = '') -> dict:\n    return {'result': text}",
            }
        )

        self.assertEqual(store_a.list(), ["tool_a"])
        self.assertEqual(store_b.list(), ["tool_b"])

    def test_agent_config_store_isolated_by_config_dir(self):
        dir_a = self.base / "a" / "agents"
        dir_b = self.base / "b" / "agents"
        store_a = AgentConfigStore(dir_a)
        store_b = AgentConfigStore(dir_b)

        store_a.save({"name": "agent_a", "model_config_name": "Test", "tools": []})
        store_b.save({"name": "agent_b", "model_config_name": "Test", "tools": []})

        self.assertEqual(store_a.list(), ["agent_a"])
        self.assertEqual(store_b.list(), ["agent_b"])

    def test_registry_stores_use_explicit_context_paths(self):
        context = ConfigurationContext.from_workspace(self.base)
        agent_store = AgentConfigStore(context.agents_dir)
        tool_store = ToolConfigStore(context.tools_dir)

        self.assertEqual(
            agent_store.config_dir,
            self.base / ".juice" / "agents",
        )
        self.assertEqual(tool_store.config_dir, self.base / ".juice" / "tools")
        self.assertNotIn("config_store", str(agent_store.config_dir))
        self.assertNotIn("config_store", str(tool_store.config_dir))


if __name__ == "__main__":
    unittest.main()
