"""测试隔离契约：Runner 测试必须显式使用临时 base_dir。"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


TEST_ROOT = Path(__file__).resolve().parents[2]


class RunnerTestIsolationContractTests(unittest.TestCase):
    def test_runner_create_calls_in_python_tests_pass_explicit_base_dir(self) -> None:
        """避免测试默认把 Runner 持久化状态写到仓库当前工作目录的 `.juice/`。"""

        offenders: list[str] = []
        for path in sorted(TEST_ROOT.rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "create"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "Runner"
                ):
                    continue
                if not any(keyword.arg == "base_dir" for keyword in node.keywords):
                    offenders.append(f"{path.relative_to(TEST_ROOT)}:{node.lineno}")

        self.assertEqual(
            offenders,
            [],
            "Runner.create(...) in tests must pass base_dir from TemporaryDirectory: "
            + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
