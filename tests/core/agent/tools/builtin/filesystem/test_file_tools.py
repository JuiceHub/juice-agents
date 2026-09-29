"""Workspace filesystem tool contracts."""

import os
import tempfile
import time
import unittest
from pathlib import Path

from juice_agents.core.agent.tools.builtin.filesystem.files_tools import EditTool, GlobTool, GrepTool, ReadTool, WriteTool


class FileToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp_dir.name)
        self.read_tool = ReadTool(root_dir=self.root_dir)
        self.write_tool = WriteTool(root_dir=self.root_dir)
        self.edit_tool = EditTool(root_dir=self.root_dir)
        self.glob_tool = GlobTool(root_dir=self.root_dir)
        self.grep_tool = GrepTool(root_dir=self.root_dir)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_write_new_file_and_read_full_content(self):
        result = self.write_tool.forward(path="docs/a.txt", content="hello")
        self.assertTrue(result["created"])
        self.assertFalse(result["overwritten"])

        read_back = self.read_tool.forward(path="docs/a.txt")
        self.assertEqual(read_back["content"], "hello")
        self.assertEqual(read_back["start_line"], 1)
        self.assertEqual(read_back["end_line"], 1)
        self.assertFalse(read_back["is_partial"])

    def test_read_supports_offset_limit_only(self):
        self.write_tool.forward(path="range.txt", content="line1\nline2\nline3\nline4")

        part = self.read_tool.forward(path="range.txt", offset=1, limit=2)
        self.assertEqual(part["content"], "line2\nline3")
        self.assertEqual(part["start_line"], 2)
        self.assertEqual(part["end_line"], 3)
        self.assertTrue(part["is_partial"])

        with self.assertRaises(TypeError):
            self.read_tool.forward(path="range.txt", start_line=2, end_line=3)

    def test_read_defaults_to_200_line_pages_and_full_coverage_unlocks_write(self):
        content = "\n".join(f"line-{index}" for index in range(450))
        self.write_tool.forward(path="paged.txt", content=content)

        first = self.read_tool.forward(path="paged.txt")
        self.assertEqual(first["start_line"], 1)
        self.assertEqual(first["end_line"], 200)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 200)

        second = self.read_tool.forward(path="paged.txt", offset=first["next_offset"])
        self.assertEqual(second["start_line"], 201)
        self.assertEqual(second["end_line"], 400)
        self.assertTrue(second["has_more"])
        self.assertEqual(second["next_offset"], 400)

        third = self.read_tool.forward(path="paged.txt", offset=second["next_offset"])
        self.assertEqual(third["start_line"], 401)
        self.assertEqual(third["end_line"], 450)
        self.assertFalse(third["has_more"])
        self.assertIsNone(third["next_offset"])

        result = self.write_tool.forward(path="paged.txt", content="fully read")
        self.assertTrue(result["overwritten"])

    def test_read_rejects_binary_or_non_utf8_text(self):
        target = self.root_dir / "bin.dat"
        target.write_bytes(b"\xff\xfe\x00\x01")
        with self.assertRaises(ValueError):
            self.read_tool.forward(path="bin.dat")

    def test_write_existing_file_requires_full_read(self):
        self.write_tool.forward(path="same.txt", content="v1\nv1b")

        with self.assertRaisesRegex(ValueError, "先使用 read"):
            self.write_tool.forward(path="same.txt", content="v2")

        self.read_tool.forward(path="same.txt", offset=0, limit=1)
        with self.assertRaisesRegex(ValueError, "完整读取"):
            self.write_tool.forward(path="same.txt", content="v2")

        # 连续分页覆盖全部行后，合并后的 read-state 才允许修改。
        page = self.read_tool.forward(path="same.txt", offset=1, limit=1)
        self.assertFalse(page["has_more"])
        result = self.write_tool.forward(path="same.txt", content="v2")
        self.assertFalse(result["created"])
        self.assertTrue(result["overwritten"])
        self.assertEqual(self.read_tool.forward(path="same.txt")["content"], "v2")

    def test_write_detects_file_modified_after_read(self):
        self.write_tool.forward(path="mtime.txt", content="old")
        self.read_tool.forward(path="mtime.txt")

        target = self.root_dir / "mtime.txt"
        target.write_text("changed outside", encoding="utf-8")
        now_ns = time.time_ns() + 5_000_000_000
        os.utime(target, ns=(now_ns, now_ns))

        with self.assertRaisesRegex(ValueError, "文件已在读取后被修改"):
            self.write_tool.forward(path="mtime.txt", content="new")

    def test_edit_requires_read_and_replaces_exactly_one_match(self):
        self.write_tool.forward(path="edit.txt", content="alpha\nbeta\ngamma")

        with self.assertRaisesRegex(ValueError, "先使用 read"):
            self.edit_tool.forward(
                path="edit.txt",
                edits=[{"type": "replace", "old": "beta", "new": "BETA"}],
            )

        self.read_tool.forward(path="edit.txt")
        result = self.edit_tool.forward(
            path="edit.txt",
            edits=[{"type": "replace", "old": "beta", "new": "BETA"}],
        )
        self.assertEqual(result["edit_count"], 1)
        self.assertEqual(
            self.read_tool.forward(path="edit.txt")["content"],
            "alpha\nBETA\ngamma",
        )

    def test_edit_replace_all_updates_every_match(self):
        self.write_tool.forward(path="dup.txt", content="same\nsame\nsame")
        self.read_tool.forward(path="dup.txt")

        result = self.edit_tool.forward(
            path="dup.txt",
            edits=[
                {
                    "type": "replace",
                    "old": "same",
                    "new": "changed",
                    "replace_all": True,
                }
            ],
        )

        self.assertEqual(result["edit_count"], 3)
        self.assertEqual(
            self.read_tool.forward(path="dup.txt")["content"],
            "changed\nchanged\nchanged",
        )

    def test_edit_insert_before_with_unique_anchor(self):
        self.write_tool.forward(path="insert.txt", content="alpha\nbeta\ngamma")
        self.read_tool.forward(path="insert.txt")

        result = self.edit_tool.forward(
            path="insert.txt",
            edits=[{"type": "insert_before", "anchor": "beta", "content": "before\n"}],
        )

        self.assertEqual(result["edit_count"], 1)
        self.assertEqual(
            self.read_tool.forward(path="insert.txt")["content"],
            "alpha\nbefore\nbeta\ngamma",
        )

    def test_edit_insert_after_with_unique_anchor(self):
        self.write_tool.forward(path="insert.txt", content="alpha\nbeta\ngamma")
        self.read_tool.forward(path="insert.txt")

        result = self.edit_tool.forward(
            path="insert.txt",
            edits=[{"type": "insert_after", "anchor": "beta", "content": "\nafter"}],
        )

        self.assertEqual(result["edit_count"], 1)
        self.assertEqual(
            self.read_tool.forward(path="insert.txt")["content"],
            "alpha\nbeta\nafter\ngamma",
        )

    def test_edit_applies_multiple_edits_in_order(self):
        self.write_tool.forward(path="multi.txt", content="alpha\nbeta\ngamma")
        self.read_tool.forward(path="multi.txt")

        result = self.edit_tool.forward(
            path="multi.txt",
            edits=[
                {"type": "replace", "old": "beta", "new": "BETA"},
                {"type": "insert_after", "anchor": "BETA", "content": "\ndelta"},
            ],
        )

        self.assertEqual(result["edit_count"], 2)
        self.assertEqual(
            self.read_tool.forward(path="multi.txt")["content"],
            "alpha\nBETA\ndelta\ngamma",
        )

    def test_edit_rejects_zero_or_multiple_match_without_replace_all(self):
        self.write_tool.forward(path="dup.txt", content="same\nsame")
        self.read_tool.forward(path="dup.txt")

        with self.assertRaisesRegex(ValueError, "恰好命中 1 次"):
            self.edit_tool.forward(
                path="dup.txt",
                edits=[{"type": "replace", "old": "same", "new": "changed"}],
            )

        with self.assertRaisesRegex(ValueError, "未找到要替换的内容"):
            self.edit_tool.forward(
                path="dup.txt",
                edits=[{"type": "replace", "old": "missing", "new": "changed"}],
            )

    def test_edit_rejects_non_unique_or_missing_anchor(self):
        self.write_tool.forward(path="anchor.txt", content="same\nsame")
        self.read_tool.forward(path="anchor.txt")

        with self.assertRaisesRegex(ValueError, "锚点必须恰好命中 1 次"):
            self.edit_tool.forward(
                path="anchor.txt",
                edits=[{"type": "insert_before", "anchor": "same", "content": "before\n"}],
            )

        with self.assertRaisesRegex(ValueError, "未找到锚点"):
            self.edit_tool.forward(
                path="anchor.txt",
                edits=[{"type": "insert_after", "anchor": "missing", "content": "\nafter"}],
            )

    def test_edit_rejects_invalid_edit_specs(self):
        self.write_tool.forward(path="invalid.txt", content="alpha\nbeta")
        self.read_tool.forward(path="invalid.txt")

        with self.assertRaisesRegex(ValueError, "edits 必须是非空数组"):
            self.edit_tool.forward(path="invalid.txt", edits=[])

        with self.assertRaisesRegex(ValueError, "edits 必须是非空数组"):
            self.edit_tool.forward(path="invalid.txt", edits="bad")

        with self.assertRaisesRegex(ValueError, "不支持的 edit.type"):
            self.edit_tool.forward(
                path="invalid.txt",
                edits=[{"type": "unsupported", "old": "alpha", "new": "ALPHA"}],
            )

        with self.assertRaisesRegex(ValueError, "replace.old 必须是非空字符串"):
            self.edit_tool.forward(
                path="invalid.txt",
                edits=[{"type": "replace", "old": "", "new": "ALPHA"}],
            )

        with self.assertRaisesRegex(ValueError, "insert_before.anchor 必须是非空字符串"):
            self.edit_tool.forward(
                path="invalid.txt",
                edits=[{"type": "insert_before", "anchor": "", "content": "before\n"}],
            )

        with self.assertRaisesRegex(ValueError, "insert_after.content 必须是非空字符串"):
            self.edit_tool.forward(
                path="invalid.txt",
                edits=[{"type": "insert_after", "anchor": "beta", "content": ""}],
            )

    def test_edit_detects_file_modified_after_read(self):
        self.write_tool.forward(path="stale.txt", content="alpha")
        self.read_tool.forward(path="stale.txt")

        target = self.root_dir / "stale.txt"
        target.write_text("outside", encoding="utf-8")
        now_ns = time.time_ns() + 5_000_000_000
        os.utime(target, ns=(now_ns, now_ns))

        with self.assertRaisesRegex(ValueError, "文件已在读取后被修改"):
            self.edit_tool.forward(
                path="stale.txt",
                edits=[{"type": "replace", "old": "alpha", "new": "ALPHA"}],
            )

    def test_glob_matches_files_under_root_dir(self):
        self.write_tool.forward(path="src/a.py", content="print('a')")
        self.write_tool.forward(path="src/nested/b.py", content="print('b')")
        self.write_tool.forward(path="notes.txt", content="skip")

        result = self.glob_tool.forward(pattern="**/*.py")
        self.assertEqual(result["num_files"], 2)
        self.assertEqual(result["filenames"], ["src/a.py", "src/nested/b.py"])
        self.assertFalse(result["truncated"])

    def test_glob_supports_search_path_and_result_limit(self):
        self.write_tool.forward(path="pkg/a.py", content="a")
        self.write_tool.forward(path="pkg/b.py", content="b")
        self.write_tool.forward(path="pkg/c.py", content="c")

        result = self.glob_tool.forward(pattern="*.py", path="pkg", limit=2)
        self.assertEqual(result["num_files"], 2)
        self.assertTrue(result["truncated"])

    def test_grep_supports_files_with_matches_content_and_count(self):
        self.write_tool.forward(path="src/a.py", content="needle = 1\nprint(needle)\n")
        self.write_tool.forward(path="src/b.py", content="print('other')\n")
        self.write_tool.forward(path="docs/readme.md", content="needle in docs\n")

        files_result = self.grep_tool.forward(pattern="needle", glob="*.py")
        self.assertEqual(files_result["mode"], "files_with_matches")
        self.assertEqual(files_result["filenames"], ["src/a.py"])

        content_result = self.grep_tool.forward(
            pattern="needle",
            path="src",
            output_mode="content",
            head_limit=1,
        )
        self.assertEqual(content_result["mode"], "content")
        self.assertIn("src/a.py:1:needle = 1", content_result["content"])
        self.assertEqual(content_result["num_matches"], 1)
        self.assertTrue(content_result["truncated"])

        count_result = self.grep_tool.forward(pattern="needle", output_mode="count")
        self.assertEqual(count_result["mode"], "count")
        self.assertEqual(count_result["counts"]["docs/readme.md"], 1)
        self.assertEqual(count_result["counts"]["src/a.py"], 2)

    def test_grep_supports_offset_pagination(self):
        self.write_tool.forward(path="logs/app.log", content="hit-1\nhit-2\nhit-3\n")

        result = self.grep_tool.forward(
            pattern="hit-",
            output_mode="content",
            offset=1,
            head_limit=1,
        )

        self.assertIn("logs/app.log:2:hit-2", result["content"])
        self.assertNotIn("hit-1", result["content"])
        self.assertTrue(result["truncated"])

    def test_prevent_path_escape_outside_root(self):
        with self.assertRaises(ValueError):
            self.write_tool.forward(path="../escape.txt", content="bad")
        with self.assertRaises(ValueError):
            self.read_tool.forward(path="../escape.txt")
        with self.assertRaises(ValueError):
            self.edit_tool.forward(
                path="../escape.txt",
                edits=[{"type": "replace", "old": "bad", "new": "good"}],
            )
        with self.assertRaises(ValueError):
            self.glob_tool.forward(pattern="*.txt", path="../")
        with self.assertRaises(ValueError):
            self.grep_tool.forward(pattern="bad", path="../")


if __name__ == "__main__":
    unittest.main()
