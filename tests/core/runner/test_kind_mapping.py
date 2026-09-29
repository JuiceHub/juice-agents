"""Async task kind 映射的单一真相契约。

这张表此前散落 6 份副本（launchers/store/executor/registry/actors 各一份，
其中两份还在函数内每次调用重建），任何一处漏改都会让某个 kind 静默退化成
`local_agent`。这些测试锁住"只有一张表，且两个方向自洽"。
"""

from __future__ import annotations

import unittest

from juice_agents.core.runner.async_tasks.kinds import (
    ASYNC_TASK_KIND_TO_PUBLIC,
    ASYNC_TASK_KINDS,
    PUBLIC_TO_ASYNC_TASK_KIND,
    to_internal_kind,
    to_public_kind,
)


class KindMappingTests(unittest.TestCase):
    def test_forward_and_reverse_maps_are_mutually_consistent(self) -> None:
        """反向表必须由正向表推导，不允许两个方向各写一份而漂移。"""

        self.assertEqual(len(ASYNC_TASK_KIND_TO_PUBLIC), len(PUBLIC_TO_ASYNC_TASK_KIND))
        for internal, public in ASYNC_TASK_KIND_TO_PUBLIC.items():
            self.assertEqual(PUBLIC_TO_ASYNC_TASK_KIND[public], internal)

    def test_round_trip_is_identity_for_every_kind(self) -> None:
        for internal in ASYNC_TASK_KIND_TO_PUBLIC:
            self.assertEqual(to_internal_kind(to_public_kind(internal)), internal)
        for public in PUBLIC_TO_ASYNC_TASK_KIND:
            self.assertEqual(to_public_kind(to_internal_kind(public)), public)

    def test_kinds_set_matches_forward_map_keys(self) -> None:
        self.assertEqual(ASYNC_TASK_KINDS, frozenset(ASYNC_TASK_KIND_TO_PUBLIC))

    def test_unknown_values_fall_back_without_raising(self) -> None:
        # 兜底行为是历史语义：无法识别时退化成 agent 派发，而不是抛错。
        self.assertEqual(to_public_kind("bogus"), "local_agent")
        self.assertEqual(to_public_kind(""), "local_agent")
        self.assertEqual(to_public_kind(None), "local_agent")
        self.assertEqual(to_internal_kind("bogus"), "agent_dispatch")
        self.assertEqual(to_internal_kind(None), "agent_dispatch")

    def test_to_internal_kind_passes_through_internal_names(self) -> None:
        """混合来源的输入（可能已是内部名）要能安全归一化。"""

        for internal in ASYNC_TASK_KIND_TO_PUBLIC:
            self.assertEqual(to_internal_kind(internal), internal)

    def test_whitespace_is_tolerated(self) -> None:
        self.assertEqual(to_public_kind("  agent_dispatch  "), "local_agent")
        self.assertEqual(to_internal_kind("  local_bash  "), "shell_command")

    def test_no_module_keeps_a_second_private_copy_of_the_table(self) -> None:
        """守住去重成果：除 kinds.py 外，runner 包内不得再出现这对映射字面量。"""

        import ast
        from pathlib import Path

        runner_dir = (
            Path(__file__).resolve().parents[3]
            / "sdk" / "src" / "juice_agents" / "core" / "runner"
        )
        self.assertTrue(runner_dir.is_dir(), f"runner 目录不存在: {runner_dir}")

        # 判据是「真的存在一个 dict 字面量把某个内部 kind 映到它对应的公开名」，
        # 而不是 grep 两个字符串是否同时出现在文件里 —— 后者会被注释和 docstring
        # 里的同名字符串误伤（本仓已有这类测试债）。
        both_directions = dict(ASYNC_TASK_KIND_TO_PUBLIC)
        both_directions.update(PUBLIC_TO_ASYNC_TASK_KIND)

        offenders: list[str] = []
        for path in sorted(runner_dir.rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "kinds.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values):
                    if not isinstance(key, ast.Constant) or not isinstance(value, ast.Constant):
                        continue
                    # 必须显式要求 key 在映射表里：`.get()` 对不存在的 key 返回 None，
                    # 会让任意 `{"foo": None}` 字面量假匹配。
                    if key.value not in both_directions:
                        continue
                    if both_directions[key.value] == value.value:
                        offenders.append(
                            f"{path.relative_to(runner_dir)}:{node.lineno} "
                            f"{key.value!r} -> {value.value!r}"
                        )
        self.assertEqual(
            [],
            offenders,
            "以下位置又抄了一份 kind 映射，应改用 kinds.py: " + "; ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
