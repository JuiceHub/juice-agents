"""Skill configuration tool contracts."""

import tempfile
import textwrap
import unittest
from pathlib import Path

from juice_agents.core.agent.tools.builtin.skills.skills_tools import SkillViewTool, SkillsListTool
from juice_agents.core.agent.tools.builtin.evolution.skill_manage import SkillManageTool


def skill_md(name: str, description: str) -> str:
    return textwrap.dedent(
        f"""
        ---
        name: {name}
        description: {description}
        ---

        # {name}

        Use {name}.
        """
    ).strip() + "\n"


class SkillToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp_dir.name) / "workspace"
        self.local_dir = self.workspace / ".juice" / "skills"
        self.builtin_dir = Path(self.tmp_dir.name) / "builtin"
        self.external_dir = Path(self.tmp_dir.name) / "external"
        shared = {
            "local_dir": self.local_dir,
            "builtin_dir": self.builtin_dir,
            "external_dirs": [self.external_dir],
        }
        self.list_tool = SkillsListTool(**shared)
        self.view_tool = SkillViewTool(**shared)
        self.manage_tool = SkillManageTool(**shared)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    @staticmethod
    def _write_skill(root: Path, name: str, description: str) -> Path:
        skill_dir = root / name
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(skill_md(name, description), encoding="utf-8")
        (skill_dir / "references" / "api.md").write_text("original reference", encoding="utf-8")
        return skill_dir

    def test_catalog_exposes_direct_list_view_manage_tools(self) -> None:
        created = self.manage_tool(
            action="create",
            name="local-skill",
            content=skill_md("local-skill", "local desc"),
        )["result"]

        self.assertTrue(created["success"])
        self.assertNotIn("revision", created)
        self.assertNotIn("generation_id", created)
        listed = self.list_tool()
        viewed = self.view_tool(name="local-skill")
        self.assertIn("local-skill", [item["name"] for item in listed["skills"]])
        self.assertEqual(viewed["metadata"]["source"], "local")
        self.assertIn("local desc", viewed["content"])

    def test_edit_external_skill_copies_complete_directory_before_editing(self) -> None:
        source = self._write_skill(self.external_dir, "review", "external description")
        original_markdown = (source / "SKILL.md").read_text(encoding="utf-8")

        edited = self.manage_tool(
            action="edit",
            name="review",
            content=skill_md("review", "workspace description"),
        )["result"]

        local = self.local_dir / "review"
        self.assertEqual(edited["copied_from"], str(source.resolve()))
        self.assertEqual((source / "SKILL.md").read_text(encoding="utf-8"), original_markdown)
        self.assertEqual((source / "references" / "api.md").read_text(encoding="utf-8"), "original reference")
        self.assertEqual((local / "references" / "api.md").read_text(encoding="utf-8"), "original reference")
        self.assertIn("workspace description", (local / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(self.view_tool(name="review")["metadata"]["source"], "local")

    def test_supporting_file_edit_also_uses_copy_on_write(self) -> None:
        source = self._write_skill(self.builtin_dir, "builtin-review", "builtin description")

        result = self.manage_tool(
            action="write_file",
            name="builtin-review",
            file_path="references/api.md",
            content="workspace reference",
        )["result"]

        self.assertTrue(result["success"])
        self.assertEqual((source / "references" / "api.md").read_text(encoding="utf-8"), "original reference")
        self.assertEqual(
            (self.local_dir / "builtin-review" / "references" / "api.md").read_text(encoding="utf-8"),
            "workspace reference",
        )

    def test_copy_on_write_rejects_any_source_symlink(self) -> None:
        source = self._write_skill(self.external_dir, "linked-review", "external description")
        outside = Path(self.tmp_dir.name) / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (source / "references" / "linked.txt").symlink_to(outside)

        result = self.manage_tool(
            action="edit",
            name="linked-review",
            content=skill_md("linked-review", "workspace description"),
        )["result"]

        self.assertFalse(result["success"])
        self.assertIn("符号链接", result["error"])
        self.assertFalse((self.local_dir / "linked-review").exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

    def test_unload_keeps_local_copy_and_delete_is_not_exposed(self) -> None:
        self._write_skill(self.external_dir, "review", "external description")
        self.manage_tool(
            action="edit",
            name="review",
            content=skill_md("review", "workspace description"),
        )
        unloaded = self.manage_tool(action="unload", name="review")["result"]

        self.assertTrue(unloaded["success"])
        self.assertEqual(unloaded["action"], "unload")
        self.assertEqual(unloaded["runtime_refresh"]["status"], "unsupported")
        self.assertTrue((self.local_dir / "review" / "SKILL.md").is_file())
        self.assertEqual(self.view_tool(name="review")["metadata"]["source"], "local")

        rejected = self.manage_tool(action="delete", name="review")["result"]
        self.assertFalse(rejected["success"])
        self.assertIn("create/edit/patch/write_file/load/unload", rejected["error"])

    def test_frontmatter_name_must_match_managed_directory_name(self) -> None:
        result = self.manage_tool(
            action="create",
            name="declared-name",
            content=skill_md("different-name", "mismatched"),
        )["result"]

        self.assertFalse(result["success"])
        self.assertIn("name", result["error"])
        self.assertFalse((self.local_dir / "declared-name").exists())


if __name__ == "__main__":
    unittest.main()
