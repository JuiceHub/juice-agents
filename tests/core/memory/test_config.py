import tempfile
import unittest
from pathlib import Path

from juice_agents.core.memory.config import (
    get_memory_config,
    set_workspace_memory_feature,
    workspace_config_path,
)


class MemoryConfigTests(unittest.TestCase):
    def test_memory_config_defaults_to_enabled_memory_and_dream(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("models: {}\ntools: {}\n", encoding="utf-8")

            config = get_memory_config(runtime_config_path=config_path)

        self.assertTrue(config.enabled)
        self.assertFalse(config.auto_extract_enabled)
        self.assertTrue(config.dream.enabled)
        self.assertEqual(config.dream.min_hours, 24)
        self.assertEqual(config.dream.min_sessions, 5)

    def test_memory_config_can_disable_memory_and_dream_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "memory:",
                        "  enabled: false",
                        "  auto_extract_enabled: true",
                        "  dream:",
                        "    enabled: false",
                        "    min_hours: 6",
                        "    min_sessions: 2",
                    ]
                ),
                encoding="utf-8",
            )

            config = get_memory_config(runtime_config_path=config_path)

        self.assertFalse(config.enabled)
        self.assertTrue(config.auto_extract_enabled)
        self.assertFalse(config.dream.enabled)
        self.assertEqual(config.dream.min_hours, 6)
        self.assertEqual(config.dream.min_sessions, 2)

    def test_workspace_config_yaml_overrides_project_runtime_memory_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            config_path = base / "config.yaml"
            config_path.write_text(
                "memory:\n  enabled: true\n  dream:\n    enabled: true\n",
                encoding="utf-8",
            )
            overlay_path = workspace_config_path(base)
            overlay_path.parent.mkdir(parents=True, exist_ok=True)
            overlay_path.write_text(
                "memory:\n  enabled: false\n  dream:\n    enabled: true\n",
                encoding="utf-8",
            )

            config = get_memory_config(runtime_config_path=config_path, workspace_dir=base)

        self.assertFalse(config.enabled)
        self.assertFalse(
            config.dream.enabled,
            "dream must be effectively disabled when memory is disabled",
        )

    def test_set_workspace_memory_feature_writes_workspace_yaml_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            runtime_config = base / "config.yaml"
            runtime_config.write_text(
                "memory:\n  enabled: true\n  dream:\n    enabled: true\n",
                encoding="utf-8",
            )

            updated = set_workspace_memory_feature(
                workspace_dir=base,
                runtime_config_path=runtime_config,
                feature="dream",
                enabled=False,
            )
            overlay = workspace_config_path(base)

            self.assertTrue(overlay.exists())
            self.assertEqual(overlay, base / ".juice" / "config.yaml")
            self.assertIn("dream:", overlay.read_text(encoding="utf-8"))
            self.assertFalse(updated.dream.enabled)


if __name__ == "__main__":
    unittest.main()
