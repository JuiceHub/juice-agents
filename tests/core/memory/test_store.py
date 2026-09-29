import tempfile
import unittest
import os
import time
from pathlib import Path
from types import SimpleNamespace

from juice_agents.core.memory.store import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_store_initializes_workspace_memory_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore.for_workspace(tmpdir)
            status = store.status()

            self.assertEqual(store.root, Path(tmpdir).resolve() / ".juice" / "memory")
            self.assertTrue((store.root / "MEMORY.md").exists())
            self.assertTrue((store.root / "topics").is_dir())
            self.assertEqual(status["topic_count"], 0)

    def test_write_topic_updates_frontmatter_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore.for_workspace(tmpdir)
            result = store.write_topic(
                title="Project conventions",
                content="- Use conda activate agents before running tests.",
                memory_type="project",
                source="explicit",
            )

            topic_text = (store.root / result["path"]).read_text(encoding="utf-8")
            index_text = (store.root / "MEMORY.md").read_text(encoding="utf-8")

        self.assertEqual(result["path"], "topics/project-conventions.md")
        self.assertIn("name: project-conventions", topic_text)
        self.assertIn("type: project", topic_text)
        self.assertIn("source: explicit", topic_text)
        self.assertIn("Use conda activate agents", topic_text)
        self.assertIn("[Project conventions](topics/project-conventions.md)", index_text)

    def test_rejects_paths_outside_memory_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore.for_workspace(tmpdir)

            with self.assertRaisesRegex(ValueError, "outside memory root"):
                store.read("../README.md")

    def test_search_and_forget_memory_topic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore.for_workspace(tmpdir)
            store.write_topic(
                title="User preferences",
                content="- The user prefers concise final reports.",
            )

            hits = store.search("concise")
            removed = store.forget("User preferences", reason="test cleanup")
            index_text = (store.root / "MEMORY.md").read_text(encoding="utf-8")

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], "topics/user-preferences.md")
        self.assertTrue(removed["removed"])
        self.assertNotIn("user-preferences.md", index_text)

    def test_dream_lock_reclaims_stale_lock_and_rejects_live_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore.for_workspace(tmpdir)

            live = store.acquire_dream_lock("runner-a:memory-maintenance")
            blocked = store.acquire_dream_lock("runner-b:memory-maintenance")
            store.release_dream_lock()
            stale_time = time.time() - 7200
            store.lock_path.write_text(
                '{"owner":"old-runner:memory-maintenance","pid":999999,"created_at":1}',
                encoding="utf-8",
            )
            os.utime(store.lock_path, (stale_time, stale_time))
            reclaimed = store.acquire_dream_lock("runner-c:memory-maintenance", stale_seconds=3600)

        self.assertTrue(live["acquired"])
        self.assertFalse(blocked["acquired"])
        self.assertEqual(blocked["owner"], "runner-a:memory-maintenance")
        self.assertTrue(reclaimed["acquired"])
        self.assertEqual(reclaimed["owner"], "runner-c:memory-maintenance")

    def test_dream_due_status_uses_last_dream_and_runner_session_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store = MemoryStore.for_workspace(base)
            now = time.time()
            old_last = now - 90000
            store.record_dream_completed(completed_at=old_last)
            self._write_session(base, "runner-old", "root", old_last - 10)
            self._write_session(base, "runner-new-1", "root", now - 20)
            self._write_session(base, "runner-new-2", "root", now - 10)
            self._write_session(base, "runner-new-3", "functional--general--abc12345", now - 5)
            self._write_session(base, "runner-current", "root", now)
            config = SimpleNamespace(min_hours=24, min_sessions=2)

            due = store.dream_due_status(config=config, runner_id="runner-current")
            strict = store.dream_due_status(
                config=SimpleNamespace(min_hours=24, min_sessions=4),
                runner_id="runner-current",
            )

        self.assertTrue(due["auto_dream_due"])
        self.assertEqual(due["eligible_session_count"], 3)
        self.assertEqual(due["next_dream_reason"], "due")
        self.assertFalse(strict["auto_dream_due"])
        self.assertIn("need 4 sessions", strict["next_dream_reason"])

    @staticmethod
    def _write_session(base: Path, runner_id: str, agent_id: str, mtime: float) -> Path:
        """Write only the Manager session path needed by the due-count test."""

        path = base / ".juice" / "runners" / runner_id / "agents" / agent_id / "session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"steps":[]}\n', encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path


if __name__ == "__main__":
    unittest.main()
