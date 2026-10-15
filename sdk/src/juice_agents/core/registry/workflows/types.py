"""Workflow registry public types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

WorkflowSource = Literal["builtin", "local"]


@dataclass(frozen=True, slots=True)
class WorkflowMetadata:
    """从 ``WORKFLOW_METADATA`` 字面量解析出的声明，发现阶段不执行脚本代码。

    - read_only: 为 True 时启动免审批，且内部 agent 只能使用只读工具
    - allowed_modes: 可运行的 Runner mode；None 表示所有 mode（与 AgentConfig 一致）
    - tools: 内部 agent 可用工具的上限；``ctx.agent(tools=...)`` 必须是它的子集
    - phases: 进度展示用的阶段名，仅用于显示
    """

    name: str
    description: str
    source: WorkflowSource
    path: Path | None
    read_only: bool = False
    allowed_modes: tuple[str, ...] | None = None
    tools: tuple[str, ...] = ()
    phases: tuple[str, ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    source_hash: str = ""

    def allows_mode(self, mode_id: str) -> bool:
        return self.allowed_modes is None or str(mode_id or "").strip() in self.allowed_modes

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "path": "" if self.path is None else str(self.path),
            "read_only": self.read_only,
            "allowed_modes": None if self.allowed_modes is None else list(self.allowed_modes),
            "tools": list(self.tools),
            "phases": list(self.phases),
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "source_hash": self.source_hash,
        }


__all__ = ["WorkflowMetadata", "WorkflowSource"]
