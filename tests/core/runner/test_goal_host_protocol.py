"""goals.py 的宿主协议契约。

goals 原先签名是 `runner: Any`，读者无法从签名判断它要用什么，测试也只能塞一个
完整 Runner。改成 GoalHost 窄协议后，这些测试锁住"goal 逻辑只需要 3 个必需成员"。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from juice_agents.core.runner import goals


class _MinimalLayout:
    """只提供 goal_path 的 layout 替身。"""

    def __init__(self, goal_path: Path) -> None:
        self.goal_path = goal_path


class _MinimalGoalHost:
    """满足 GoalHost 的最小实现 —— 没有 Agent、没有 async task、没有 profile。"""

    def __init__(self, goal_path: Path) -> None:
        self.layout = _MinimalLayout(goal_path)
        self.state: dict[str, Any] = {}
        self.persist_calls = 0

    def persist_runner_state(self) -> None:
        self.persist_calls += 1


class GoalHostProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.host = _MinimalGoalHost(Path(self._tmp.name) / "goal.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_minimal_host_satisfies_the_protocol(self) -> None:
        """goal 状态机不应该需要完整 Runner 才能跑。"""

        from juice_agents.core.runner.goals import GoalHost

        self.assertIsInstance(self.host, GoalHost)  # runtime_checkable 才成立

    def test_full_goal_lifecycle_on_a_minimal_host(self) -> None:
        created = goals.set_goal(self.host, "ship the refactor", max_turns=3)
        self.assertEqual(created["status"], "active")
        self.assertEqual(created["objective"], "ship the refactor")

        self.assertEqual(goals.load_goal(self.host)["status"], "active")

        paused = goals.pause_goal(self.host, reason="plan_mode")
        self.assertEqual(paused["status"], "paused")

        resumed = goals.resume_goal(self.host)
        self.assertEqual(resumed["status"], "active")

        cleared = goals.clear_goal(self.host)
        self.assertEqual(cleared["status"], "cleared")
        self.assertIsNone(self.host.state["goal_ref"])
        # 清除后 goal.json 必须消失，否则下次 load 会读到僵尸目标。
        self.assertFalse(self.host.layout.goal_path.exists())
        self.assertEqual(goals.load_goal(self.host), {})

    def test_writes_land_in_the_hosts_goal_path(self) -> None:
        goals.set_goal(self.host, "persisted objective")
        self.assertTrue(self.host.layout.goal_path.exists())
        payload = json.loads(self.host.layout.goal_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["objective"], "persisted objective")

    def test_every_mutation_persists_runner_state(self) -> None:
        """goal_ref 是 manifest 的一部分，任何变更都必须落盘。"""

        goals.set_goal(self.host, "x")
        after_set = self.host.persist_calls
        self.assertGreater(after_set, 0)
        goals.pause_goal(self.host)
        self.assertGreater(self.host.persist_calls, after_set)

    def test_control_action_round_trip(self) -> None:
        goals.set_goal_control_action(self.host, "continue")
        self.assertEqual(goals.consume_goal_control_action(self.host), "continue")
        # 消费是一次性的：第二次读回落到 none，避免同一动作驱动两轮续跑。
        self.assertEqual(goals.consume_goal_control_action(self.host), "none")

    def test_optional_members_are_truly_optional(self) -> None:
        """evidence 在宿主缺少 permission_mode / evaluator_model 时也要能构造。"""

        goals.set_goal(self.host, "objective")
        evidence = goals.build_goal_evidence(self.host, {"final_output": "done"})
        self.assertEqual(evidence["permission_mode"], "")
        self.assertEqual(evidence["agent_mode"], "")
        self.assertEqual(evidence["root_agent_name"], "")

    def test_evaluator_falls_back_without_a_model(self) -> None:
        """没有 evaluator_model 时必须降级到文本启发式，而不是抛错。"""

        goals.set_goal(self.host, "objective")
        evidence = goals.build_goal_evidence(self.host, {"final_output": "done"})
        verdict = goals.run_goal_evaluator(self.host, evidence)
        # 降级判定绝不能把目标判成已完成：那会让 submit_output 绕过 evaluator。
        self.assertFalse(verdict["completed"])
        self.assertTrue(verdict["should_continue"])
        self.assertIn("unavailable", verdict["reason"])


class GoalsSignatureContractTests(unittest.TestCase):
    def test_no_goal_function_signature_falls_back_to_runner_any(self) -> None:
        """守住去 stamp-coupling 成果：函数签名不得回退成 `runner: Any`。

        只检查真实签名（AST 形参注解），不做全文匹配 —— 否则文档里解释历史问题的
        散文也会误伤。
        """

        import ast

        source = (
            Path(__file__).resolve().parents[3]
            / "sdk" / "src" / "juice_agents" / "core" / "runner" / "goals.py"
        ).read_text(encoding="utf-8")
        offenders: list[str] = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                if arg.arg != "runner" or arg.annotation is None:
                    continue
                if ast.unparse(arg.annotation).strip() == "Any":
                    offenders.append(node.name)
        self.assertEqual([], offenders, f"这些函数的 runner 参数退回了 Any: {offenders}")


if __name__ == "__main__":
    unittest.main()
