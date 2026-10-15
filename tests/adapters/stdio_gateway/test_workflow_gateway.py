import tempfile
import unittest
from pathlib import Path

from adapters.stdio_gateway.handlers import RpcHandlers
from adapters.stdio_gateway.runtime import DirectRunnerRuntime


SOURCE = '''
WORKFLOW_METADATA = {"name": "counter", "description": "counter", "read_only": True}

def run(ctx, args):
    """Simple counter workflow for testing."""
    value = args.get("value", 0)
    return {"value": value + 1}
'''


class WorkflowGatewayTests(unittest.TestCase):
    def test_runtime_lists_and_executes_workflows(self):
        """Verify workflow listing, execution, and permission status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / ".juice" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "counter.py").write_text(SOURCE, encoding="utf-8")
            runtime = DirectRunnerRuntime(base_dir=tmpdir)

            # 验证列表包含已注册的 workflow
            self.assertIn("counter", [item["name"] for item in runtime.list_workflows()["workflows"]])

            # 执行 workflow 并验证结果
            response = runtime.run_workflow("counter", {"value": 1})
            self.assertEqual(response["status"], "completed")
            self.assertEqual(response["result"]["value"], 2)

            # 验证权限状态
            self.assertEqual(runtime.permission_status()["permission_mode"], "default")

    def test_plan_mode_rejects_workflow_execution(self):
        """Verify plan mode blocks workflow execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = DirectRunnerRuntime(base_dir=tmpdir, permission_mode="default", agent_mode="plan")
            status = runtime.permission_status()
            # plan 属执行模式维度；权限维度保持 default，只读性由 agent_mode 决定。
            self.assertEqual(status["permission_mode"], "default")
            self.assertEqual(status["agent_mode"], "plan")
            self.assertTrue(status["read_only"])
            with self.assertRaises(PermissionError):
                runtime.run_workflow("deep_research", {"question": "test"})

    def test_rpc_handlers_expose_workflow_and_permission_reads(self):
        """Verify RPC handlers provide workflow listing and permission status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            handlers = RpcHandlers()
            workflows = handlers.handle_list_workflows({"base_dir": tmpdir})
            permissions = handlers.handle_permission_status({"base_dir": tmpdir})
            self.assertIn("deep_research", [item["name"] for item in workflows["workflows"]])
            self.assertEqual(permissions["permission_mode"], "default")


if __name__ == "__main__":
    unittest.main()
