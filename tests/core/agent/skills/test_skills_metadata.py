import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# tests/core/agent/skills/test_skills_metadata.py -> 向上 4 层到仓库根目录
ROOT_DIR = Path(__file__).resolve().parents[4]
ASSETS_DIR = ROOT_DIR / "sdk" / "src" / "juice_agents" / "_assets"
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


def _run_script(script_name: str, args: list[str] | None = None) -> dict:
    script_path = ASSETS_DIR / "skills" / "joke-expert" / "scripts" / script_name
    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(result.stdout.strip())


class SkillMetadataTests(unittest.TestCase):
    def setUp(self):
        self._skills_tmp = tempfile.TemporaryDirectory()
        self.skill_kwargs = {
            "skill_names": ["joke-expert"],
            "skills_config_path": ASSETS_DIR / "config.example.yaml",
            "local_skills_path": Path(self._skills_tmp.name) / ".juice" / "skills",
        }

    def tearDown(self):
        self._skills_tmp.cleanup()

    def test_builtin_skills_directory_only_keeps_joke_expert(self):
        skills_dir = ASSETS_DIR / "skills"
        builtin_skill_dirs = sorted(
            path.name for path in skills_dir.iterdir() if path.is_dir() and not path.name.startswith("__")
        )
        self.assertEqual(builtin_skill_dirs, ["joke-expert"])

    def test_skill_metadata_loaded(self):
        agent = ReActAgent(
            tools=[_NoOpTool()],
            model=DummyModel(),
            **self.skill_kwargs,
        )
        self.assertTrue(agent.skills_metadata)
        self.assertIn("joke-expert", agent.system_prompt)
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


class JokeSkillScriptTests(unittest.TestCase):
    def test_list_categories(self):
        data = _run_script("list_joke_categories.py")
        self.assertEqual(data["categories"], ["dad", "tech"])
        self.assertEqual(data["count"], 2)

    def test_get_random_joke_with_category(self):
        data = _run_script("get_random_joke.py", ["--category", "tech"])
        joke_text = data["joke"]["text"]
        self.assertIn("程序员", joke_text)

    def test_get_random_joke_any(self):
        data = _run_script("get_random_joke.py")
        joke_text = data["joke"]["text"]
        self.assertTrue(any(keyword in joke_text for keyword in ["程序员", "腰带"]))


if __name__ == "__main__":
    unittest.main()
