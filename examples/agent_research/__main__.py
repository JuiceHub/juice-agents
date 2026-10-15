"""Run a source-backed deep-research request through the Agent profile."""

from __future__ import annotations

import argparse

from .._runtime import add_runtime_arguments, run_showcase, showcase_workspace


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
        help="Dynamic research question, for example: 'Compare two database choices for our service'.",
    )
    add_runtime_arguments(parser)
    return parser


def main(argv: list[str] | None = None):
    """Run a real research request inside this package's dedicated workspace."""

    args = build_parser().parse_args(argv)
    # ``$workflow:deep_research`` 是显式 workflow 入口：只传纯文本问题，
    # 其余参数（如 source_mode）由 root agent 依据 input_schema 自行补全或向用户追问。
    return run_showcase(
        args,
        agent_mode="agent",
        workspace=showcase_workspace(__file__),
        task=f"$workflow:deep_research {args.question}",
    )


if __name__ == "__main__":  # pragma: no cover - exercised through ``python -m``.
    main()
