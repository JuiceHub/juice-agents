"""Public-SDK contracts for the self-contained business Showcase packages.

The user-facing packages never include an offline path.  These tests put
fakes at the public Juice/Runner boundary instead, so no provider credential
or model response is needed to test command parsing and Runner lifecycle.
"""

from __future__ import annotations

import argparse
import importlib
import io
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from examples import _runtime


class _FakeRunner:
    """Small public Runner double recording stream and lifecycle calls."""

    def __init__(self, *, runner_id: str = "showcase-runner", error: Exception | None = None) -> None:
        self.runner_id = runner_id
        self.error = error
        self.tasks: list[str] = []
        self.stop_calls = 0

    def stream(self, task: str):
        self.tasks.append(task)
        if self.error is not None:
            raise self.error
        yield {
            "kind": "action_step",
            "actor_name": "manager",
            "step_num": 1,
            "action_step": type("Step", (), {"output": "classified feedback"})(),
        }
        yield {"kind": "round_end", "outcome": "submitted", "output": "business result"}

    def stop(self) -> None:
        self.stop_calls += 1


class _FakeRunners:
    """Public ``Juice.runners`` double preserving create/resume options."""

    def __init__(self, runner: _FakeRunner) -> None:
        self.runner = runner
        self.create_calls: list[dict[str, Any]] = []
        self.resume_calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeRunner:
        self.create_calls.append(dict(kwargs))
        return self.runner

    def resume(self, **kwargs: Any) -> _FakeRunner:
        self.resume_calls.append(dict(kwargs))
        return self.runner


class _FakeJuice:
    """Workspace-bound SDK fake used at the public dependency boundary."""

    def __init__(self, runner: _FakeRunner) -> None:
        self.runners = _FakeRunners(runner)


class _CapturingJuice:
    """SDK constructor fake used to validate workspace and model forwarding."""

    received_kwargs: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).received_kwargs = dict(kwargs)
        self.runners = _FakeRunners(_FakeRunner())


@dataclass(frozen=True)
class _Showcase:
    """One package's business input and expected registered ModeProfile."""

    module_name: str
    business_input: str
    agent_mode: str
    stream_task: str | None = None
    has_evolution_builder: bool = False


SHOWCASES = (
    _Showcase(
        "examples.agent_research.__main__",
        "调查量子计算在药物研发中的最新可验证进展",
        "agent",
        stream_task="/deep-research 调查量子计算在药物研发中的最新可验证进展",
    ),
    _Showcase(
        "examples.agent_evolution.__main__",
        "工单 OPS-42：增加能归一化客户反馈标签的工具并委派复核",
        "agent",
        has_evolution_builder=True,
    ),
    _Showcase(
        "examples.group_operations.__main__",
        "客户反馈：导出报表很慢、偶尔失败，希望本周改善",
        "group",
    ),
    _Showcase(
        "examples.team_delivery.__main__",
        "为订单导出失败修复制定并交付一个可验证的小改动",
        "team",
    ),
)


