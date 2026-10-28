"""Coordinate a research/development delivery through the durable Team profile."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Direct file execution has no package context; add only this repository root.
# Absolute imports then behave identically for file and ``python -m`` launches.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples._runtime import add_runtime_arguments, run_showcase, showcase_workspace


DEFAULT_TASK = (
    "交付一个仅依赖Python标准库的订单CSV汇总小工具：order_summary.py提供"
    "summarize_orders(csv_text)，输入列为order_id,status,amount；只汇总paid订单，"
    "金额用Decimal精确求和，返回paid_count和两位小数字符串total_amount，"
    "非法金额抛出ValueError。交付tests/test_order_summary.py并运行验证。"
    "样例数据：o1,paid,10.10；o2,cancelled,99.00；o3,paid,0.20；"
    "该样例预期paid_count=2、total_amount='10.30'。"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for one project delivery brief."""

    parser = argparse.ArgumentParser(
        prog="python -m examples.team_delivery",
        description=(
            "Ask a Team lead to coordinate researcher/developer tasks, inboxes, "
            "and asynchronous work into a resumable project delivery."
        )
    )
    parser.add_argument(
        "brief",
        nargs="?",
        default=DEFAULT_TASK,
        help="Project brief; defaults to delivering and testing a standard-library CSV order summary.",
    )
    add_runtime_arguments(parser)
    return parser


def main(argv: list[str] | None = None):
    """Run the delivery brief inside this package's dedicated workspace."""

    args = build_parser().parse_args(argv)
    return run_showcase(
        args,
        agent_mode="team",
        workspace=showcase_workspace(__file__),
        task=args.brief,
    )


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess entry tests.
    main()
