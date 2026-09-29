"""Tools for managing workspace-local cron scheduled prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from juice_agents.core.cron import create_cron_task, delete_cron_task, list_cron_tasks

from ...runtime.base_tools import LIST_OBSERVATION_CHARS, Tool


class _CronToolBase(Tool):
    """Resolve the workspace used by cron tools."""

    def __init__(self, workspace_dir: str | Path | None = None) -> None:
        super().__init__()
        self.workspace_dir = None if workspace_dir is None else Path(workspace_dir).expanduser().resolve()
        self.owner_agent: Any | None = None

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _workspace_dir(self) -> Path:
        if self.workspace_dir is not None:
            return self.workspace_dir
        if self.owner_agent is not None:
            workspace = getattr(self.owner_agent, "_runtime_workspace", None)
            default_base_dir = getattr(self.owner_agent, "_runtime_base_dir", None)
            if workspace is not None and getattr(workspace, "workspace_dir", None) is not None:
                return Path(workspace.workspace_dir).expanduser().resolve()
            if default_base_dir is not None:
                return Path(default_base_dir).expanduser().resolve()
        return Path.cwd().resolve()


class CronCreateTool(_CronToolBase):
    """Create a recurring or one-shot scheduled prompt."""

    name = "cron_create"
    description = "创建 workspace-local cron 定时 prompt；当前进程存活且空闲时触发"
    inputs = {
        "cron": {"type": "string", "description": "5 字段 cron 表达式，例如 */5 * * * *"},
        "prompt": {"type": "string", "description": "到点后发送给 root agent 的 prompt"},
        "recurring": {"type": "boolean", "description": "是否循环触发，默认 true", "required": False},
    }
    outputs = {
        "id": {"type": "string", "description": "任务 id"},
        "cron": {"type": "string", "description": "cron 表达式"},
        "prompt": {"type": "string", "description": "触发 prompt"},
        "next_run_at": {"type": "number", "description": "下一次触发 Unix 时间戳"},
    }

    def forward(self, cron: str, prompt: str, recurring: bool = True) -> dict[str, Any]:
        return create_cron_task(self._workspace_dir(), cron=cron, prompt=prompt, recurring=recurring)


class CronListTool(_CronToolBase):
    _execution_mode = "parallel_safe"
    """List scheduled prompts."""

    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "cron_list"
    is_read_only = True
    description = "列出 workspace-local cron 定时 prompt"
    inputs: dict[str, dict[str, Any]] = {}
    outputs = {
        "tasks": {"type": "list", "description": "任务列表"},
    }

    def forward(self) -> dict[str, Any]:
        return {"tasks": list_cron_tasks(self._workspace_dir())}


class CronDeleteTool(_CronToolBase):
    """Delete a scheduled prompt."""

    name = "cron_delete"
    description = "删除 workspace-local cron 定时 prompt"
    inputs = {
        "id": {"type": "string", "description": "cron_create 返回的任务 id"},
    }
    outputs = {
        "deleted": {"type": "boolean", "description": "是否删除了任务"},
        "id": {"type": "string", "description": "任务 id"},
    }

    def forward(self, id: str) -> dict[str, Any]:
        return delete_cron_task(self._workspace_dir(), id)


CRON_TOOLS = [CronCreateTool, CronListTool, CronDeleteTool]

__all__ = ["CRON_TOOLS", "CronCreateTool", "CronDeleteTool", "CronListTool"]
