import base64
import os
import unittest

from juice_agents.core.agent.local_python_executor import LocalPythonExecutor
from juice_agents.core.agent.tools.builtin.images.attachments_tools import AddImageTool
from juice_agents.core.agent.tools.runtime.base_tools import Tool

SAMPLE_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5W9r8AAAAASUVORK5CYII="
)


class LocalPythonExecutorTests(unittest.TestCase):
    def setUp(self):
        self.executor = LocalPythonExecutor(additional_authorized_imports=["math"])

        class DoubleTool(Tool):
            name = "double"
            description = "x2"

            def forward(self, x):
                return x * 2

        self.executor.send_tools({"double": DoubleTool(), "add_image": AddImageTool()})

    def test_execute_code_and_logs(self):
        output = self.executor("val = double(21)\nprint('hello')\n_result = val")
        self.assertIsNone(output.error)
        self.assertFalse(output.submitted)
        self.assertEqual(output.output, 42)
        self.assertIn("hello", output.logs)

    def test_large_logs_remain_canonical_for_codeact_projection_layer(self):
        output = self.executor("print('x' * 60000)")

        self.assertIsNone(output.error)
        self.assertEqual(output.logs, "x" * 60_000 + "\n")
        self.assertNotIn("truncated", output.logs)

    def test_submit_output_and_images(self):
        output = self.executor(f"add_image({SAMPLE_PNG_BYTES!r})\nsubmit_output(output='done')")
        self.assertTrue(output.submitted)
        self.assertEqual(output.output, "done")
        self.assertEqual(len(output.observation_images), 1)
        img0 = output.observation_images[0]
        image_url = str(getattr(img0, "image_url", ""))
        self.assertFalse(image_url.startswith("data:image"))
        self.assertTrue(image_url.startswith("http") or os.path.exists(image_url))
        self.assertTrue(img0.to_bytes())

    def test_import_guard(self):
        # 未授权的导入应当报错
        res = self.executor("import os")
        self.assertIsNotNone(res.error)
        self.assertIn("not allowed", res.error)

    def test_protect_static_tool_overwrite(self):
        res = self.executor("print = 1")
        self.assertIsNotNone(res.error)
        self.assertIn("Cannot assign to name 'print'", res.error)

    def test_allow_authorized_import(self):
        res = self.executor("import math\n_result = math.sqrt(16)")
        self.assertIsNone(res.error)
        self.assertEqual(res.output, 4.0)

    def test_allow_all_imports_still_blocks_blacklisted_import(self):
        executor = LocalPythonExecutor(
            additional_authorized_imports=[],
            allow_all_imports=True,
            blocked_imports=["ctypes"],
        )
        res = executor("import os\n_result = os.path.basename('/tmp/demo.txt')")
        self.assertIsNone(res.error)
        self.assertEqual(res.output, "demo.txt")

        blocked = executor("import ctypes")
        self.assertIsNotNone(blocked.error)
        self.assertIn("blocked", blocked.error)

    def test_blocked_python_call_in_allow_all_mode(self):
        executor = LocalPythonExecutor(
            additional_authorized_imports=[],
            allow_all_imports=True,
            blocked_call_prefixes=["os.system"],
        )
        res = executor("import os\nos.system('echo hi')")
        self.assertIsNotNone(res.error)
        self.assertIn("Blocked Python call", res.error)

    def test_subprocess_shell_string_reuses_shell_blacklist(self):
        executor = LocalPythonExecutor(
            additional_authorized_imports=[],
            allow_all_imports=True,
            blocked_shell_patterns=[(r"(^|\s)sudo(\s|$)", "禁止提权命令 sudo")],
        )
        res = executor("import subprocess\nsubprocess.run('sudo ls', shell=True)")
        self.assertIsNotNone(res.error)
        self.assertIn("禁止提权命令 sudo", res.error)


if __name__ == "__main__":
    unittest.main()
