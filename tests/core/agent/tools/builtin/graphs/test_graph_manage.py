"""graph_manage 的写入契约：只写不删。

与 agent/tool/skill 三域的 `test_delete_is_not_a_manage_action` 对称 —— Graph
没有绑定态可解，所以没有 load/unload，但同样不提供物理删除。
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from juice_agents.core.agent.tools.builtin.evolution.graph_manage import GraphManageTool
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.graphs import GraphRegistry

SOURCE = '''
from typing import TypedDict
from juice_agents.core.graph import END, StateGraph

GRAPH_METADATA = {"name": "counter", "description": "counter", "read_only": True}

class State(TypedDict):
    value: int

def build_graph(context):
    graph = StateGraph(State)
    graph.add_node("increment", lambda state: {"value": state.get("value", 0) + 1})
    graph.set_entry_point("increment")
    graph.add_edge("increment", END)
    return graph.compile()
'''


class GraphManageTests(unittest.TestCase):
    def _tool(self, workspace: Path) -> GraphManageTool:
        """构造 workspace-scoped 工具，避免落盘到仓库根。"""

        registry = GraphRegistry(config_context=ConfigurationContext.from_workspace(workspace))
        return GraphManageTool(graph_registry=registry)

    def test_create_then_edit_writes_local_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tool = self._tool(workspace)

            created = tool.forward("create", "counter", content=SOURCE)
            self.assertTrue(created["success"], created)
            path = workspace / ".juice" / "graphs" / "counter.py"
            self.assertTrue(path.is_file())

            edited = tool.forward("edit", "counter", content=SOURCE.replace("+ 1", "+ 2"))
            self.assertTrue(edited["success"], edited)
            self.assertIn("+ 2", path.read_text(encoding="utf-8"))

    def test_delete_is_not_a_manage_action(self) -> None:
        """物理删除必须交给通用文件/Shell 工具，manage 拒绝该动词。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tool = self._tool(workspace)
            self.assertTrue(tool.forward("create", "counter", content=SOURCE)["success"])
            path = workspace / ".juice" / "graphs" / "counter.py"

            result = tool.forward("delete", "counter")
            self.assertFalse(result["success"])
            self.assertIn("create/edit", result["error"])
            # 关键：文件必须仍在。
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
