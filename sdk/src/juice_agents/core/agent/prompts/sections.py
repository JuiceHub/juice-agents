"""Prompt section primitives.

The default agent prompts are still exported as plain strings for runtime
compatibility, but the source of truth is a typed list of sections.  The section
metadata lets tests and future tooling reason about ordering and static/dynamic
boundaries without parsing Markdown headings out of one large template.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Sequence


PromptLanguage = Literal["en", "zh"]


class PromptSectionScope(str, Enum):
    """Whether a prompt section is stable text or rendered from runtime data."""

    STATIC = "static"
    DYNAMIC = "dynamic"


@dataclass(frozen=True)
class PromptSection:
    """One named prompt block.

    `template` may contain Jinja syntax; rendering still happens through the
    existing agent prompt renderer so custom system prompts keep the same rules.
    """

    name: str
    scope: PromptSectionScope
    template: str


def join_prompt_sections(sections: Sequence[PromptSection]) -> str:
    """Join section templates into the string consumed by existing agents."""

    return "\n\n".join(section.template.strip() for section in sections if section.template.strip())


def split_prompt_sections(sections: Sequence[PromptSection]) -> tuple[str, str]:
    """按 scope 把 sections 切成 (static, dynamic) 两段模板字符串。

    设计目的：让 STATIC 段成为稳定前缀，DYNAMIC 段承载会话内可能变化的内容
    （工具列表、托管 agent、skills、memory 等）。这样：
    - Anthropic 可以在 static 段末尾打 `cache_control` 断点；
    - OpenAI 兼容 provider 的自动前缀缓存能稳定命中 static 段；
    - 运行时工具变更只需重渲染 dynamic 段，不击穿 static 缓存前缀。

    约束：一旦出现 DYNAMIC section，其后不允许再出现 STATIC section，否则
    无法切出“纯静态前缀 + 纯动态后缀”的两段结构，缓存边界也就无从谈起。
    现有 ReAct / CodeAct 的 sections 列表天然满足该顺序。
    """

    seen_dynamic = False
    static_parts: list[str] = []
    dynamic_parts: list[str] = []
    for section in sections:
        if section.scope is PromptSectionScope.DYNAMIC:
            seen_dynamic = True
            dynamic_parts.append(section.template)
            continue
        # STATIC section：必须全部位于 DYNAMIC 之前。
        if seen_dynamic:
            raise ValueError(
                f"STATIC section '{section.name}' 出现在 DYNAMIC section 之后，"
                "破坏了静态前缀，无法用于 prompt 缓存分块"
            )
        static_parts.append(section.template)

    static_text = "\n\n".join(part.strip() for part in static_parts if part.strip())
    dynamic_text = "\n\n".join(part.strip() for part in dynamic_parts if part.strip())
    return static_text, dynamic_text


def normalize_prompt_language(language: str | None) -> PromptLanguage:
    """Normalize prompt language values used by agents and preview tooling."""

    normalized = str(language or "en").strip().lower()
    if normalized in {"zh", "cn", "zh-cn", "chinese"}:
        return "zh"
    if normalized in {"en", "en-us", "english"}:
        return "en"
    raise ValueError("prompt_language must be 'en' or 'zh'")


__all__ = [
    "PromptLanguage",
    "PromptSection",
    "PromptSectionScope",
    "join_prompt_sections",
    "split_prompt_sections",
    "normalize_prompt_language",
]
