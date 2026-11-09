"""Public-SDK contracts for the self-contained business Showcase packages.

The user-facing packages never include an offline path.  These tests put
fakes at the public Runner boundary instead, so no provider credential
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
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from examples import _runtime
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.agents.types import AgentConfig


class _FakeRunner:
    """Small public Runner double recording stream and lifecycle calls."""

    def __init__(self, *, runner_id: str = "showcase-runner", error: Exception | None = None) -> None:
        self.root_config = AgentConfig(name="root", instructions="existing policy")
        self.config_context = ConfigurationContext.from_workspace(Path.cwd())
        self._root_instance = SimpleNamespace(tools={})
        self.runner_id = runner_id
        self.error = error
        self.tasks: list[str] = []
        self.stop_calls = 0
        self.ask_handler = None

    def set_ask_handler(self, handler) -> None:
        self.ask_handler = handler

    def _root(self):
        """Expose the actual scoped-capability installer surface without models."""

        return SimpleNamespace(instance=self._root_instance)

    def stream(self, task: str):
        self.tasks.append(task)
        if self.error is not None:
            raise self.error
        yield {
            "kind": "step_end",
            "agent_name": "manager",
            "step_num": 1,
            "data": {"step": {"model_output": "classified feedback"}},
        }
        yield {"kind": "round_end", "data": {"result": {"round_id": "r1", "status": "completed", "output": "business result"}}}

    def stop(self) -> None:
        self.stop_calls += 1


class _RunnerFactory:
    """Public ``Runner.create/resume`` double preserving workspace and options."""

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


@dataclass(frozen=True)
class _Showcase:
    """One package's business input and expected registered ModeProfile."""

    module_name: str
    business_input: str
    agent_mode: str
    stream_task: str | None = None
    has_evolution_policy: bool = False


