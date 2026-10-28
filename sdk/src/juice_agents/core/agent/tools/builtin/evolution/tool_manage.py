"""声明式 Tool YAML 的写入工具。

只读查询在 `builtin/tools/tools_tools.py`；本模块继承那里的
`ToolConfigToolBase`，因此读写共用同一条 workspace / config_dir 解析链。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from juice_agents.core.registry.common import normalize_name

from ..tools.tools_tools import ToolConfigToolBase

logger = logging.getLogger(__name__)

# 参数示例只属于 tool_manage 的使用契约。保存为数据并统一编码，确保源码中的
# 换行以合法 JSON 字符串展示；两个现有协议渲染器会自动带上这段工具说明。
_SAVE_EXAMPLE_ARGS = {
    "action": "save",
    "name": "print_current_time",
    "config": {
        "description": "打印并返回带时区的当前系统时间",
        "inputs": {},
        "outputs": {"current_time": {"type": "string", "description": "ISO 8601 格式的当前时间"}},
        "imports": ["datetime"],
        "forward": (
            "def forward(self):\n"
            "    current_time = datetime.datetime.now().astimezone().isoformat(timespec='seconds')\n"
            "    print(current_time)\n"
            "    return {'current_time': current_time}\n"
        ),
    },
}


class ToolManageTool(ToolConfigToolBase):
    name = "tool_manage"
    # 与同一 step 内的其他动作串行，避免并发写 .juice/tools。
    description = (
        "保存声明式 Tool YAML 并登记当前 Agent 的工具引用，或仅卸载当前引用。\n"
        "使用规范：\n"
        "- 创建可复用工具须使用 save；临时执行 Python/Shell 代码不等于创建工具。"
        "修改已有工具前先检查原声明。\n"
        "- 分别核验声明保存、引用登记和调用结果。检查 result.runtime_refresh.status 和 "
        "effective_from；scheduled/next_step 表示当前 Agent 下一步更新工具列表，"
        "fresh_acquire 表示后续新建实例时生效，unsupported 表示无法自动绑定。"
        "新工具出现在可用工具列表后才可调用验证，尚未激活或验证须如实说明。\n"
        "- save 报错时，先用可用的只读工具确认 YAML 是否已写入，再决定如何重试；"
        "不要重写 YAML 绕过绑定错误。root 与其他 Agent 一样持久化工具引用；unload 不删除 YAML。\n"
        "以下是完整 save 参数示例，按当前 ReAct/CodeAct 协议调用 tool_manage：\n"
        f"save_args: {json.dumps(_SAVE_EXAMPLE_ARGS, ensure_ascii=False)}"
    )
    inputs = {
        "action": {
            "type": "string",
            "description": "save：保存配置并登记引用；unload：移除引用，不删除工具文件",
        },
        "name": {"type": "string", "description": "Tool 名称；不能覆盖 Python 内置工具，创建时使用新名称"},
        "config": {
            "type": "object",
            "description": (
                "创建时必须提供完整配置对象（ReAct 用 JSON object，CodeAct 用 Python dict）；"
                "省略仅用于重新登记已有 YAML，unload 时不传。"
                "字段：description（用途）、inputs/outputs（参数名到 type/description/required 的对象映射；"
                "无参数用 {}）、forward（Python 函数源码字符串，必须定义 def forward(self, ...)，"
                "除 self 外的参数与 inputs 对应，可选参数提供默认值，返回值与 outputs 对应）。"
                "可选字段：name（以外层 name 为准）、imports（模块名字符串列表）、"
                "runtime（safe/trusted，默认 safe）、init_params（初始化属性对象）、"
                "max_observation_chars（正整数，默认 12000）。"
                "不要使用 command、params、entrypoint、source 或 schema 代替这些配置字段。"
            ),
            "required": False,
        },
    }
    outputs = {
        "result": {
            "type": "object",
            "description": "action/name/path、save 时的 config，以及引用登记状态 runtime_refresh（status/effective_from）",
        }
    }

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

            runtime_refresh = bind_tool(
                self.owner_agent, saved.name, tool_config_dir=self._config_dir()
            )
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
