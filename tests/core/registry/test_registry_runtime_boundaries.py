"""Registry 封装边界：ConfigStore 只能由 registry 包内部直接使用。

外部模块必须经由 ``AgentRegistry`` / ``ToolRegistry`` 门面访问配置；
绕过门面直接拿 Store 会跳过校验与运行时装配。
"""

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[3]

# registry 包自身就是门面实现所在处，允许直接依赖 Store。
_REGISTRY_PACKAGE = PROJECT_ROOT / "sdk" / "src" / "juice_agents" / "core" / "registry"

# 受约束的生产代码根目录。测试不在其中：测试需要直接构造 Store 来准备夹具。
_GUARDED_ROOTS = (
    PROJECT_ROOT / "sdk" / "src" / "juice_agents",
    PROJECT_ROOT / "adapters",
)

_STORE_IMPORTS = (
    "from juice_agents.core.registry.agents.store import AgentConfigStore",
    "from juice_agents.core.registry.tools.store import ToolConfigStore",
)


class RegistryRuntimeBoundaryTests(unittest.TestCase):
    def test_runtime_modules_do_not_import_registry_stores_directly(self):
        scanned = 0
        violations: list[str] = []
        for guarded_root in _GUARDED_ROOTS:
            self.assertTrue(guarded_root.is_dir(), f"受约束目录不存在: {guarded_root}")
            for path in sorted(guarded_root.rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                if path.is_relative_to(_REGISTRY_PACKAGE):
                    continue
                scanned += 1
                text = path.read_text(encoding="utf-8")
                for needle in _STORE_IMPORTS:
                    if needle in text:
                        violations.append(str(path.relative_to(PROJECT_ROOT)))

        # 断言确实扫到了文件：目录结构调整后这里曾静默退化为空循环恒真。
        self.assertGreater(scanned, 0, "未扫描到任何受约束模块，边界断言已失效")
        self.assertEqual(
            violations,
            [],
            "registry 外部模块不应直接 import AgentConfigStore/ToolConfigStore: "
            + ", ".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
