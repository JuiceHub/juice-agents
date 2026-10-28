import sys
import tempfile
import unittest
from pathlib import Path

# tests/core/agent/skills/test_skills_metadata.py -> 向上 4 层到仓库根目录
ROOT_DIR = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from juice_agents.core.agent import CodeActAgent, ReActAgent
from juice_agents.core.agent.tools.builtin.code_execution.code_tools import ShellTool
from juice_agents.core.agent.tools.runtime.base_tools import Tool


class DummyModel:
    def generate(self, messages, stop_sequence=None):
        return {"role": "assistant", "content": "ok"}


class _NoOpTool(Tool):
    name = "noop"
    description = "do nothing"

    def forward(self):
        return "ok"


class SkillMetadataTests(unittest.TestCase):
    def setUp(self):
        self._skills_tmp = tempfile.TemporaryDirectory()
        # 每个测试自行提供最小 workspace 技能，避免依赖 SDK 的演示资源。
        local_skills_path = Path(self._skills_tmp.name) / ".juice" / "skills"
        self.skill_file = local_skills_path / "test-skill" / "SKILL.md"
        self.skill_file.parent.mkdir(parents=True)
        self.skill_file.write_text(
            "---\nname: test-skill\ndescription: Test skill metadata loading.\n---\n"
            "# Test skill\n\nRead the full instructions before applying this skill.\n",
            encoding="utf-8",
        )
        self.skill_kwargs = {
            "skill_names": ["test-skill"],
            "local_skills_path": local_skills_path,
        }

    def tearDown(self):
        self._skills_tmp.cleanup()

    def test_skill_metadata_loaded(self):
        agent = ReActAgent(
            tools=[_NoOpTool()],
            model=DummyModel(),
            **self.skill_kwargs,
        )
        self.assertEqual(len(agent.skills_metadata), 1)
        metadata = agent.skills_metadata[0]
        self.assertEqual(metadata["name"], "test-skill")
        self.assertEqual(metadata["description"], "Test skill metadata loading.")
        self.assertEqual(metadata["source"], "local")
        self.assertEqual(Path(metadata["location"]), self.skill_file)
        self.assertIn("test-skill", agent.system_prompt)
        self.assertIn(metadata["description"], agent.system_prompt)
        self.assertIn("# Skills", agent.system_prompt)
        self.assertIn("skill_view", agent.system_prompt)

    def test_react_skills_metadata_prompt_without_shell_keeps_metadata_but_not_shell_guidance(self):
        agent = ReActAgent(
            tools=[_NoOpTool()],
            model=DummyModel(),
            **self.skill_kwargs,
        )
        self.assertIn("# Skills", agent.system_prompt)
        self.assertNotIn('shell(command="cat <skill_location>")', agent.system_prompt)
        self.assertIn("Only `skill_view` returns the complete SKILL.md", agent.system_prompt)

    def test_react_skills_metadata_prompt_with_shell_uses_json_action_example(self):
        agent = ReActAgent(
            tools=[_NoOpTool(), ShellTool()],
            model=DummyModel(),
            **self.skill_kwargs,
        )
        self.assertIn("skill_view", agent.system_prompt)
        self.assertNotIn('cat <skill_location>', agent.system_prompt)
        self.assertNotIn('shell(command="cat <skill_location>")', agent.system_prompt)

    def test_codeact_skills_metadata_prompt_without_shell_avoids_hardcoded_shell_requirement(self):
        agent = CodeActAgent(
            tools=[_NoOpTool()],
            model=DummyModel(),
            **self.skill_kwargs,
        )
        self.assertIn("# Skills", agent.system_prompt)
        self.assertNotIn("你需要使用shell工具去导入python函数和执行", agent.system_prompt)
        self.assertIn('skill_view(name="...")', agent.system_prompt)

    def test_codeact_skills_metadata_prompt_with_shell_mentions_shell_as_optional_capability(self):
        agent = CodeActAgent(
            tools=[_NoOpTool(), ShellTool()],
            model=DummyModel(),
            **self.skill_kwargs,
        )
        self.assertIn("skill_view", agent.system_prompt)
        self.assertNotIn("shell(command=\"cat <skill_location>\")", agent.system_prompt)
        self.assertNotIn("你需要使用shell工具去导入python函数和执行", agent.system_prompt)


if __name__ == "__main__":
    unittest.main()
