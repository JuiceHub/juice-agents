"""Prompt preview API and CLI.

The default preview is the complete prompt template assembled from every
`PromptSection`, so conditional dynamic sections such as memory remain visible
for review.  Use `mode="rendered"` / `--mode rendered` when you need the older
runtime-style preview rendered through a real agent instance.
"""

from __future__ import annotations

import argparse
from typing import Literal

from jinja2 import Template

from juice_agents.core.agent import CodeActAgent, ReActAgent
from juice_agents.core.agent.prompts.codeact_agent_prompt import get_codeact_prompt_sections
from juice_agents.core.agent.prompts.react_agent_prompt import get_react_prompt_sections
from juice_agents.core.agent.prompts.sections import (
    PromptLanguage,
    PromptSection,
    join_prompt_sections,
    normalize_prompt_language,
)
from juice_agents.core.agent.tools.builtin.code_execution.code_tools import ShellTool
from juice_agents.core.agent.tools.runtime.base_tools import Tool

AgentType = Literal["react", "codeact"]
PreviewMode = Literal["template", "rendered"]


class _PreviewModel:
    """Minimal model stub; preview rendering never calls `generate()`."""

    def generate(self, messages, stop_sequence=None):  # pragma: no cover - defensive only
        del messages, stop_sequence
        raise RuntimeError("prompt preview does not execute model generation")


def _build_preview_tools(with_tools: list[str] | None) -> list[Tool]:
    tools: list[Tool] = []
    for name in with_tools or []:
        if name == "shell":
            tools.append(ShellTool())
        else:
            raise ValueError(f"不支持的预览工具: {name}")
    return tools


def _build_preview_agent(
    *,
    agent_type: AgentType,
    language: PromptLanguage,
    instructions: str | None,
    with_tools: list[str] | None,
):
    tools = _build_preview_tools(with_tools)
    common_kwargs = {
        "tools": tools,
        "model": _PreviewModel(),
        "system_prompt": None,
        "max_steps": 1,
        "instructions": instructions,
        "prompt_language": language,
    }
    if agent_type == "react":
        return ReActAgent(**common_kwargs)
    if agent_type == "codeact":
        return CodeActAgent(**common_kwargs)
    raise ValueError(f"未知 agent_type: {agent_type}")


def _sections_for_agent_type(agent_type: AgentType, language: PromptLanguage) -> list[PromptSection]:
    if agent_type == "react":
        return get_react_prompt_sections(language)
    if agent_type == "codeact":
        return get_codeact_prompt_sections(language)
    raise ValueError(f"未知 agent_type: {agent_type}")


def _render_section(section: PromptSection, context: dict[str, object]) -> str:
    return Template(section.template).render(**context).strip()


def _render_sections_preview(
    agent_type: AgentType,
    language: PromptLanguage,
    context: dict[str, object],
) -> str:
    parts: list[str] = []
    for section in _sections_for_agent_type(agent_type, language):
        rendered = _render_section(section, context)
        if not rendered:
            continue
        parts.append(f"--- [{section.scope.value}] {section.name} ---\n{rendered}")
    return "\n\n".join(parts)


def _template_sections_preview(agent_type: AgentType, language: PromptLanguage) -> str:
    parts: list[str] = []
    for section in _sections_for_agent_type(agent_type, language):
        template = section.template.strip()
        if not template:
            continue
        parts.append(f"--- [{section.scope.value}] {section.name} ---\n{template}")
    return "\n\n".join(parts)


def render_prompt_preview(
    *,
    agent_type: AgentType = "react",
    language: str = "en",
    mode: PreviewMode = "template",
    instructions: str | None = None,
    show_sections: bool = False,
    with_tools: list[str] | None = None,
) -> str:
    """Render the default prompt template or a runtime-style preview.

    Args:
        agent_type: `react` or `codeact`.
        language: `en` or `zh`.
        mode: `template` returns the full section templates without rendering;
            `rendered` renders through a real agent instance.
        instructions: Optional dynamic instructions for `rendered` mode.
        show_sections: When true, include section headers with scope metadata.
        with_tools: Optional preview tools for `rendered` mode. Currently
            supports `["shell"]`.
    """

    prompt_language = normalize_prompt_language(language)
    if mode == "template":
        sections = _sections_for_agent_type(agent_type, prompt_language)
        if show_sections:
            return _template_sections_preview(agent_type, prompt_language)
        return join_prompt_sections(sections)
    if mode != "rendered":
        raise ValueError(f"未知 prompt preview mode: {mode}")

    agent = _build_preview_agent(
        agent_type=agent_type,
        language=prompt_language,
        instructions=instructions,
        with_tools=with_tools,
    )
    if not show_sections:
        return agent.system_prompt
    context = agent._build_prompt_context()
    if agent_type == "codeact":
        context["authorized_imports"] = getattr(agent, "authorized_imports", [])
    return _render_sections_preview(agent_type, prompt_language, context)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview juice-agents default system prompts.")
    parser.add_argument("--agent-type", choices=["react", "codeact"], default="react")
    parser.add_argument("--language", choices=["en", "zh"], default="en", help="默认 prompt 语言")
    parser.add_argument(
        "--mode",
        choices=["template", "rendered"],
        default="template",
        help="template 直接拼接完整模板；rendered 渲染实际运行时 prompt",
    )
    parser.add_argument("--instructions", default=None, help="rendered 模式的动态任务指令预览内容")
    parser.add_argument("--show-sections", action="store_true", help="显示 section 名称与作用域")
    parser.add_argument(
        "--with-tools",
        action="append",
        choices=["shell"],
        default=[],
        help="为预览注入额外工具；可重复传入",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    print(
        render_prompt_preview(
            agent_type=args.agent_type,
            language=args.language,
            mode=args.mode,
            instructions=args.instructions,
            show_sections=args.show_sections,
            with_tools=args.with_tools,
        )
    )


if __name__ == "__main__":
    main()
