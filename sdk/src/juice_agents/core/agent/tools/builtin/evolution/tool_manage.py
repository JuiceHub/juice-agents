"""声明式 Tool YAML 的写入工具。

只读查询在 `builtin/tools/tools_tools.py`；本模块继承那里的
`ToolConfigToolBase`，因此读写共用同一条 workspace / config_dir 解析链。
"""

from __future__ import annotations

import logging
from typing import Any

from juice_agents.core.registry.common import normalize_name

from ..tools.tools_tools import ToolConfigToolBase

logger = logging.getLogger(__name__)


class ToolManageTool(ToolConfigToolBase):
    name = "tool_manage"
    # 与同一 step 内的其他动作串行，避免并发写 .juice/tools。
    description = (
        "保存声明式 Tool YAML 并绑定到当前 Agent，或仅卸载当前引用；"
        "能力在下一模型 step 生效，unload 不删除 YAML。"
    )
    inputs = {
        "action": {"type": "string", "description": "save/unload"},
        "name": {"type": "string", "description": "Tool 名称"},
        "config": {"type": "object", "description": "save 时的完整 Tool 配置", "required": False},
    }
    outputs = {"result": {"type": "object", "description": "直接文件写入结果"}}

    def forward(
        self,
        action: str,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        normalized_name = normalize_name(name)
        registry = self._registry()
        if normalized_action == "save":
            from juice_agents.core.registry.tools.defaults import get_default_tool_factories

            if normalized_name in get_default_tool_factories():
                raise PermissionError(
                    "Python 内置 Tool 没有声明式 YAML，不能写时复制；请使用新名称创建 ToolConfig"
                )
            if config is None:
                # 省略 config 等价于 load：读回现有声明再重新绑定。
                try:
                    payload = registry.load_config(normalized_name).to_dict()
                except FileNotFoundError:
                    raise ValueError("save 必须提供 config") from None
            else:
                payload = dict(config)
            payload["name"] = normalized_name
            saved = registry.save_config(payload, name=normalized_name)
            path = self._config_dir() / f"{saved.name}.yaml"
            from juice_agents.core.agent.runtime_reconciler import bind_tool

            runtime_refresh = bind_tool(self.owner_agent, saved.name)
            logger.info(
                "tool_manage 保存并登记刷新: name=%s path=%s status=%s",
                saved.name,
                path,
                runtime_refresh["status"],
            )
            return {
                "result": {
                    "action": "save",
                    "name": saved.name,
                    "path": str(path),
                    "config": saved.to_dict(),
                    "runtime_refresh": runtime_refresh,
                }
            }
        if normalized_action == "unload":
            from juice_agents.core.agent.runtime_reconciler import unload_tool

            path = self._config_dir() / f"{normalized_name}.yaml"
            runtime_refresh = unload_tool(self.owner_agent, normalized_name)
            logger.info(
                "tool_manage 仅卸载引用: name=%s path=%s status=%s",
                normalized_name,
                path,
                runtime_refresh["status"],
            )
            return {
                "result": {
                    "action": "unload",
                    "name": normalized_name,
                    "path": str(path),
                    "runtime_refresh": runtime_refresh,
                }
            }
        raise ValueError("tool_manage action 必须为 save/unload")


__all__ = ["ToolManageTool"]
