"""Local workflow 源码的写入工具。

只读发现与执行在 `builtin/workflows/workflows_tools.py`；本模块继承那里的
`WorkflowToolBase` 以复用同一条 config_context / registry 解析链。

动词表只有 `create|edit`：Workflow 不进入 Agent 能力集，没有绑定态可解，
所以既没有 load/unload，也不提供物理删除 —— 删除 workflow 一律走通用文件或
Shell 工具，与 Agent/Tool/Skill 的「manage 不删文件」约定保持一致。
"""

from __future__ import annotations

import logging
from typing import Any

from ..workflows.workflows_tools import WorkflowToolBase

logger = logging.getLogger(__name__)

# 工具说明即模型写脚本时唯一能看到的 API 文档，所以把最小模板与约束写全。
_DESCRIPTION = """创建或更新 .juice/workflows/<name>.py；edit 可编辑已有内置 workflow，校验成功后保存同名本地覆盖。
查看和运行内置 workflow 不复制文件；删除本地覆盖后恢复内置定义。删除请使用通用文件工具。

脚本模板：
WORKFLOW_METADATA = {
    "name": "<name>", "description": "...",
    "read_only": True,                 # True 时内部 agent 只能用只读工具，启动免审批
    "allowed_modes": ["agent"],        # 可省略 = 所有 mode
    "tools": ["read", "grep"],         # 内部 agent 可用工具上限
    "phases": ["Scan", "Report"],      # 进度显示
    "input_schema": {...},
}
def run(ctx, args):                    # 必须是同步函数
    out = ctx.agent(task, instructions="...", tools=["read"], schema={...}, phase="Scan", label="scan")
    many = ctx.parallel([lambda x=x: ctx.agent(x, instructions="...") for x in items])
    ctx.artifact("report.md", text)
    return {...}

约束：编排必须确定性（不用随机数/当前时间拼 prompt，否则续跑缓存失效）；
脚本本身不读写文件/网络，副作用全部交给 ctx.agent；内部 agent 以 accept 运行，
禁止使用 workflow_*/agent_tool/team_*/ask/plan 及自进化工具。"""


class WorkflowManageTool(WorkflowToolBase):
    name = "workflow_manage"
    # 与同一 step 内的其他动作串行，避免并发写 .juice/workflows。
    description = _DESCRIPTION
    inputs = {
        "action": {"type": "string", "description": "create/edit"},
        "name": {"type": "string", "description": "workflow 名称"},
        "content": {"type": "string", "description": "完整 Python 单文件源码", "required": False},
        "overwrite": {"type": "boolean", "description": "create 时是否覆盖", "required": False},
    }
    outputs = {"success": {"type": "boolean", "description": "是否成功"}}

    def forward(
        self,
        action: str,
        name: str,
        content: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        result = self._registry().manage(action, name=name, content=content, overwrite=overwrite)
        logger.info(
            "workflow_manage 完成: action=%s name=%s success=%s",
            str(action or "").strip().lower(),
            name,
            result.get("success"),
        )
        return result


__all__ = ["WorkflowManageTool"]
