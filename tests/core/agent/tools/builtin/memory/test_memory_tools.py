"""Persistent workspace-memory tool contracts."""

import tempfile
import unittest

from juice_agents.core.agent.tools.builtin.memory.memory_tools import (
    MemoryForgetTool,
    MemoryReadTool,
    MemorySearchTool,
    MemoryStatusTool,
    MemoryWriteTool,
)


class MemoryToolTests(unittest.TestCase):
    def test_memory_tools_read_write_search_and_forget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            write_tool = MemoryWriteTool(workspace_dir=tmpdir)
            read_tool = MemoryReadTool(workspace_dir=tmpdir)
            search_tool = MemorySearchTool(workspace_dir=tmpdir)
            forget_tool = MemoryForgetTool(workspace_dir=tmpdir)

            written = write_tool(
                title="Project conventions",
                content="- Keep user prompts dynamic and concise.",
                memory_type="project",
                source="explicit",
            )
            read = read_tool(path=written["path"])
            hits = search_tool(query="dynamic")
            removed = forget_tool(path_or_title="Project conventions", reason="unit test")

        self.assertTrue(written["updated"])
        self.assertIn("Keep user prompts dynamic", read["content"])
        self.assertEqual(hits["hits"][0]["path"], "topics/project-conventions.md")
        self.assertTrue(removed["removed"])

    def test_memory_read_tool_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            read_tool = MemoryReadTool(workspace_dir=tmpdir)

            with self.assertRaisesRegex(ValueError, "outside memory root"):
                read_tool(path="../secret.txt")

    def test_memory_status_reports_disabled_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_tool = MemoryStatusTool(workspace_dir=tmpdir, enabled=False, dream_enabled=True)

            status = status_tool()

        self.assertFalse(status["enabled"])
        self.assertTrue(status["dream_enabled"])


if __name__ == "__main__":
    unittest.main()
