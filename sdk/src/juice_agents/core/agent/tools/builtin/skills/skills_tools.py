"""Skill 的 progressive-disclosure 发现与只读查看工具。

list/view 是 Hermes 风格按需加载的入口：列出候选、读出 SKILL.md，然后由
`$<skill-name>` 执行 —— 属于调用面，不受 `self_evolution.enabled` 约束。
写时复制的编辑能力在 `builtin/evolution/skill_manage.py`，它继承这里的
`SkillToolBase` 以复用同一条 local_dir / registry 解析链。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from juice_agents.core.config.runtime_config import DEFAULT_RUNTIME_CONFIG_PATH
from juice_agents.core.registry.skills import SkillRegistry

from ...runtime.base_tools import CONTENT_OBSERVATION_CHARS, LIST_OBSERVATION_CHARS, Tool
from ...runtime.owner_context import resolve_owner_context

logger = logging.getLogger(__name__)


class SkillToolBase(Tool):
    """Shared local_dir/registry resolution for both read and write tools."""

    def __init__(
        self,
        *,
        owner_agent: Any | None = None,
        local_dir: str | Path | None = None,
        builtin_dir: str | Path | None = None,
        external_dirs: list[str | Path] | None = None,
        config_path: str | Path | None = None,
        allow_names: list[str] | None = None,
        deny_names: list[str] | None = None,
        platform: str = "cli",
        **_: Any,
    ) -> None:
        super().__init__()
        self.owner_agent = owner_agent
        self.local_dir = None if local_dir is None else Path(local_dir)
        self.builtin_dir = None if builtin_dir is None else Path(builtin_dir)
        self.external_dirs = list(external_dirs or [])
        self.config_path = Path(config_path) if config_path is not None else DEFAULT_RUNTIME_CONFIG_PATH
        self.allow_names = list(allow_names or [])
        self.deny_names = list(deny_names or [])
        self.platform = str(platform or "cli")

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _runtime_local_dir(self) -> Path | None:
        if self.local_dir is not None:
            return self.local_dir
        context = resolve_owner_context(self.owner_agent, explicit_workspace_dir=None)
        return context.juice_root / "skills"

    def _registry(self, *, include_owner_denied: bool = True) -> SkillRegistry:
        available_tools = list(getattr(self.owner_agent, "available_tool_names", []) or [])
        declared = getattr(self.owner_agent, "_declared_agent_config", None)
        deny_names = (
            list(getattr(declared, "disabled_skill_names", self.deny_names) or [])
            if include_owner_denied
            else []
        )
        local_dir = self._runtime_local_dir()
        workspace = None if local_dir is None else local_dir.parent.parent
        return SkillRegistry(
            local_dir=local_dir,
            builtin_dir=self.builtin_dir,
            external_dirs=self.external_dirs,
            config_path=self.config_path,
            workspace_dir=workspace,
            platform=self.platform,
            allow_names=self.allow_names,
            deny_names=deny_names,
            available_tools=available_tools,
        )


class SkillsListTool(SkillToolBase):
    _execution_mode = "parallel_safe"
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "skills_list"
    is_read_only = True
    description = "列出按 .juice > Juice 内置 > Plugin/项目/用户外部优先级解析后的 Skills。"
    inputs = {
        "category": {"type": "string", "description": "可选分类过滤", "required": False}
    }
    outputs = {"skills": {"type": "list", "description": "Skill 索引"}}

    def forward(self, category: str | None = None) -> dict[str, Any]:
        registry = self._registry()
        skills = [item.to_dict() for item in registry.list(category=category)]
        return {
            "success": True,
            "skills": skills,
            "categories": registry.categories(),
            "category_descriptions": registry.category_descriptions(),
            "count": len(skills),
            "hint": "Use skill_view(name) to read the complete Skill",
        }


class SkillViewTool(SkillToolBase):
    _execution_mode = "parallel_safe"
    max_observation_chars = CONTENT_OBSERVATION_CHARS
    name = "skill_view"
    is_read_only = True
    description = "读取 Skill 的 SKILL.md 或内部支持文件，并返回实际命中的来源。"
    inputs = {
        "name": {"type": "string", "description": "Skill 名称或 category/name"},
        "file_path": {"type": "string", "description": "Skill 内相对路径", "required": False},
    }
    outputs = {"result": {"type": "object", "description": "Skill 内容、元数据和文件索引"}}

    def forward(self, name: str, file_path: str | None = None) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name 必须为非空字符串")
        try:
            return self._registry().view(name, file_path=file_path)
        except Exception as exc:
            logger.warning("skill_view 失败 name=%s file_path=%s error=%s", name, file_path, exc)
            return {"success": False, "error": str(exc), "name": name, "file_path": file_path}


__all__ = ["SkillToolBase", "SkillViewTool", "SkillsListTool"]
