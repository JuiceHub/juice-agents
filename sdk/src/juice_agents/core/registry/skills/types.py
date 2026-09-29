"""Skill registry 类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SkillSource = Literal["local", "builtin", "external"]


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """单个 SKILL.md 的索引元数据。"""

    name: str
    description: str
    category: str | None
    version: str | None
    source: SkillSource
    skill_dir: Path
    skill_file: Path
    read_only: bool
    platforms: tuple[str, ...] = field(default_factory=tuple)
    conditions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        if self.category:
            return f"{self.category}/{self.name}"
        return self.name

    @property
    def location(self) -> str:
        return str(self.skill_file)

    def to_prompt_dict(self) -> dict[str, str]:
        """返回 system prompt 中使用的轻量索引。"""
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "description": self.description,
            "category": self.category or "uncategorized",
            "source": self.source,
            "location": str(self.skill_file),
        }

    def to_dict(self) -> dict[str, Any]:
        """返回工具/API 使用的可序列化结构。"""
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "source": self.source,
            "skill_dir": str(self.skill_dir),
            "skill_file": str(self.skill_file),
            "read_only": self.read_only,
            "platforms": list(self.platforms),
            "conditions": {key: list(value) for key, value in self.conditions.items()},
            "metadata": dict(self.metadata),
        }


__all__ = ["SkillMetadata", "SkillSource"]