class ShowcaseRuntimeTests(unittest.TestCase):
    """Verify workspace binding and safe cleanup in the shared public runtime."""

    def test_runtime_forwards_dedicated_workspace_and_model_selection(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            args = argparse.Namespace(model="verified-model", model_effort="high")
            _CapturingJuice.received_kwargs = None

            _runtime._juice_from_args(args, workspace=workspace, juice_cls=_CapturingJuice)

        self.assertEqual(
            _CapturingJuice.received_kwargs,
            {"workspace": workspace, "model_name": "verified-model", "model_effort": "high"},
        )

    def test_stream_error_still_stops_runner(self):
        runner = _FakeRunner(error=RuntimeError("model stream failed"))
        juice = _FakeJuice(runner)
        args = argparse.Namespace(
            model=None,
            model_effort=None,
            agent_type="react",
            permission_mode="default",
            resume=None,
        )

        with patch.object(_runtime, "_juice_from_args", return_value=juice):
            with self.assertRaisesRegex(RuntimeError, "model stream failed"):
                _runtime.run_showcase(
                    args,
                    agent_mode="group",
                    workspace=Path.cwd(),
                    task="process feedback",
                    writer=lambda _line: None,
                )

        self.assertEqual(runner.stop_calls, 1)


class ShowcaseEntryPointTests(unittest.TestCase):
    """Execute every package entry through real parsing and fake public SDK calls."""

    def test_entries_create_new_runner_in_their_own_folder_and_stream_business_input(self):
        for showcase in SHOWCASES:
            with self.subTest(module=showcase.module_name):
                module = importlib.import_module(showcase.module_name)
                runner = _FakeRunner()
                juice = _FakeJuice(runner)
                captured_workspaces: list[Path] = []
                stdout = io.StringIO()

                def _juice_from_args(_args, *, workspace, juice_cls=_runtime.Juice):
                    del juice_cls
                    captured_workspaces.append(workspace)
                    return juice

                argv = [
                    showcase.business_input,
                    "--model",
                    "showcase-model",
                    "--model-effort",
                    "medium",
                    "--agent-type",
                    "codeact",
                    "--permission-mode",
                    "accept",
                ]
                with patch.object(_runtime, "_juice_from_args", side_effect=_juice_from_args), redirect_stdout(stdout):
                    result = module.main(argv)

                expected_workspace = Path(module.__file__).resolve().parent
                self.assertEqual(result, "business result")
                self.assertEqual(captured_workspaces, [expected_workspace])
                self.assertEqual(runner.tasks, [showcase.stream_task or showcase.business_input])
                self.assertEqual(runner.stop_calls, 1)
                self.assertIn("Runner ID: showcase-runner", stdout.getvalue())
                self.assertIn("[action_step] manager", stdout.getvalue())
                self.assertIn("[round_end]", stdout.getvalue())
                self.assertEqual(len(juice.runners.create_calls), 1)
                self.assertEqual(juice.runners.resume_calls, [])

                create_call = juice.runners.create_calls[0]
                self.assertEqual(create_call["agent_mode"], showcase.agent_mode)
                self.assertEqual(create_call["permission_mode"], "accept")
                self.assertEqual(create_call["agent_type"], "codeact")
                self.assertEqual("agent_builder" in create_call, showcase.has_evolution_builder)
                if showcase.has_evolution_builder:
                    self._assert_evolution_builder(create_call["agent_builder"], expected_workspace, showcase)

    def test_entries_resume_only_when_resume_id_is_explicit(self):
        for showcase in SHOWCASES:
            with self.subTest(module=showcase.module_name):
                module = importlib.import_module(showcase.module_name)
                runner = _FakeRunner(runner_id=f"{showcase.agent_mode}-resume")
                juice = _FakeJuice(runner)
                rebuilt_root: object | None = None

                with ExitStack() as patches:
                    patches.enter_context(patch.object(_runtime, "_juice_from_args", return_value=juice))
                    # A resumed evolution Agent must rebuild its customised root.
                    # Keep construction at the test boundary to avoid resolving
                    # a real workspace model during this CLI contract test.
                    if showcase.has_evolution_builder:
                        rebuilt_root = object()
                        patches.enter_context(
                            patch.object(module, "build_evolution_agent", return_value=rebuilt_root)
                        )
                    # Stream rendering is asserted in the create path above;
                    # capture it here so a normal test run stays concise.
                    with redirect_stdout(io.StringIO()):
                        module.main([showcase.business_input, "--resume", "existing-runner-001"])

                self.assertEqual(juice.runners.create_calls, [])
                self.assertEqual(len(juice.runners.resume_calls), 1)
                resume_call = juice.runners.resume_calls[0]
                self.assertEqual(resume_call["runner_id"], "existing-runner-001")
                self.assertEqual(resume_call["agent_type"], "react")
                if showcase.has_evolution_builder:
                    self.assertIs(resume_call["root_agent"], rebuilt_root)
                else:
                    self.assertNotIn("root_agent", resume_call)
                self.assertEqual(runner.tasks, [showcase.stream_task or showcase.business_input])
                self.assertEqual(runner.stop_calls, 1)

    def test_entries_reject_external_workspace_overrides(self):
        """A model must never be redirected into the caller's own repository."""

        for showcase in SHOWCASES:
            with self.subTest(module=showcase.module_name):
                module = importlib.import_module(showcase.module_name)
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    module.main([showcase.business_input, "--workspace", "/outside-workspace"])

    def _assert_evolution_builder(self, builder, workspace: Path, showcase: _Showcase) -> None:
        """Ensure fixed policy remains separate from the dynamic ticket text."""

        class _BuiltAgent:
            instructions = "existing policy"
            refreshed = False

            def refresh_system_prompt(self) -> None:
                self.refreshed = True

        built_agent = _BuiltAgent()
        with patch(
            "juice_agents.core.agent.builtin.build_general_agent",
            return_value=built_agent,
        ) as build_agent:
            self.assertIs(builder(), built_agent)

        build_agent.assert_called_once_with(
            workspace_dir=workspace,
            model_name="showcase-model",
            model_effort="medium",
            agent_type="codeact",
        )
        self.assertTrue(built_agent.refreshed)
        self.assertIn("existing policy", built_agent.instructions)
        self.assertIn("Controlled ticket capability evolution", built_agent.instructions)
        self.assertNotIn(showcase.business_input, built_agent.instructions)


if __name__ == "__main__":
    unittest.main()
