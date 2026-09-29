"""Workspace 绑定逻辑（纯函数）。

将 workspace 约束注入到 agent config，实现文件工具的 root_dir 限制、
shell 工具的 default_workdir、以及 todos/plan 工具的路径绑定。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.registry.tools.types import ToolRef
from juice_agents.core.runner.workspace import RuntimeWorkspace

FILE_TOOL_NAMES = {
    "read",
    "write",
    "edit",
    "glob",
    "grep",
}


def bind_workspace_to_agent_config(
    agent_config: dict[str, Any] | AgentConfig,
    *,
    runtime_workspace: RuntimeWorkspace,
    default_plan_base_dir: str | Path,
) -> dict[str, Any]:
    """
    将 workspace 约束注入到 agent config（纯函数）。

    规则：
    - 文件工具补 `root_dir`
    - shell 工具补 `default_workdir`
    - `todos` / `plan` 落到 workspace 下的 `.juice/todos|plans`
    """

    normalized = AgentConfig.from_dict(agent_config).to_dict()

    for key in ("system_prompt", "log_file_path"):
        if normalized.get(key) is None:
            normalized.pop(key, None)
    if not normalized.get("additional_authorized_imports"):
        normalized.pop("additional_authorized_imports", None)

    if not runtime_workspace.enabled:
        return normalized

    agent_name = str(normalized.get("name") or "").strip()
    rebound_tools: list[dict[str, Any]] = []
    for idx, raw_tool in enumerate(list(normalized.get("tools") or [])):
        tool_ref = ToolRef.from_raw(raw_tool, field_name=f"tools[{idx}]")
        params = dict(tool_ref.params)
        if tool_ref.name in FILE_TOOL_NAMES:
            params["root_dir"] = str(runtime_workspace.workspace_dir)
        if tool_ref.name == "shell":
            params["default_workdir"] = str(runtime_workspace.workspace_dir)
        if tool_ref.name == "todos" and agent_name:
            params["file_path"] = str(
                runtime_workspace.todos_file(
                    agent_name,
                    default_base_dir=default_plan_base_dir,
                )
            )
        if tool_ref.name == "plan" and agent_name:
            params["file_path"] = str(
                runtime_workspace.plan_file(
                    agent_name,
                    default_base_dir=default_plan_base_dir,
                )
            )
        rebound_tools.append({"name": tool_ref.name, "params": params})

    normalized["tools"] = rebound_tools
    return normalized


__all__ = [
    "FILE_TOOL_NAMES",
    "bind_workspace_to_agent_config",
]
