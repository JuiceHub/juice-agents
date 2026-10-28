"""Run a controlled Tool and specialist evolution work order with Agent mode."""

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
    "工单 DEMO-001：需要一个客户反馈标签归一化能力和对应复核专员。"
    "输入样例为 [' Bug ', 'BUG', ' Feature ', '', 'feature']；"
    "预期输出 labels=['bug', 'feature']，忽略空白并按首次出现顺序去重。"
)


EVOLUTION_SYSTEM_INSTRUCTIONS = """
## Controlled ticket capability evolution

For this ticket, demonstrate a minimal, auditable capability change rather
than a speculative redesign. The user message contains only the work-order
details; this fixed workflow is mandatory:

1. Inspect the existing Tool and Agent declarations first. Reuse an existing
   capability if it already fulfills the ticket.
2. If a new capability is required, create exactly one narrowly scoped
   declarative Tool through `tool_manage(action="save")`. Its `forward` source
   must be deterministic, must not access network or secrets, and must return
   a structured result that includes the ticket input.
3. Create exactly one functional specialist through `agent_manage`: call
   `validate` with its complete config before `save`. The specialist may hold
   only the necessary new Tool and must finish through `submit_output`.
4. A save takes effect at a step boundary. In a later model step, verify the
   generated Tool is available, call it yourself, then delegate a focused
   validation task to the saved specialist using `agent_tool`.
5. Report the Tool name, specialist name, validation evidence, the direct Tool
   result, delegated result, and any remaining risk. Do not alter unrelated
   workspace configuration or claim a refresh before observing it.
""".strip()


def configure_evolution_runner(runner: Any) -> None:
    """Extend the detached root declaration before lazy Agent construction.

    Apply the same policy after create and resume. Runner owns Agent creation;
    the example changes only its fixed scenario instructions and leaves model,
    tool binding, persisted conversations, and lifecycle to the shared runtime.
    """

    existing = str(runner.root_config.instructions or "").strip()
    instructions = "\n\n".join(
        part for part in (existing, EVOLUTION_SYSTEM_INSTRUCTIONS) if part
    )
    runner.root_config = replace(runner.root_config, instructions=instructions)


def build_parser() -> argparse.ArgumentParser:
    """Build the parser for the dynamic business work order."""

    parser = argparse.ArgumentParser(
        prog="python -m examples.agent_evolution",
        description=(
            "Have an Agent make and verify one controlled Tool/specialist "
            "capability change for a real work order."
        )
    )
    parser.add_argument(
        "ticket",
        nargs="?",
        default=DEFAULT_TASK,
        help="Work-order details; defaults to creating and verifying feedback-label normalization.",
    )
    add_runtime_arguments(parser)
    return parser


def main(argv: list[str] | None = None):
    """Run a real evolution work order through public ``Juice.runners`` APIs."""

    args = build_parser().parse_args(argv)
    workspace = showcase_workspace(__file__)
    return run_showcase(
        args,
        agent_mode="agent",
        workspace=workspace,
        task=args.ticket,
        configure_runner=configure_evolution_runner,
    )


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess entry tests.
    main()
