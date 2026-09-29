import json
import tempfile
import unittest
from pathlib import Path

from juice_agents.core.cron.lock import cron_lock_path
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
