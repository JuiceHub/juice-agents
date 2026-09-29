"""Plugin configuration-view tool contracts."""

import json
import tempfile
import unittest
from pathlib import Path

from juice_agents.core.agent.tools import PLUGIN_TOOLS
from juice_agents.core.agent.tools.builtin.plugins.plugins_tools import PluginViewTool, PluginsListTool
from juice_agents.core.config.context import ConfigurationContext


class PluginToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        plugin = self.workspace / ".juice" / "plugins" / "review-pack"
        manifest_path = plugin / ".juice-plugin" / "plugin.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "review-pack",
                    "description": "Review skills",
                    "skills": ["skills"],
                }
            ),
            encoding="utf-8",
        )
        skill_dir = plugin / "skills" / "review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review code\n---\n\nReview code.\n",
            encoding="utf-8",
        )
        self.list_tool = PluginsListTool(workspace_dir=self.workspace)
        self.view_tool = PluginViewTool(workspace_dir=self.workspace)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_plugin_catalog_is_list_and_view_only(self) -> None:
        self.assertEqual([tool.name for tool in PLUGIN_TOOLS], ["plugins_list", "plugin_view"])
        self.assertTrue(all(tool.is_read_only for tool in PLUGIN_TOOLS))

        listed = self.list_tool()
        viewed = self.view_tool(name="review-pack")

        self.assertEqual([plugin["name"] for plugin in listed["plugins"]], ["review-pack"])
        self.assertEqual(listed["plugins"][0]["skill_names"], ["review"])
        self.assertEqual(viewed["result"]["plugin"]["name"], "review-pack")

    def test_plugin_view_reads_safe_internal_file(self) -> None:
        result = self.view_tool(name="review-pack", file_path="skills/review/SKILL.md")
        self.assertIn("Review code", result["result"]["content"])
        with self.assertRaisesRegex(ValueError, "安全相对路径"):
            self.view_tool(name="review-pack", file_path="../secret")

    def test_plugin_tools_reuse_owner_configuration_context(self) -> None:
        project_config = self.workspace / "custom-config.yaml"
        project_config.write_text(
            "plugins:\n  disabled: [review-pack]\n",
            encoding="utf-8",
        )
        owner = type(
            "Owner",
            (),
            {
                "_declared_config_context": ConfigurationContext.from_workspace(
                    self.workspace,
                    project_config_path=project_config,
                )
            },
        )()

        listed = PluginsListTool(owner_agent=owner)()

        self.assertFalse(listed["plugins"][0]["enabled"])
        with self.assertRaises(FileNotFoundError):
            PluginViewTool(owner_agent=owner)(name="review-pack")


if __name__ == "__main__":
    unittest.main()
