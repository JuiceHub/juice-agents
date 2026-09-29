import tempfile
import unittest
from pathlib import Path

from juice_agents.core.registry.tools.store import ToolConfigStore
from juice_agents.core.registry.tools.types import ToolConfig, ToolRef
from juice_agents.core.registry.tools import normalize_tool_ref


class ToolConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp_dir.name)
        self.config_dir = self.base / "registry" / "tools" / "configs"
        self.config_store = ToolConfigStore(self.config_dir)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_save_load_list_delete(self):
        saved = self.config_store.save(
            {
                "name": "echo_tool",
                "description": "echo input text",
                "inputs": {"text": {"type": "string", "description": "input"}},
                "outputs": {"result": {"type": "string", "description": "output"}},
                "forward": (
                    "def forward(self, text: str = '') -> dict:\n"
                    "    return {'result': text}\n"
                ),
            }
        )
        self.assertEqual(saved.name, "echo_tool")
        self.assertEqual(self.config_store.list(), ["echo_tool"])

        loaded = self.config_store.load("echo_tool")
        self.assertEqual(loaded.name, "echo_tool")

        self.config_store.delete("echo_tool")
        self.assertEqual(self.config_store.list(), [])

    def test_tool_ref_normalization(self):
        self.assertEqual(
            normalize_tool_ref({"name": "shell"}),
            {"name": "shell", "params": {}},
        )
        self.assertEqual(
            normalize_tool_ref(ToolRef(name="python")),
            {"name": "python", "params": {}},
        )

    def test_save_rejects_config_name_that_disagrees_with_payload_name(self):
        """文件名与配置内 name 不一致时拒绝保存，避免按一个名称加载出另一个工具。"""
        with self.assertRaisesRegex(ValueError, "name 必须与 config_name 一致"):
            self.config_store.save(
                {
                    "name": "payload_tool",
                    "forward": "def forward(self):\n    return {'ok': True}\n",
                },
                name="config_tool",
            )

        self.assertEqual(self.config_store.list(), [])

    def test_context_free_default_store_is_removed(self):
        self.assertFalse(hasattr(ToolConfigStore, "default"))

    def test_tool_config_observation_limit_defaults_round_trips_and_validates(self):
        base = {
            "name": "bounded_tool",
            "forward": "def forward(self):\n    return 'ok'\n",
        }

        default_config = ToolConfig.from_dict(base)
        self.assertEqual(default_config.max_observation_chars, 12_000)

        explicit = ToolConfig.from_dict({**base, "max_observation_chars": 777})
        self.assertEqual(explicit.to_dict()["max_observation_chars"], 777)
        self.assertEqual(
            ToolConfig.from_dict(explicit.to_dict()).max_observation_chars,
            777,
        )

        for invalid in (0, -1, 1.5, True, "100"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "max_observation_chars"):
                    ToolConfig.from_dict({**base, "max_observation_chars": invalid})


if __name__ == "__main__":
    unittest.main()
