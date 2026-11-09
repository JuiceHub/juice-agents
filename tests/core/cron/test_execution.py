"""Scheduled execution uses the real task store and isolated Runner lifetimes."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from juice_agents.core.cron import (
    create_cron_task,
    execute_due_cron_tasks,
    list_cron_tasks,
)
from juice_agents.core.runner import Runner


def _runner(runner_id: str, output: str = "done") -> Mock:
    runner = Mock(spec=["runner_id", "set_ask_handler", "run", "close"])
    runner.runner_id = runner_id
    runner.run.return_value = output
    return runner


class CronExecutionTests(unittest.TestCase):
    def test_due_prompts_use_independent_runners_and_forward_runtime_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            first = create_cron_task(workspace, "* * * * *", "run once", recurring=False)
            second = create_cron_task(workspace, "* * * * *", "run again", recurring=True)
            now = second["created_at"] + 120
            runners = [_runner("runner-first", "first output"), _runner("runner-second")]
            handler = Mock()
            overrides = {"models": {"default": "test-model"}}
            config_path = workspace / "runtime.yaml"

            with patch.object(Runner, "create", side_effect=runners) as create:
                results = execute_due_cron_tasks(
                    workspace,
                    now=now,
                    permission_mode="accept",
                    runner_config="plan",
                    runtime_config_path=config_path,
                    runtime_overrides=overrides,
                    ask_handler=handler,
                )
                # A second poll at the same instant must not execute either
                # the removed one-shot or the advanced recurring task again.
                self.assertEqual(execute_due_cron_tasks(workspace, now=now), [])

            expected_create = call(
                base_dir=workspace.resolve(),
                permission_mode="accept",
                runner_config="plan",
                runtime_config_path=config_path,
                runtime_overrides=overrides,
            )
            self.assertEqual(create.call_args_list, [expected_create, expected_create])
            self.assertEqual([result["task"]["id"] for result in results], [first["id"], second["id"]])
            self.assertEqual([result["runner_id"] for result in results], ["runner-first", "runner-second"])
            self.assertEqual([result["status"] for result in results], ["completed", "completed"])
            self.assertEqual(results[0]["output"], "first output")
            for runner, prompt in zip(runners, ["run once", "run again"]):
                self.assertEqual(
                    runner.method_calls,
                    [call.set_ask_handler(handler), call.run(prompt), call.close()],
                )
            remaining = list_cron_tasks(workspace)
            self.assertEqual([task["id"] for task in remaining], [second["id"]])
            self.assertEqual(remaining[0]["last_fired_at"], now)

    def test_creation_execution_and_cleanup_failures_do_not_skip_claimed_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks = [
                create_cron_task(tmpdir, "* * * * *", prompt, recurring=False)
                for prompt in ["create fails", "run fails", "close fails", "succeeds"]
            ]
            failed_run = _runner("runner-run-failure")
            failed_run.run.side_effect = RuntimeError("model unavailable")
            failed_close = _runner("runner-close-failure")
            failed_close.close.side_effect = RuntimeError("cleanup unavailable")
            successful = _runner("runner-success")

            with (
                patch.object(
                    Runner,
                    "create",
                    side_effect=[ValueError("invalid configuration"), failed_run, failed_close, successful],
                ) as create,
                self.assertLogs("juice_agents.core.cron.execution", level="ERROR") as logs,
            ):
                results = execute_due_cron_tasks(tmpdir, now=tasks[-1]["created_at"] + 120)

            self.assertEqual(create.call_count, 4)
            self.assertEqual([result["status"] for result in results], ["failed", "failed", "completed", "completed"])
            self.assertIsNone(results[0]["runner_id"])
            self.assertEqual(results[0]["error"], "invalid configuration")
            self.assertEqual(results[1]["runner_id"], "runner-run-failure")
            self.assertEqual(results[1]["error"], "model unavailable")
            self.assertEqual(results[2]["output"], "done")
            self.assertEqual(results[2]["cleanup_error"], "cleanup unavailable")
            for runner in [failed_run, failed_close, successful]:
                runner.set_ask_handler.assert_called_once_with(None)
                runner.close.assert_called_once_with()
            successful.run.assert_called_once_with("succeeds")
            self.assertEqual(len(logs.records), 3)
            # Claiming happens before execution; failed one-shot tasks remain
            # consumed, matching the lower-level fire_due_cron_tasks contract.
            self.assertEqual(list_cron_tasks(tmpdir), [])

    def test_handler_binding_failure_closes_runner_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task = create_cron_task(tmpdir, "* * * * *", "ask", recurring=False)
            runner = _runner("runner-bind-failure")
            runner.set_ask_handler.side_effect = RuntimeError("invalid handler")
            with (
                patch.object(Runner, "create", return_value=runner),
                self.assertLogs("juice_agents.core.cron.execution", level="ERROR"),
            ):
                results = execute_due_cron_tasks(tmpdir, now=task["created_at"] + 120)

            self.assertEqual(results[0]["status"], "failed")
            self.assertEqual(results[0]["error"], "invalid handler")
            runner.run.assert_not_called()
            runner.close.assert_called_once_with()

    def test_empty_workspace_does_not_create_runner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(Runner, "create") as create:
                self.assertEqual(execute_due_cron_tasks(tmpdir), [])
            create.assert_not_called()

    def test_typed_round_errors_preserve_cancel_and_partial_output(self):
        from juice_agents.core.agent.contracts import RoundCancelledError, RoundFailedError, RoundResult

        for status, error_type in (("failed", RoundFailedError), ("cancelled", RoundCancelledError)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmpdir:
                task = create_cron_task(tmpdir, "* * * * *", "work", recurring=False)
                result = RoundResult("round-test", status, output="partial answer", reason="test-stop")
                runner = _runner("runner-error")
                runner.run.side_effect = error_type(result)
                with patch.object(Runner, "create", return_value=runner):
                    results = execute_due_cron_tasks(tmpdir, now=task["created_at"] + 120)
                self.assertEqual(results[0]["status"], status)
                self.assertEqual(results[0]["output"], "partial answer")
                self.assertEqual(results[0]["result"], result.to_dict())
                runner.close.assert_called_once_with()

    def test_nonexistent_workspace_is_rejected_without_creating_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "missing"
            with self.assertRaisesRegex(ValueError, "existing directory"):
                execute_due_cron_tasks(workspace)
            self.assertFalse(workspace.exists())


if __name__ == "__main__":
    unittest.main()
