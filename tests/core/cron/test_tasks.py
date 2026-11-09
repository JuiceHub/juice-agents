import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from juice_agents.core.cron.lock import acquire_cron_lock, cron_lock_path
from juice_agents.core.cron.tasks import (
    create_cron_task,
    cron_tasks_path,
    delete_cron_task,
    fire_due_cron_tasks,
    list_cron_tasks,
)


class CronTaskStoreTests(unittest.TestCase):
    def test_malformed_file_reads_as_empty_task_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = cron_tasks_path(tmpdir)
            path.parent.mkdir(parents=True)
            path.write_text("{not json", encoding="utf-8")

            self.assertEqual(list_cron_tasks(tmpdir), [])

    def test_create_list_and_delete_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task = create_cron_task(tmpdir, "*/5 * * * *", "check deploy", recurring=True)

            self.assertTrue(task["id"])
            tasks = list_cron_tasks(tmpdir)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["prompt"], "check deploy")
            self.assertTrue(tasks[0]["recurring"])

            result = delete_cron_task(tmpdir, task["id"])

            self.assertTrue(result["deleted"])
            self.assertEqual(list_cron_tasks(tmpdir), [])

    def test_idle_polls_never_lock_or_write_workspace_files(self):
        for store_state in ("missing", "empty", "not_due"):
            with self.subTest(store_state=store_state), tempfile.TemporaryDirectory() as tmpdir:
                workspace = Path(tmpdir)
                path = cron_tasks_path(workspace)
                now = 1_700_000_000.0
                if store_state == "empty":
                    path.parent.mkdir()
                    path.write_text('{"tasks": []}\n', encoding="utf-8")
                elif store_state == "not_due":
                    task = create_cron_task(workspace, "* * * * *", "later")
                    now = task["created_at"]

                # Snapshot directory entries as well as file data and mtime:
                # an absent store must not cause .juice/ to be created either.
                entries_before = set(workspace.rglob("*"))
                file_before = (path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else None
                with patch(
                    "juice_agents.core.cron.tasks.acquire_cron_lock",
                    side_effect=AssertionError("idle poll must not acquire a lock"),
                ) as acquire:
                    for _ in range(3):
                        self.assertEqual(fire_due_cron_tasks(workspace, now=now), [])

                acquire.assert_not_called()
                self.assertEqual(set(workspace.rglob("*")), entries_before)
                file_after = (path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else None
                self.assertEqual(file_after, file_before)
                self.assertFalse(cron_lock_path(workspace).exists())

    def test_due_snapshot_is_rechecked_after_another_instance_claims_task(self):
        for recurring in (False, True):
            with self.subTest(recurring=recurring), tempfile.TemporaryDirectory() as tmpdir:
                task = create_cron_task(tmpdir, "* * * * *", "already claimed", recurring=recurring)
                now = task["created_at"] + 120
                path = cron_tasks_path(tmpdir)
                claimed_file = None

                @contextmanager
                def claim_before_acquiring(*args, **kwargs):
                    nonlocal claimed_file
                    with acquire_cron_lock(*args, **kwargs):
                        # Reproduce a different CLI claiming the task between
                        # our optimistic read and the authoritative locked read.
                        remaining = [{**task, "last_fired_at": now}] if recurring else []
                        path.write_text(json.dumps({"tasks": remaining}), encoding="utf-8")
                        claimed_file = (path.read_bytes(), path.stat().st_mtime_ns)
                        yield

                with patch(
                    "juice_agents.core.cron.tasks.acquire_cron_lock",
                    side_effect=claim_before_acquiring,
                ) as acquire:
                    self.assertEqual(fire_due_cron_tasks(tmpdir, now=now), [])

                acquire.assert_called_once()
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), claimed_file)
                self.assertFalse(cron_lock_path(tmpdir).exists())
                remaining = list_cron_tasks(tmpdir)
                self.assertEqual([item["id"] for item in remaining], [task["id"]] if recurring else [])
                if recurring:
                    self.assertEqual(remaining[0]["last_fired_at"], now)

    def test_one_shot_fire_deletes_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task = create_cron_task(tmpdir, "* * * * *", "run once", recurring=False)
            created_at = task["created_at"]

            fired = fire_due_cron_tasks(tmpdir, now=created_at + 120)

            self.assertEqual([item["id"] for item in fired], [task["id"]])
            self.assertEqual(list_cron_tasks(tmpdir), [])

    def test_recurring_fire_updates_last_fired_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task = create_cron_task(tmpdir, "* * * * *", "run often", recurring=True)
            fired_at = task["created_at"] + 120

            fired = fire_due_cron_tasks(tmpdir, now=fired_at)

            self.assertEqual([item["id"] for item in fired], [task["id"]])
            tasks = list_cron_tasks(tmpdir)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["last_fired_at"], fired_at)
            self.assertEqual(fire_due_cron_tasks(tmpdir, now=fired_at), [])

    def test_stale_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = cron_lock_path(tmpdir)
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text(
                json.dumps({"owner": "dead", "pid": 99999999, "acquired_at": 1}),
                encoding="utf-8",
            )

            task = create_cron_task(tmpdir, "*/5 * * * *", "check stale lock")

            self.assertTrue(task["id"])
            self.assertFalse(lock_path.exists())

    def test_fire_due_under_lock_does_not_double_return_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task = create_cron_task(tmpdir, "* * * * *", "once", recurring=False)
            now = task["created_at"] + 120

            first = fire_due_cron_tasks(tmpdir, now=now)
            second = fire_due_cron_tasks(tmpdir, now=now)

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
