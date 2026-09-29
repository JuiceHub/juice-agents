"""Skill 的 workspace-local 写时复制编辑工具。

只读发现在 `builtin/skills/skills_tools.py`；本模块继承那里的 `SkillToolBase`，
因此读写共用同一条 local_dir / registry 解析链。
"""

from __future__ import annotations

import logging
from typing import Any

from ..skills.skills_tools import SkillToolBase

logger = logging.getLogger(__name__)


class SkillManageTool(SkillToolBase):
    name = "skill_manage"
    # 与同一 step 内的其他动作串行，避免并发写 .juice/skills。
    description = (
        "直接管理 .juice/skills；编辑 Plugin、项目/用户外部或 Juice 内置 Skill 时，"
        "先完整复制目录到 .juice 再编辑本地副本。"
    )
    inputs = {
        "action": {"type": "string", "description": "create/edit/patch/write_file/load/unload"},
        "name": {"type": "string", "description": "Skill 名称"},
        "content": {"type": "string", "description": "写入内容", "required": False},
        "category": {"type": "string", "description": "create 时可选分类", "required": False},
        "old_string": {"type": "string", "description": "patch 旧片段", "required": False},
        "new_string": {"type": "string", "description": "patch 新片段", "required": False},
        "file_path": {"type": "string", "description": "支持文件相对路径", "required": False},
        "replace_all": {"type": "boolean", "description": "替换全部命中", "required": False},
        "overwrite": {"type": "boolean", "description": "create 是否覆盖本地副本", "required": False},
    }
    outputs = {"result": {"type": "object", "description": "直接文件编辑或写时复制结果"}}

    def forward(
        self,
        action: str,
        name: str,
        content: str | None = None,
        category: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        file_path: str | None = None,
        replace_all: bool = False,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"create", "edit", "patch", "write_file", "load", "unload"}:
            return {
                "result": {
                    "success": False,
                    "action": normalized_action,
                    "error": "skill_manage action 必须为 create/edit/patch/write_file/load/unload",
                }
            }

        # manage 必须能重新加载或编辑当前 Agent 已 unload 的 Skill；只读 list/view
        # 仍使用 per-agent deny-list 隐藏它。
        registry = self._registry(include_owner_denied=False)
        if normalized_action == "unload":
            from juice_agents.core.agent.runtime_reconciler import unload_skill

            runtime_refresh = unload_skill(self.owner_agent, name)
            result = {
                "success": True,
                "action": "unload",
                "name": name,
                "path": None,
            }
        else:
            if normalized_action == "load":
                meta = registry.get(name)
                result = {
                    "success": True,
                    "action": "load",
                    "name": meta.name,
                    "path": str(meta.skill_file),
                }
            else:
                result = registry.manage(
                    normalized_action,
                    name=name,
                    content=content,
                    category=category,
                    old_string=old_string,
                    new_string=new_string,
                    file_path=file_path,
                    replace_all=replace_all,
                    overwrite=overwrite,
                )
                if not result.get("success"):
                    return {"result": result}
            from juice_agents.core.agent.runtime_reconciler import bind_skill

            runtime_refresh = bind_skill(self.owner_agent, name)

        result["runtime_refresh"] = runtime_refresh
        logger.info(
            "skill_manage 完成并登记刷新: action=%s name=%s status=%s",
            normalized_action,
            name,
            runtime_refresh["status"],
        )
        return {"result": result}


__all__ = ["SkillManageTool"]
