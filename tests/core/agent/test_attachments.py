import unittest

from juice_agents.core.agent.attachments import serialize_runtime_attachments_text


class PlanModeAttachmentTests(unittest.TestCase):
    def test_async_task_attachment_uses_launch_limit_without_mutating_raw_result(self):
        raw_result = {"stdout": "z" * 1_000}
        attachment = {
            "attachment_type": "async_task_notification",
            "created_at": 1.0,
            "payload": {
                "async_task_id": "job_1",
                "status": "completed",
                "summary": "done",
                "result": raw_result,
                "max_observation_chars": 80,
            },
        }

        text = serialize_runtime_attachments_text([attachment])

        body = text.split(">", 2)[2].split("</async_task_notification>", 1)[0].strip()
        self.assertLessEqual(len(body), 80)
        self.assertIn("truncated original_length=", body)
        self.assertEqual(raw_result, {"stdout": "z" * 1_000})

    def test_plan_mode_attachment_serializes_reminder(self):
        text = serialize_runtime_attachments_text(
            [
                {
                    "attachment_type": "plan_mode",
                    "created_at": 1.0,
                    "payload": {
                        "reminder_type": "full",
                        "plan_file": "/tmp/plan.md",
                        "message": "You are still in Plan Mode.",
                    },
                }
            ]
        )

        self.assertIn("<plan_mode", text)
        self.assertIn('reminder_type="full"', text)
        self.assertIn('plan_file="/tmp/plan.md"', text)
        self.assertIn("You are still in Plan Mode.", text)

    def test_full_plan_mode_attachment_serializes_guardrails(self):
        text = serialize_runtime_attachments_text(
            [
                {
                    "attachment_type": "plan_mode",
                    "created_at": 1.0,
                    "payload": {
                        "reminder_type": "full",
                        "plan_file": "/tmp/plan.md",
                        "message": (
                            "Plan Mode is active. Use read-only tools only. "
                            "The plan file is the only file you may write. "
                            "Use exit_plan for approval."
                        ),
                    },
                }
            ]
        )

        self.assertIn("Plan Mode is active", text)
        self.assertIn("read-only tools", text)
        self.assertIn("only file you may write", text)
        self.assertIn("exit_plan", text)

    def test_plan_mode_exit_attachment_serializes_approved_plan(self):
        text = serialize_runtime_attachments_text(
            [
                {
                    "attachment_type": "plan_mode_exit",
                    "created_at": 1.0,
                    "payload": {
                        "plan_file": "/tmp/plan.md",
                        "approved_plan": "# Approved\n\n- implement",
                    },
                }
            ]
        )

        self.assertIn("<plan_mode_exit", text)
        self.assertIn('plan_file="/tmp/plan.md"', text)
        self.assertIn("User approved the plan", text)
        self.assertIn("# Approved", text)

    def test_goal_state_attachment_serializes_objective_and_budget(self):
        text = serialize_runtime_attachments_text(
            [
                {
                    "attachment_type": "goal_state",
                    "created_at": 1.0,
                    "payload": {
                        "objective": "Ship /goal",
                        "status": "active",
                        "turns_used": 1,
                        "max_turns": 12,
                    },
                }
            ]
        )

        self.assertIn("<goal_state", text)
        self.assertIn('status="active"', text)
        self.assertIn('turns_used="1"', text)
        self.assertIn("Ship /goal", text)

    def test_goal_evaluation_attachment_serializes_reason(self):
        text = serialize_runtime_attachments_text(
            [
                {
                    "attachment_type": "goal_evaluation",
                    "created_at": 1.0,
                    "payload": {
                        "completed": False,
                        "should_continue": True,
                        "reason": "Need tests",
                        "progress_fingerprint": "tests",
                    },
                }
            ]
        )

        self.assertIn("<goal_evaluation", text)
        self.assertIn('completed="False"', text)
        self.assertIn('should_continue="True"', text)
        self.assertIn("Need tests", text)

    def test_agent_user_message_attachment_serializes_user_instruction(self):
        text = serialize_runtime_attachments_text(
            [
                {
                    "attachment_type": "agent_user_message",
                    "created_at": 1.0,
                    "payload": {
                        "message_id": "agent_msg_1",
                        "from": "user",
                        "agent_name": "researcher",
                        "text": "Please focus on the API tests.",
                    },
                }
            ]
        )

        self.assertIn("<agent_user_message", text)
        self.assertIn('from="user"', text)
        self.assertIn('agent="researcher"', text)
        self.assertIn('message_id="agent_msg_1"', text)
        self.assertIn("Please focus on the API tests.", text)


if __name__ == "__main__":
    unittest.main()
