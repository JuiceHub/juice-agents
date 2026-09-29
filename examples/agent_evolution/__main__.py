"""Run a controlled Tool and specialist evolution work order with Agent mode."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .._runtime import add_runtime_arguments, run_showcase, showcase_workspace


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


def build_evolution_agent(args: argparse.Namespace, *, workspace: Path) -> Any:
    """Build the standard Agent with this Showcase's fixed system policy only."""

    # The public SDK's ``agent_builder`` extension point owns Runner creation;
    # this factory only appends the scenario's process and safety constraints.
    from juice_agents.core.agent.builtin import build_general_agent

    agent = build_general_agent(
        workspace_dir=workspace,
        model_name=args.model,
        model_effort=args.model_effort,
        agent_type=args.agent_type,
    )
    existing = str(getattr(agent, "instructions", "") or "").strip()
    agent.instructions = "\n\n".join(
        part for part in (existing, EVOLUTION_SYSTEM_INSTRUCTIONS) if part
    )
    agent.refresh_system_prompt()
    return agent


def build_parser() -> argparse.ArgumentParser:
    """Build the parser for the dynamic business work order."""

    parser = argparse.ArgumentParser(
        prog="python -m examples.agent_evolution",
        description=(
            "Have an Agent make and verify one controlled Tool/specialist "
            "capability change for a real work order."
        )
    )
    parser.add_argument("ticket", help="Dynamic ticket or work-order details.")
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
        # The builder is passed into the public SDK, which calls it as part of
        # the normal Agent-profile bootstrap. No mock model or private Runner
        # construction is hidden inside this Showcase.
        create_options={
            "agent_builder": lambda: build_evolution_agent(args, workspace=workspace)
        },
        # ``resume_prebuilt_runner`` accepts a rebuilt root rather than an
        # ``agent_builder`` callback. Rebuild through that public SDK option so
        # the resumed session keeps the same controlled-evolution constraints.
        resume_options={
            "root_agent": build_evolution_agent(args, workspace=workspace)
        } if args.resume else None,
    )


if __name__ == "__main__":  # pragma: no cover - exercised through ``python -m``.
    main()
