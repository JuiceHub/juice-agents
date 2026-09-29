"""Terminal response tools used by Agent protocols."""

from __future__ import annotations

from typing import Any

from ...runtime.base_tools import Tool


class SubmitOutputTool(Tool):
    """Return the final business result and mark the current agent turn complete."""

    name = "submit_output"
    description = "提交最终业务结果并结束当前 agent 回合"
    is_terminal = True
    is_read_only = True
    inputs = {"output": {"type": "any", "description": "最终业务结果"}}
    outputs = {"output": {"type": "any", "description": "最终业务结果"}}

    def forward(self, output: Any) -> Any:
        """Keep the submitted value unchanged for the caller's terminal result."""

        return output


__all__ = ["SubmitOutputTool"]
