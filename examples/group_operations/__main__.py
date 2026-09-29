"""Use persistent Group workers to turn customer feedback into operations work."""

from __future__ import annotations

import argparse

from .._runtime import add_runtime_arguments, run_showcase, showcase_workspace


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
        help="Dynamic customer-feedback batch and the operational question to answer.",
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
        create_options={"group_name": "customer_operations"},
    )


if __name__ == "__main__":  # pragma: no cover - exercised through ``python -m``.
    main()
