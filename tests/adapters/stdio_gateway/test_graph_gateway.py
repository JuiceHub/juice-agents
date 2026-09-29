import tempfile
import unittest
from pathlib import Path

from adapters.stdio_gateway.handlers import RpcHandlers
from adapters.stdio_gateway.runtime import DirectRunnerRuntime


SOURCE = '''
from typing import TypedDict
from juice_agents.core.graph import END, StateGraph
GRAPH_METADATA = {"name": "counter", "description": "counter", "read_only": True}
class State(TypedDict):
    value: int
def build_graph(context):
    graph = StateGraph(State)
    graph.add_node("increment", lambda state: {"value": state["value"] + 1})
    graph.set_entry_point("increment")
    graph.add_edge("increment", END)
    return graph.compile()
'''


class GraphGatewayTests(unittest.TestCase):
    def test_runtime_lists_runs_and_reports_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_dir = Path(tmpdir) / ".juice" / "graphs"
            graph_dir.mkdir(parents=True)
            (graph_dir / "counter.py").write_text(SOURCE, encoding="utf-8")
            runtime = DirectRunnerRuntime(base_dir=tmpdir)

            self.assertIn("counter", [item["name"] for item in runtime.list_graphs()["graphs"]])
            run = runtime.run_graph("counter", {"value": 1})
            self.assertEqual(run["result"]["value"], 2)
            self.assertEqual(runtime.list_graph_runs()["count"], 1)
            self.assertEqual(runtime.permission_status()["permission_mode"], "default")

    def test_plan_mode_rejects_graph_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = DirectRunnerRuntime(base_dir=tmpdir, permission_mode="default", agent_mode="plan")
            status = runtime.permission_status()
            # plan 属执行模式维度；权限维度保持 default，只读性由 agent_mode 决定。
            self.assertEqual(status["permission_mode"], "default")
            self.assertEqual(status["agent_mode"], "plan")
            self.assertTrue(status["read_only"])
            with self.assertRaises(PermissionError):
                runtime.run_graph("deep_research", {"question": "test"})

    def test_rpc_handlers_expose_graph_and_permission_reads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            handlers = RpcHandlers()
            graphs = handlers.handle_list_graphs({"base_dir": tmpdir})
            permissions = handlers.handle_permission_status({"base_dir": tmpdir})
            self.assertIn("deep_research", [item["name"] for item in graphs["graphs"]])
            self.assertEqual(permissions["permission_mode"], "default")


if __name__ == "__main__":
    unittest.main()
