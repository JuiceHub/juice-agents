"""Use persistent Group workers to turn customer feedback into operations work."""

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
    "客户反馈批次 OPS-DEMO：F1 企业客户导出10万行报表耗时90秒；"
    "F2 两位付费客户遇到导出失败且没有错误提示；"
    "F3 免费客户希望增加深色主题。本周只有2个开发人日。"
    "交付本目录 operations_plan.json，包含反馈分类、优先级、负责人角色、"
    "验收指标，以及产品和客户支持两个视角的结论。"
)

OPERATIONS_SYSTEM_INSTRUCTIONS = """
## Customer feedback operations demonstration

Use `agent_tool` to delegate independent product-priority and customer-support
analysis to persistent Group workers. Keep each assignment bounded to the given
feedback. Reuse worker identities when following up on their conclusions.
Reconcile their findings into the requested deliverable in the current workspace,
and report the worker contributions and the evidence for each priority. Do not
invent customer interviews, telemetry, or completed engineering changes.
""".strip()


def configure_operations_runner(runner: Any) -> None:
    """Keep the repeatable collaboration process in the root system policy."""

    runner.root_config = replace(
        runner.root_config,
        instructions=f"{runner.root_config.instructions}\n\n{OPERATIONS_SYSTEM_INSTRUCTIONS}",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for one customer-feedback batch."""

    parser = argparse.ArgumentParser(
        prog="python -m examples.group_operations",
        description=(
            "Ask a persistent Group manager to classify customer feedback, propose "
            "priorities, and deliver an actionable operations conclusion."
        )
    )
    parser.add_argument(
        "feedback",
        nargs="?",
        default=DEFAULT_TASK,
        help="Feedback batch; defaults to a three-item batch and a two-day operations plan.",
    )
    add_runtime_arguments(parser)
    return parser


def main(argv: list[str] | None = None):
    """Run the feedback operation inside this package's dedicated workspace."""

    args = build_parser().parse_args(argv)
    return run_showcase(
        args,
        agent_mode="group",
        workspace=showcase_workspace(__file__),
        task=args.feedback,
        configure_runner=configure_operations_runner,
    )


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess entry tests.
    main()
