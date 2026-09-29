"""Planning tools must be available only to the Manager-owned Plan root."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from juice_agents.core.agent.tools.builtin.planning.planning_tools import ExitPlanTool, PlanTool


class ExitPlanToolSafetyTests(unittest.TestCase):
    """Cover malformed approval replies and trusted-root authorization."""

    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.plan_file = Path(self._temporary_directory.name) / "plans" / "root.md"
        self.plan_file.parent.mkdir(parents=True)
        self.plan_file.write_text("# Test Plan\n\nThis is a test plan.", encoding="utf-8")
        self.tool = ExitPlanTool()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _context(self, ask_response: object) -> SimpleNamespace:
        """Create the minimum Manager-owned root identity for a tool call."""

        owner = SimpleNamespace()
        managed = SimpleNamespace(agent_id="root", is_root=True, instance=owner)
        runner = SimpleNamespace(
            mode_id="plan",
            root_agent_id="root",
            agent_manager=SimpleNamespace(get=lambda _agent_id: managed),
            has_capability=lambda _capability: True,
            plan_file_path=lambda: self.plan_file,
        )
        context = SimpleNamespace(
            runner=runner,
            agent_id="root",
            agent_name="root",
            owner_agent=owner,
            get_plan_file_path=Mock(return_value=str(self.plan_file)),
            ask_user=Mock(return_value=ask_response),
            record_approved_plan_exit=Mock(),
        )
        owner.runner_context = context
        return context

    def _run(self, response: object) -> tuple[dict[str, object], SimpleNamespace]:
        context = self._context(response)
        self.tool.owner_agent = context.owner_agent
        return self.tool.forward(), context

    def test_handles_empty_selected_list(self) -> None:
        result, _ = self._run({"status": "answered", "selected": [], "custom_response": "", "error": ""})
        self.assertFalse(result["approved"])
        self.assertEqual(result["feedback"], "")

    def test_handles_none_selected(self) -> None:
        result, _ = self._run({"status": "answered", "selected": None, "custom_response": "", "error": ""})
        self.assertFalse(result["approved"])
        self.assertEqual(result["feedback"], "")

    def test_handles_missing_selected_field(self) -> None:
        result, _ = self._run({"status": "answered", "custom_response": "", "error": ""})
        self.assertFalse(result["approved"])
        self.assertEqual(result["feedback"], "")

    def test_handles_invalid_response_type(self) -> None:
        result, _ = self._run(None)
        self.assertFalse(result["approved"])
        self.assertIn("handler", str(result["error"]) + str(result["feedback"]))

    def test_normal_approval_still_works(self) -> None:
        result, context = self._run(
            {"status": "answered", "selected": [{"label": "Approve", "value": "approve"}], "custom_response": "", "error": ""}
        )
        self.assertTrue(result["approved"])
        self.assertEqual(result["plan"], "# Test Plan\n\nThis is a test plan.")
        context.record_approved_plan_exit.assert_called_once()

    def test_rejection_keeps_plan_mode_without_recorder_side_effect(self) -> None:
        result, context = self._run(
            {"status": "answered", "selected": [], "custom_response": "需要修改计划", "error": ""}
        )
        self.assertFalse(result["approved"])
        self.assertEqual(result["feedback"], "需要修改计划")
        context.record_approved_plan_exit.assert_not_called()

    def test_subagent_cannot_impersonate_root_to_request_approval(self) -> None:
        context = self._context({"status": "answered", "selected": [{"value": "approve"}]})
        child = SimpleNamespace(runner_context=context, name="root")
        context.runner.agent_manager = SimpleNamespace(
            get=lambda _agent_id: SimpleNamespace(agent_id="root", is_root=False, instance=child)
        )
        self.tool.owner_agent = child

        result = self.tool.forward()

        self.assertFalse(result["approved"])
        self.assertIn("trusted root", str(result["error"]))
        context.record_approved_plan_exit.assert_not_called()


class PlanToolSafetyTests(unittest.TestCase):
    """A forged planning tool must not create a plan path or file."""

    def test_subagent_cannot_write_plan_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / "uncreated" / "root.md"
            owner = SimpleNamespace()
            managed = SimpleNamespace(agent_id="child", is_root=False, instance=owner)
            runner = SimpleNamespace(
                mode_id="plan",
                root_agent_id="root",
                agent_manager=SimpleNamespace(get=lambda _agent_id: managed),
                has_capability=lambda _capability: True,
            )
            owner.runner_context = SimpleNamespace(
                runner=runner,
                agent_id="child",
                get_plan_file_path=lambda: str(target),
            )
            tool = PlanTool(file_path=str(target))
            tool.owner_agent = owner

            with self.assertRaisesRegex(PermissionError, "可信 root"):
                tool.forward("# forged")

            self.assertFalse(target.exists())
            self.assertFalse(target.parent.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
