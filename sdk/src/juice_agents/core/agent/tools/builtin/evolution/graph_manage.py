"""Local graph 源码的写入工具。

只读发现与执行在 `builtin/graphs/graphs_tools.py`；本模块继承那里的
`GraphToolBase` 以复用同一条 config_context / registry 解析链。

动词表只有 `create|edit`：Graph 不进入 Agent 能力集，没有绑定态可解，
所以既没有 load/unload，也不提供物理删除 —— 删除 graph 一律走通用文件或
Shell 工具，与 Agent/Tool/Skill 的「manage 不删文件」约定保持一致。
"""

from __future__ import annotations

import logging
from typing import Any

from ..graphs.graphs_tools import GraphToolBase

logger = logging.getLogger(__name__)


class GraphManageTool(GraphToolBase):
    name = "graph_manage"
    # 与同一 step 内的其他动作串行，避免并发写 .juice/graphs。
    description = "创建或更新 .juice/graphs 中的 local graph；删除请使用通用文件工具"
    inputs = {
        "action": {"type": "string", "description": "create/edit"},
        "name": {"type": "string", "description": "graph 名称"},
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
            "graph_manage 完成: action=%s name=%s success=%s",
            str(action or "").strip().lower(),
            name,
            result.get("success"),
        )
        return result


__all__ = ["GraphManageTool"]
