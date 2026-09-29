import tempfile
import unittest

from juice_agents.core.agent import ReActAgent
from juice_agents.core.memory.config import MemoryConfig
from juice_agents.core.memory.prompt import build_memory_prompt_context
from juice_agents.core.memory.store import MemoryStore


class MemoryPromptTests(unittest.TestCase):
    def test_memory_enabled_injects_workspace_memory_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore.for_workspace(tmpdir)
            store.write_topic(title="Project conventions", content="- Use conda activate agents.")
            agent = ReActAgent(tools=[], model=_NoopModel(), name="root")
            agent.memory_context = build_memory_prompt_context(
                workspace_dir=tmpdir,
                config=MemoryConfig(enabled=True),
            )
            prompt = agent.init_system_prompt()

        self.assertIn("# Workspace Memory", prompt)
        self.assertIn("MEMORY.md", prompt)
        self.assertIn("topics/project-conventions.md", prompt)

    def test_memory_disabled_does_not_inject_workspace_memory_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = ReActAgent(tools=[], model=_NoopModel(), name="root")
            agent.memory_context = build_memory_prompt_context(
                workspace_dir=tmpdir,
                config=MemoryConfig(enabled=False),
            )
            prompt = agent.init_system_prompt()

        self.assertNotIn("# Workspace Memory", prompt)


class _NoopModel:
    def generate(self, messages, stop_sequence=None):
        del messages, stop_sequence
        return {"role": "assistant", "content": ""}


if __name__ == "__main__":
    unittest.main()
