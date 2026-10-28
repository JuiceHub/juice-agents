"""Run a source-backed deep-research request through the Agent profile."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

# Direct file execution has no package context; add only this repository root.
# Absolute imports then behave identically for file and ``python -m`` launches.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples._runtime import add_runtime_arguments, run_showcase, showcase_workspace


DEFAULT_TASK = (
    "资料范围：source_mode=workspace，仅本工作区的 __main__.py 源文件。"
    "研究本示例如何选择工作区、传递研究问题，以及有哪些运行参数；"
    "结论需要引用对应源代码，不依赖互联网或当前事件。"
)

RESEARCH_SYSTEM_INSTRUCTIONS = """
## Traceable research demonstration

Run the built-in `deep_research` workflow through `workflow_run` for the supplied
question. Its args require `question` and `source_mode` (`workspace`, `web`, or
`web_workspace`). Respect the source scope in the question; use `workspace` for
local-only evidence. Report the actual workflow result, citation limitations,
and generated artifact paths. Do not substitute an unsupported summary for a
workflow run or invent sources. The `$workflow:deep_research` prefix identifies
this workflow; the remaining text is the dynamic research question.
""".strip()


def configure_research_runner(runner: Any) -> None:
    """Activate only this explicit workflow without changing workspace settings.

    Runner's SDK stream does not parse gateway capability syntax. Reuse the
    existing scoped capability installer so disabled automatic workflows remain
    disabled while this example can actually call its selected workflow.
    """

    from juice_agents.core.agent.tools.builtin.capabilities.manual_capabilities import (
        resolve_manual_capability,
    )

    runner.root_config = replace(
        runner.root_config,
        instructions=f"{runner.root_config.instructions}\n\n{RESEARCH_SYSTEM_INSTRUCTIONS}",
    )
    capability = resolve_manual_capability(
        "$workflow:deep_research", context=runner.config_context,
    )
    capability.install(runner._root().instance)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser without performing model configuration work."""

    parser = argparse.ArgumentParser(
        prog="python -m examples.agent_research",
        description=(
            "Ask an Agent to run the built-in deep_research workflow and preserve its "
            "report, sources, claims, and workflow trace in this Showcase workspace."
        )
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=DEFAULT_TASK,
        help="Research question; defaults to a cited investigation of this example's source file.",
    )
    add_runtime_arguments(parser)
    return parser


def main(argv: list[str] | None = None):
    """Run a real research request inside this package's dedicated workspace."""

    args = build_parser().parse_args(argv)
    # Keep the selected workflow and the dynamic question visible in the trace;
    # configure_research_runner performs the actual scoped capability install.
    return run_showcase(
        args,
        agent_mode="agent",
        workspace=showcase_workspace(__file__),
        task=f"$workflow:deep_research {args.question}",
        configure_runner=configure_research_runner,
    )


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess entry tests.
    main()
