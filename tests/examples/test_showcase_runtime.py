"""Functional coverage for readable example traces and the real SDK boundary."""

from __future__ import annotations

import argparse
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from examples import _runtime
from juice_agents.core.agent.context.steps import ActionStep
from juice_agents.core.config.model_catalog import load_runtime_model_config


@pytest.mark.parametrize("source", ["repository", "example", "process"])
def test_example_credentials_resolve_from_any_cwd_and_restore_environment(monkeypatch, tmp_path, source):
    """Exercise the actual catalog reader with all supported credential sources."""

    repo = tmp_path / "repository"
    workspace = repo / "examples" / "demo"
    workspace.mkdir(parents=True)
    (repo / ".env").write_text("DOUBAO_API_KEY=repository-key\n", encoding="utf-8")
    if source in {"example", "process"}:
        (workspace / ".env").write_text("DOUBAO_API_KEY=example-key\n", encoding="utf-8")
    monkeypatch.setattr(_runtime, "_REPO_ROOT", repo)
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    if source == "process":
        monkeypatch.setenv("DOUBAO_API_KEY", "process-key")
    monkeypatch.chdir(tmp_path)

    with _runtime._showcase_environment(workspace):
        config = load_runtime_model_config(model_name="doubao_lite", workspace_dir=workspace,
                                           dotenv_path=workspace / ".env")
        assert config.api_key == f"{source}-key"
    assert os.environ.get("DOUBAO_API_KEY") == ("process-key" if source == "process" else None)


def test_example_environment_is_restored_if_runner_creation_fails(monkeypatch, tmp_path):
    """A failed embedded launch must not leave shared credentials in the caller."""

    (tmp_path / ".env").write_text("DOUBAO_API_KEY=repository-key\n", encoding="utf-8")
    monkeypatch.setattr(_runtime, "_REPO_ROOT", tmp_path)
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    args = argparse.Namespace(model=None, model_effort=None, agent_type=None,
                              permission_mode="default", resume=None)

    def fail_create(*_args, **_kwargs):
        assert os.environ["DOUBAO_API_KEY"] == "repository-key"
        raise RuntimeError("cannot create runner")

    monkeypatch.setattr(_runtime, "create_or_resume_runner", fail_create)
    with pytest.raises(RuntimeError, match="cannot create runner"):
        _runtime.run_showcase(args, agent_mode="agent", workspace=tmp_path,
                              task="demo", writer=lambda _line: None)
    assert "DOUBAO_API_KEY" not in os.environ


class ShowcaseTraceTests(unittest.TestCase):
    """Use actual SDK step objects and Team events rather than invented fields."""

    def test_live_and_serialized_action_steps_show_model_output_and_observations(self):
        step = ActionStep(
            step_num=2,
            model_output="Call the cache validation tool",
            observations=["All cache checks passed"],
        )
        rendered = []
        for value in (step, asdict(step)):
            lines = []
            _runtime.render_event(
                {"kind": "action_step", "agent_name": "root", "action_step": value},
                writer=lines.append,
            )
            rendered.append(lines)
        self.assertEqual(rendered[0], rendered[1])
        self.assertIn("[action_step] root step=2", rendered[0][0])
        self.assertIn(step.model_output, rendered[0][0])
        self.assertIn(step.observations[0], rendered[0][0])

    def test_tool_progress_is_visible_but_duplicate_step_events_are_suppressed(self):
        lines = []
        for event_type, data in (
            ("thinking", {"model_output": "repeated text"}),
            ("tool_start", {"tool_name": "bash", "tool_args": {"command": "pytest"}}),
            ("tool_end", {"tool_name": "bash", "observation": "3 passed", "error": None}),
            ("step_complete", {"step": "repeated step"}),
        ):
            _runtime.render_event(
                {"kind": "action_event", "agent_name": "root", "event_type": event_type, "data": data},
                writer=lines.append,
            )
        self.assertEqual(len(lines), 2)
        self.assertIn("[tool_start] root bash", lines[0])
        self.assertIn("pytest", lines[0])
        self.assertIn("3 passed", lines[1])

    def test_team_trace_uses_real_update_shape_without_dumping_snapshot(self):
        lines = []
        _runtime.render_event(
            {
                "kind": "team_update",
                "update": {"type": "task_completed", "task_id": "task-1", "member": "reviewer"},
                "snapshot": {
                    "tasks": [{"task_id": "task-1", "title": "Review cache", "status": "completed"}],
                    "messages": [{"content": "private irrelevant context"}],
                },
            },
            writer=lines.append,
        )
        self.assertIn("task_completed", lines[0])
        self.assertIn("Review cache", lines[0])
        self.assertNotIn("private irrelevant context", lines[0])

    def test_intermediate_payload_is_bounded_and_final_deliverable_is_complete(self):
        large_output = "x" * 5000
        lines = []
        _runtime.render_event(
            {"kind": "action_step", "action_step": ActionStep(1, large_output)}, writer=lines.append
        )
        _runtime.render_event(
            {"kind": "round_end", "outcome": "submitted", "output": large_output}, writer=lines.append
        )
        self.assertLess(len(lines[0]), 1400)
        self.assertIn("[truncated]", lines[0])
        self.assertTrue(lines[1].endswith(large_output))


class ShowcaseSdkTests(unittest.TestCase):
    """Exercise actual create/resume signatures and persistence without a provider."""

    def test_real_runner_modes_create_resume_and_cleanup_in_temporary_workspaces(self):
        # No model is acquired during creation/resume. This catches obsolete SDK
        # arguments that permissive fake RunnerManagers would otherwise hide.
        args = argparse.Namespace(
            model=None, model_effort=None, agent_type=None,
            permission_mode="default", resume=None,
        )
        for mode in ("agent", "group", "team"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                runner = _runtime.create_or_resume_runner(args, agent_mode=mode, workspace=workspace)
                runner_id = runner.runner_id
                try:
                    self.assertEqual(runner.mode_id, mode)
                    self.assertEqual(runner.base_dir, workspace)
                    self.assertEqual(runner.root_config.max_steps, 20)
                finally:
                    runner.stop()
                resumed_args = argparse.Namespace(**{**vars(args), "resume": runner_id})
                resumed = _runtime.create_or_resume_runner(resumed_args, agent_mode=mode, workspace=workspace)
                try:
                    self.assertEqual(resumed.runner_id, runner_id)
                    self.assertEqual(resumed.mode_id, mode)
                    self.assertIsNotNone(resumed._ask_handler)
                finally:
                    resumed.stop()

    def test_console_approval_requires_explicit_selection(self):
        request = {
            "question": "Allow writing the report?",
            "options": [
                {"label": "Allow once", "value": "allow_once", "description": ""},
                {"label": "Deny", "value": "deny", "description": ""},
            ],
            "allow_custom": False,
        }
        with patch.object(_runtime.sys.stdin, "isatty", return_value=True), patch(
            "builtins.input", return_value="1"
        ), redirect_stdout(io.StringIO()):
            response = _runtime.console_ask(request)
        self.assertEqual(response["status"], "answered")
        self.assertEqual(response["selected"][0]["value"], "allow_once")
        with patch.object(_runtime.sys.stdin, "isatty", return_value=False):
            response = _runtime.console_ask(request)
        self.assertEqual(response["status"], "error")
        self.assertIn("--permission-mode accept", response["error"])


if __name__ == "__main__":
    unittest.main()
