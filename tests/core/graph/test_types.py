import unittest

from juice_agents.core.graph.types import GraphConfig


class GraphConfigTests(unittest.TestCase):
    def test_from_any_reads_workspace_dir_from_top_level(self):
        cfg = GraphConfig.from_any(
            {
                "max_steps": 5,
                "workspace_dir": "/tmp/demo-workspace",
                "custom_flag": True,
            }
        )
        self.assertEqual(cfg.max_steps, 5)
        self.assertEqual(cfg.workspace_dir, "/tmp/demo-workspace")
        self.assertTrue(cfg.configurable["custom_flag"])

    def test_from_any_keeps_workspace_dir_unset_when_missing(self):
        cfg = GraphConfig.from_any({"max_steps": 3})
        self.assertIsNone(cfg.workspace_dir)
        cfg.validate()

    def test_validate_rejects_blank_workspace_dir(self):
        cfg = GraphConfig(workspace_dir="  ")
        with self.assertRaises(ValueError):
            cfg.validate()


if __name__ == "__main__":
    unittest.main()