SHOWCASES = (
    _Showcase(
        "examples.agent_research.__main__",
        "调查量子计算在药物研发中的最新可验证进展",
        "agent",
        stream_task="$workflow:deep_research 调查量子计算在药物研发中的最新可验证进展",
    ),
    _Showcase(
        "examples.agent_evolution.__main__",
        "工单 OPS-42：增加能归一化客户反馈标签的工具并委派复核",
        "agent",
        has_evolution_policy=True,
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

    def test_runtime_binds_workspace_and_interaction_after_create_and_resume(self):
        """Runtime callbacks are rebound without replacing persisted permissions."""

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runner = _FakeRunner()
            factory = _RunnerFactory(runner)
            args = argparse.Namespace(permission_mode="accept", agent_type="react", resume=None)
            created = _runtime.create_or_resume_runner(
                args, agent_mode="group", workspace=workspace, runner_cls=factory,
            )
            self.assertIs(created, runner)
            self.assertIs(runner.ask_handler, _runtime.console_ask)
            self.assertEqual(factory.create_calls, [{
                "base_dir": workspace, "permission_mode": "accept", "runner_config": "group",
            }])

            # A recovered Runner has no durable UI callback. The shared runtime
            # must install the current process's handler on this path as well.
            runner.ask_handler = None
            args.resume = "existing-runner"
            restored = _runtime.create_or_resume_runner(
                args, agent_mode="group", workspace=workspace, runner_cls=factory,
            )
            self.assertIs(restored, runner)
            self.assertIs(runner.ask_handler, _runtime.console_ask)
            self.assertEqual(factory.resume_calls, [{
                "base_dir": workspace, "runner_id": "existing-runner",
            }])

    def test_stream_error_still_stops_runner(self):
        runner = _FakeRunner(error=RuntimeError("model stream failed"))
        factory = _RunnerFactory(runner)
        args = argparse.Namespace(
            model=None,
            model_effort=None,
            agent_type="react",
            permission_mode="default",
            resume=None,
        )

        with self.assertRaisesRegex(RuntimeError, "model stream failed"):
            _runtime.run_showcase(
                args,
                agent_mode="group",
                workspace=Path.cwd(),
                task="process feedback",
                writer=lambda _line: None,
                runner_cls=factory,
            )

        self.assertEqual(runner.stop_calls, 1)


class ShowcaseEntryPointTests(unittest.TestCase):
    """Execute every package entry through real parsing and fake public SDK calls."""

    def test_entries_create_new_runner_in_their_own_folder_and_stream_business_input(self):
        for showcase in SHOWCASES:
            with self.subTest(module=showcase.module_name):
                module = importlib.import_module(showcase.module_name)
                runner = _FakeRunner()
                factory = _RunnerFactory(runner)
                stdout = io.StringIO()

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
                with ExitStack() as patches:
                    patches.enter_context(patch.object(_runtime.Runner, "create", side_effect=factory.create))
                    patches.enter_context(patch.object(_runtime.Runner, "resume", side_effect=factory.resume))
                    patches.enter_context(redirect_stdout(stdout))
                    result = module.main(argv)

                expected_workspace = Path(module.__file__).resolve().parent
                self.assertEqual(result, "business result")
                self.assertEqual(runner.tasks, [showcase.stream_task or showcase.business_input])
                self.assertEqual(runner.stop_calls, 1)
                self.assertIn("Runner ID: showcase-runner", stdout.getvalue())
                self.assertIn("[step_end] manager", stdout.getvalue())
                self.assertIn("[round_end]", stdout.getvalue())
                self.assertEqual(len(factory.create_calls), 1)
                self.assertEqual(factory.resume_calls, [])
                self.assertIs(runner.ask_handler, _runtime.console_ask)

                create_call = factory.create_calls[0]
                self.assertEqual(create_call["base_dir"], expected_workspace)
                self.assertEqual(create_call["runner_config"], showcase.agent_mode)
                self.assertEqual(create_call["permission_mode"], "accept")
                self.assertNotIn("agent_type", create_call)
                self.assertEqual(runner.root_config.agent_type, "codeact")
                self.assertEqual(runner.root_config.model_config_name, "showcase-model")
                self.assertEqual(runner.root_config.model_effort, "medium")
                if showcase.has_evolution_policy:
                    self.assertIn("existing policy", runner.root_config.instructions)
                    self.assertIn("Controlled ticket capability evolution", runner.root_config.instructions)
                    self.assertNotIn(showcase.business_input, runner.root_config.instructions)
                if showcase.module_name == "examples.agent_research.__main__":
                    self.assertEqual(runner._root_instance.tools["workflow_run"].workflow_name, "deep_research")
                    self.assertEqual(runner._root_instance._manual_workflow_authorization, "deep_research")

    def test_entries_resume_only_when_resume_id_is_explicit(self):
        for showcase in SHOWCASES:
            with self.subTest(module=showcase.module_name):
                module = importlib.import_module(showcase.module_name)
                runner = _FakeRunner(runner_id=f"{showcase.agent_mode}-resume")
                factory = _RunnerFactory(runner)

                with ExitStack() as patches:
                    patches.enter_context(patch.object(_runtime.Runner, "create", side_effect=factory.create))
                    patches.enter_context(patch.object(_runtime.Runner, "resume", side_effect=factory.resume))
                    # Stream rendering is asserted in the create path above;
                    # capture it here so a normal test run stays concise.
                    with redirect_stdout(io.StringIO()):
                        module.main([showcase.business_input, "--resume", "existing-runner-001"])

                self.assertEqual(factory.create_calls, [])
                self.assertEqual(len(factory.resume_calls), 1)
                resume_call = factory.resume_calls[0]
                self.assertEqual(resume_call["runner_id"], "existing-runner-001")
                self.assertEqual(resume_call, {
                    "runner_id": "existing-runner-001", "base_dir": Path(module.__file__).resolve().parent,
                })
                self.assertIs(runner.ask_handler, _runtime.console_ask)
                if showcase.has_evolution_policy:
                    self.assertIn("Controlled ticket capability evolution", runner.root_config.instructions)
                if showcase.module_name == "examples.agent_research.__main__":
                    self.assertEqual(runner._root_instance.tools["workflow_run"].workflow_name, "deep_research")
                self.assertEqual(runner.tasks, [showcase.stream_task or showcase.business_input])
                self.assertEqual(runner.stop_calls, 1)

    def test_entries_reject_external_workspace_overrides(self):
        """A model must never be redirected into the caller's own repository."""

        for showcase in SHOWCASES:
            with self.subTest(module=showcase.module_name):
                module = importlib.import_module(showcase.module_name)
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    module.main([showcase.business_input, "--workspace", "/outside-workspace"])



if __name__ == "__main__":
    unittest.main()
