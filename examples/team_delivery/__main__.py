"""Coordinate a research/development delivery through the durable Team profile."""

from __future__ import annotations

import argparse

from .._runtime import add_runtime_arguments, run_showcase, showcase_workspace


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
        help="Dynamic project brief, expected code/document outputs, and acceptance checks.",
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
        create_options={"team_name": "delivery_team"},
    )


if __name__ == "__main__":  # pragma: no cover - exercised through ``python -m``.
    main()
