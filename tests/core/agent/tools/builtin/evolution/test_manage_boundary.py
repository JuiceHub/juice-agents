"""evolution/ 目录的边界契约。

各域的 list/view/manage 端到端行为分别由 `builtin/{agents,tools,skills,plugins}/`
下的测试覆盖；本文件只锁一件事：**目录边界 == 权限边界 == 语义边界**。

新增写工具时若忘了登记到 `SELF_EVOLUTION_TOOL_NAMES`，或忘了声明
`BARRIER + main` 执行策略，这里立刻失败。
"""

from __future__ import annotations

from pathlib import Path
import unittest

from juice_agents.core.agent.tools import AGENT_TOOLS, GRAPH_TOOLS, SKILL_TOOLS, TOOL_TOOLS
from juice_agents.core.agent.tools.builtin import evolution as _evolution_pkg
from juice_agents.core.agent.tools.builtin.evolution.agent_manage import AgentManageTool
from juice_agents.core.agent.tools.builtin.evolution.constants import SELF_EVOLUTION_TOOL_NAMES
from juice_agents.core.agent.tools.builtin.evolution.graph_manage import GraphManageTool
from juice_agents.core.agent.tools.builtin.evolution.skill_manage import SkillManageTool
from juice_agents.core.agent.tools.builtin.evolution.tool_manage import ToolManageTool

# 从包自身推导目录，避免依赖 cwd（conftest 会在 session 级切换它）。
_EVOLUTION_DIR = Path(_evolution_pkg.__file__).parent
_MANAGE_TOOLS = (AgentManageTool, ToolManageTool, SkillManageTool, GraphManageTool)


class ManageBoundaryTests(unittest.TestCase):
    def test_evolution_package_exposes_exactly_the_self_evolution_scope(self) -> None:
        """目录里的模块集合必须与硬开关作用域逐字相等。"""

        modules = {
            path.stem for path in _EVOLUTION_DIR.glob("*.py")
        } - {"__init__", "constants"}
        self.assertEqual(modules, {f"{name.removesuffix('_manage')}_manage" for name in SELF_EVOLUTION_TOOL_NAMES})
        self.assertEqual({tool.name for tool in _MANAGE_TOOLS}, set(SELF_EVOLUTION_TOOL_NAMES))

    def test_every_manage_tool_is_a_main_thread_barrier(self) -> None:
        """并发写 .juice 会互相覆盖，四个 manage 必须成为主线程屏障。"""

        for tool in _MANAGE_TOOLS:
            with self.subTest(tool=tool.name):
                policy = tool().execution_policy({}, None)
                self.assertEqual(policy.mode.value, "barrier")
                self.assertEqual(policy.thread_affinity, "main")

    def test_no_manage_tool_claims_read_only(self) -> None:
        for tool in _MANAGE_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertFalse(getattr(tool, "is_read_only", False))

    def test_list_and_view_stay_outside_the_evolution_scope(self) -> None:
        """只读工具受领域能力开关约束，不受 self_evolution 约束。"""

        for bundle in (AGENT_TOOLS, TOOL_TOOLS, SKILL_TOOLS, GRAPH_TOOLS):
            for tool in bundle:
                if tool.name.endswith("_manage"):
                    continue
                with self.subTest(tool=tool.name):
                    self.assertNotIn(tool.name, SELF_EVOLUTION_TOOL_NAMES)

    def test_graph_manage_rejects_delete_like_the_other_three_domains(self) -> None:
        """manage 只写不删；物理删除一律走通用文件/Shell 工具。"""

        self.assertEqual(GraphManageTool.inputs["action"]["description"], "create/edit")


if __name__ == "__main__":
    unittest.main()
