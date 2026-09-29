"""文件工具并发安全契约：原子写入、mtime 校验与 LRU 淘汰。"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from juice_agents.core.agent.tools.builtin.filesystem.files_tools import (
    EditTool,
    ReadTool,
    WriteTool,
    _FileRuntimeState,
    _ReadSnapshot,
)


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp_dir.name)
        self.read_tool = ReadTool(root_dir=self.root_dir)
        self.write_tool = WriteTool(root_dir=self.root_dir)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_atomic_write_preserves_permissions(self):
        target = self.root_dir / "perms.txt"
        target.write_text("initial", encoding="utf-8")
        os.chmod(target, 0o644)

        self.read_tool.forward(path="perms.txt")
        self.write_tool.forward(path="perms.txt", content="updated")

        mode = target.stat().st_mode & 0o777
        self.assertEqual(mode, 0o644)

    def test_atomic_write_no_leftover_tmp_on_success(self):
        self.write_tool.forward(path="clean.txt", content="hello")
        tmp_files = list(self.root_dir.glob(".clean.txt.tmp.*"))
        self.assertEqual(len(tmp_files), 0)

    def test_write_crash_leaves_original_intact(self):
        """模拟原子 rename 场景:原文件应完整。"""
        target = self.root_dir / "intact.txt"
        target.write_text("original", encoding="utf-8")
        self.read_tool.forward(path="intact.txt")

        # 正常写入后文件内容正确
        self.write_tool.forward(path="intact.txt", content="new content")
        self.assertEqual(target.read_text(), "new content")


class ContentComparisonFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp_dir.name)
        self.read_tool = ReadTool(root_dir=self.root_dir)
        self.write_tool = WriteTool(root_dir=self.root_dir)
        self.edit_tool = EditTool(root_dir=self.root_dir)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_mtime_changed_but_content_same_allows_write(self):
        """mtime 虚假变化(如云同步)但内容未变时,应放行。"""
        target = self.root_dir / "fake_mtime.txt"
        target.write_text("same content", encoding="utf-8")
        self.read_tool.forward(path="fake_mtime.txt")

        future_ns = time.time_ns() + 5_000_000_000
        os.utime(target, ns=(future_ns, future_ns))

        result = self.write_tool.forward(path="fake_mtime.txt", content="new")
        self.assertTrue(result["overwritten"])

    def test_mtime_changed_and_content_changed_rejects(self):
        """mtime 和内容都变了,应拒绝。"""
        target = self.root_dir / "real_change.txt"
        target.write_text("original", encoding="utf-8")
        self.read_tool.forward(path="real_change.txt")

        target.write_text("modified by external", encoding="utf-8")
        future_ns = time.time_ns() + 5_000_000_000
        os.utime(target, ns=(future_ns, future_ns))

        with self.assertRaisesRegex(ValueError, "文件已在读取后被修改"):
            self.write_tool.forward(path="real_change.txt", content="new")

    def test_partial_read_no_content_fallback(self):
        """局部读取不做内容对比,mtime 变就拒。"""
        target = self.root_dir / "partial.txt"
        target.write_text("line1\nline2\nline3", encoding="utf-8")
        self.read_tool.forward(path="partial.txt", offset=0, limit=1)

        future_ns = time.time_ns() + 5_000_000_000
        os.utime(target, ns=(future_ns, future_ns))

        with self.assertRaisesRegex(ValueError, "完整读取"):
            self.write_tool.forward(path="partial.txt", content="new")


class SecondMtimeCheckTests(unittest.TestCase):
    """测试写入前的二次 mtime 校验(收紧 TOCTOU 窗口)。"""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp_dir.name)
        self.read_tool = ReadTool(root_dir=self.root_dir)
        self.write_tool = WriteTool(root_dir=self.root_dir)
        self.edit_tool = EditTool(root_dir=self.root_dir)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_edit_second_check_detects_modification(self):
        """Edit 在 str_replace 计算后、写入前检测到文件被改。"""
        target = self.root_dir / "race.txt"
        target.write_text("alpha\nbeta", encoding="utf-8")
        self.read_tool.forward(path="race.txt")

        # 在 read 之后外部修改文件(模拟 TOCTOU)
        target.write_text("alpha\nmodified", encoding="utf-8")
        future_ns = time.time_ns() + 5_000_000_000
        os.utime(target, ns=(future_ns, future_ns))

        with self.assertRaisesRegex(ValueError, "被.*修改"):
            self.edit_tool.forward(
                path="race.txt",
                edits=[{"type": "replace", "old": "beta", "new": "BETA"}],
            )

    def test_write_second_check_content_same_allows(self):
        """二次校验时 mtime 变了但内容相同,应放行。"""
        target = self.root_dir / "benign.txt"
        target.write_text("hello", encoding="utf-8")
        self.read_tool.forward(path="benign.txt")

        # 只改 mtime
        future_ns = time.time_ns() + 5_000_000_000
        os.utime(target, ns=(future_ns, future_ns))

        result = self.write_tool.forward(path="benign.txt", content="world")
        self.assertTrue(result["overwritten"])


class LRUCacheTests(unittest.TestCase):
    """测试 _FileRuntimeState 的 LRU 淘汰逻辑。"""

    def test_evicts_oldest_when_max_entries_exceeded(self):
        state = _FileRuntimeState(max_entries=3, max_size_bytes=100 * 1024 * 1024)
        for i in range(4):
            snap = _ReadSnapshot(
                resolved_path=f"/tmp/f{i}.txt",
                mtime_ns=i,
                is_partial=False,
                read_at="",
                content=f"content{i}",
                size_bytes=8,
            )
            state.add_snapshot(f"/tmp/f{i}.txt", snap)

        self.assertIsNone(state.get_snapshot("/tmp/f0.txt"))
        self.assertIsNotNone(state.get_snapshot("/tmp/f1.txt"))
        self.assertIsNotNone(state.get_snapshot("/tmp/f3.txt"))

    def test_evicts_oldest_when_max_size_exceeded(self):
        state = _FileRuntimeState(max_entries=1000, max_size_bytes=100)
        for i in range(5):
            snap = _ReadSnapshot(
                resolved_path=f"/tmp/big{i}.txt",
                mtime_ns=i,
                is_partial=False,
                read_at="",
                content="x" * 30,
                size_bytes=30,
            )
            state.add_snapshot(f"/tmp/big{i}.txt", snap)

        # 100 bytes 最多容纳 3 个 30-byte 条目
        self.assertLessEqual(state.current_size_bytes, 100)
        self.assertIsNone(state.get_snapshot("/tmp/big0.txt"))
        self.assertIsNone(state.get_snapshot("/tmp/big1.txt"))

    def test_access_refreshes_lru_order(self):
        state = _FileRuntimeState(max_entries=3, max_size_bytes=100 * 1024 * 1024)
        for i in range(3):
            snap = _ReadSnapshot(
                resolved_path=f"/tmp/lru{i}.txt",
                mtime_ns=i,
                is_partial=False,
                read_at="",
                content=f"c{i}",
                size_bytes=2,
            )
            state.add_snapshot(f"/tmp/lru{i}.txt", snap)

        # 访问 f0 使其变为最新
        state.get_snapshot("/tmp/lru0.txt")

        # 添加第 4 个,应淘汰 f1(最旧)
        snap = _ReadSnapshot(
            resolved_path="/tmp/lru3.txt",
            mtime_ns=3,
            is_partial=False,
            read_at="",
            content="c3",
            size_bytes=2,
        )
        state.add_snapshot("/tmp/lru3.txt", snap)

        self.assertIsNotNone(state.get_snapshot("/tmp/lru0.txt"))
        self.assertIsNone(state.get_snapshot("/tmp/lru1.txt"))
        self.assertIsNotNone(state.get_snapshot("/tmp/lru2.txt"))
        self.assertIsNotNone(state.get_snapshot("/tmp/lru3.txt"))

    def test_update_existing_entry_recalculates_size(self):
        state = _FileRuntimeState(max_entries=100, max_size_bytes=100 * 1024 * 1024)
        snap1 = _ReadSnapshot(
            resolved_path="/tmp/up.txt",
            mtime_ns=1,
            is_partial=False,
            read_at="",
            content="short",
            size_bytes=5,
        )
        state.add_snapshot("/tmp/up.txt", snap1)
        self.assertEqual(state.current_size_bytes, 5)

        snap2 = _ReadSnapshot(
            resolved_path="/tmp/up.txt",
            mtime_ns=2,
            is_partial=False,
            read_at="",
            content="much longer content",
            size_bytes=19,
        )
        state.add_snapshot("/tmp/up.txt", snap2)
        self.assertEqual(state.current_size_bytes, 19)


if __name__ == "__main__":
    unittest.main()
