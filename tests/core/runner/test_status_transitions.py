"""Runner 状态迁移的四字段原子性契约。

`status` / `status_reason` / `status_changed_at` / `active_round` 必须一起变更：
只改 status 会让审计时间线错乱，漏清 active_round 会让已结束的回合看起来仍在运行。
此前 6 处调用点各自手写这四行赋值，漏写不会被类型检查发现。
"""

from __future__ import annotations

import unittest

from juice_agents.core.runner.status import settle_async_pending_status, settle_status


def _stale_state() -> dict:
    """构造一个"上一回合还在运行"的脏状态，用于验证字段确实被覆盖。"""

    return {
        "status": "running",
        "status_reason": "round_started",
        "status_changed_at": 1.0,
        "active_round": {"round_id": "old", "actor_name": "root", "started_at": 1.0},
    }


class SettleStatusTests(unittest.TestCase):
    def test_writes_all_four_fields_and_clears_active_round(self) -> None:
        state = _stale_state()
        settle_status(state, status="idle", reason="round_ended")
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["status_reason"], "round_ended")
        self.assertGreater(state["status_changed_at"], 1.0)
        # 关键不变量：结束回合必须把 active_round 清空，否则前端会一直显示运行中。
        self.assertIsNone(state["active_round"])

    def test_explicit_active_round_is_copied_not_aliased(self) -> None:
        state: dict = {}
        incoming = {"round_id": "r1", "actor_name": "root", "started_at": 5.0}
        settle_status(state, status="running", reason="round_started", active_round=incoming)
        self.assertEqual(state["active_round"], incoming)
        # 存的是副本：调用方之后改自己的 dict 不能反向污染 runner 状态。
        incoming["round_id"] = "mutated"
        self.assertEqual(state["active_round"]["round_id"], "r1")

    def test_at_parameter_pins_the_timestamp(self) -> None:
        """回合开始时同一个时间戳要同时进 status_changed_at 与 active_round。"""

        state: dict = {}
        started = 1234.5
        settle_status(
            state,
            status="running",
            reason="round_started",
            active_round={"round_id": "r1", "started_at": started},
            at=started,
        )
        self.assertEqual(state["status_changed_at"], started)
        self.assertEqual(state["active_round"]["started_at"], started)

    def test_reason_is_stripped(self) -> None:
        state: dict = {}
        settle_status(state, status="idle", reason="  resumed  ")
        self.assertEqual(state["status_reason"], "resumed")

    def test_non_dict_active_round_normalizes_to_none(self) -> None:
        state = _stale_state()
        settle_status(state, status="idle", reason="round_ended", active_round="garbage")  # type: ignore[arg-type]
        self.assertIsNone(state["active_round"])


class SettleAsyncPendingStatusTests(unittest.TestCase):
    def test_waiting_when_async_work_outstanding(self) -> None:
        state = _stale_state()
        settle_async_pending_status(state, has_async=True)
        self.assertEqual(state["status"], "waiting")
        self.assertEqual(state["status_reason"], "async_pending")
        self.assertIsNone(state["active_round"])

    def test_idle_when_nothing_outstanding(self) -> None:
        state = _stale_state()
        settle_async_pending_status(state, has_async=False)
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["status_reason"], "round_ended")

    def test_reasons_stay_parameterizable(self) -> None:
        """resume 用 pending_notification/resumed，是对外可见的审计措辞，不能被抹平。"""

        state: dict = {}
        settle_async_pending_status(
            state,
            has_async=True,
            waiting_reason="pending_notification",
            idle_reason="resumed",
        )
        self.assertEqual(state["status_reason"], "pending_notification")

        settle_async_pending_status(
            state,
            has_async=False,
            waiting_reason="pending_notification",
            idle_reason="resumed",
        )
        self.assertEqual(state["status_reason"], "resumed")


class StatusPersistenceContractTests(unittest.TestCase):
    def test_write_runner_state_rejects_illegal_status(self) -> None:
        """settle_status 写出的 status 必须能通过落盘校验，非法值要被拒。"""

        from juice_agents.core.runner.persistence.store import write_runner_state

        with self.assertRaises(ValueError):
            write_runner_state("/dev/null", {"status": "bogus_status"})  # type: ignore[arg-type]

    def test_runner_package_no_longer_hand_writes_the_tuple(self) -> None:
        """守住去重成果：状态迁移只能经 settle_status，不得手写字段赋值。

        用 AST 判断"真的有一条赋值语句写了这些 key"，而不是 grep 源码文本：
        文本匹配会被注释和 docstring 里的同名字符串误伤（本仓已有这类测试债，
        本次重构中我自己写的 goals 守卫就被自己的 docstring 绊过一次）。
        """

        import ast
        from pathlib import Path

        runner_dir = (
            Path(__file__).resolve().parents[3]
            / "sdk" / "src" / "juice_agents" / "core" / "runner"
        )
        self.assertTrue(runner_dir.is_dir(), f"runner 目录不存在: {runner_dir}")

        guarded_keys = {"status_reason", "status_changed_at"}
        offenders: list[str] = []
        for path in sorted(runner_dir.rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "status.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not isinstance(target, ast.Subscript):
                        continue
                    key = target.slice
                    if isinstance(key, ast.Constant) and key.value in guarded_keys:
                        offenders.append(
                            f"{path.relative_to(runner_dir)}:{node.lineno} 写了 [{key.value!r}]"
                        )
        self.assertEqual(
            [],
            offenders,
            "这些位置绕过了 settle_status 手写状态字段: " + "; ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
