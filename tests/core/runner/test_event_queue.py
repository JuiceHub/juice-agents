import unittest
import threading
import time

from juice_agents.core.agent.attachments import merge_runtime_attachments
from juice_agents.core.runner.execution.event_queue import RunnerEventQueue


class RunnerEventQueueTests(unittest.TestCase):
    def test_enqueue_and_drain_are_isolated_by_recipient_agent_id(self):
        queue = RunnerEventQueue()
        queue.enqueue(
            "agent:a",
            {
                "event_type": "async_task_notification",
                "created_at": 1.0,
                "payload": {"async_task_id": "job_a", "kind": "agent_dispatch", "status": "completed"},
            },
        )
        queue.enqueue(
            "agent:b",
            {
                "event_type": "async_task_notification",
                "created_at": 2.0,
                "payload": {"async_task_id": "job_b", "kind": "shell_command", "status": "failed"},
            },
        )

        self.assertTrue(queue.has_events("agent:a"))
        self.assertTrue(queue.has_events("agent:b"))

        events_a = queue.drain("agent:a")
        self.assertEqual(len(events_a), 1)
        self.assertEqual(events_a[0]["payload"]["async_task_id"], "job_a")
        self.assertFalse(queue.has_events("agent:a"))
        self.assertTrue(queue.has_events("agent:b"))

        events_b = queue.drain("agent:b")
        self.assertEqual(len(events_b), 1)
        self.assertEqual(events_b[0]["payload"]["async_task_id"], "job_b")
        self.assertFalse(queue.has_events("agent:b"))

    def test_merge_runtime_attachments_orders_async_task_notifications_before_inbox_when_timestamp_matches(self):
        attachments = merge_runtime_attachments(
            runtime_events=[
                {
                    "event_type": "async_task_notification",
                    "created_at": 10.0,
                    "payload": {"async_task_id": "job_1", "kind": "agent_dispatch", "status": "completed"},
                }
            ],
            inbox_messages=[
                {
                        "from": "teamlead",
                    "text": "check task_1",
                    "summary": "assignment",
                    "timestamp": "1970-01-01T00:00:10Z",
                }
            ],
        )
        self.assertEqual(
            [attachment["attachment_type"] for attachment in attachments],
            ["async_task_notification", "inbox_message"],
        )

    def test_wait_drain_blocks_until_event_arrives(self):
        queue = RunnerEventQueue()

        def _emit_later():
            time.sleep(0.05)
            queue.enqueue(
                "agent:a",
                {
                    "event_type": "async_task_notification",
                    "payload": {"async_task_id": "job_wait", "status": "completed"},
                },
            )

        worker = threading.Thread(target=_emit_later)
        worker.start()
        try:
            started = time.time()
            events = queue.wait_drain("agent:a", timeout=0.5)
            elapsed = time.time() - started
        finally:
            worker.join()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["async_task_id"], "job_wait")
        self.assertGreaterEqual(elapsed, 0.03)
        self.assertFalse(queue.has_events("agent:a"))

    def test_wait_drain_times_out_without_event(self):
        queue = RunnerEventQueue()

        started = time.time()
        events = queue.wait_drain("agent:none", timeout=0.05)
        elapsed = time.time() - started

        self.assertEqual(events, [])
        self.assertGreaterEqual(elapsed, 0.03)


if __name__ == "__main__":
    unittest.main()
