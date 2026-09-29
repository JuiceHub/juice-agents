"""Terminal-output tool contracts."""

import unittest

from juice_agents.core.agent.tools.builtin.output.completion_tools import SubmitOutputTool


class TestSubmitOutputTool(unittest.TestCase):
    """SubmitOutputTool must preserve the submitted value without coercion."""

    def test_forward_returns_input(self):
        tool = SubmitOutputTool()

        self.assertEqual(tool.forward("output"), "output")
        self.assertEqual(tool.forward(123), 123)
        self.assertEqual(tool.forward({"key": "value"}), {"key": "value"})


if __name__ == "__main__":
    unittest.main()
