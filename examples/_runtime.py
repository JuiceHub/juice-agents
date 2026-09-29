"""四个业务 Showcase 共用的公开 SDK 运行时入口。

示例刻意只依赖 :class:`juice_agents.Juice` 的公开 API。这样用户复制入口时
不会把 Runner 的内部构造、临时 mock 或测试专用模型带进自己的项目。
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Callable, Mapping

from juice_agents import Juice

logger = logging.getLogger(__name__)

ModeOptions = Mapping[str, Any]
EventWriter = Callable[[str], None]


def showcase_workspace(entry_file: str | Path) -> Path:
    """Return the dedicated folder that contains one Showcase entry point.

    Every Showcase is intentionally its own small workspace.  Runner state,
    workspace config, and model-created artifacts therefore stay beside the
    entry point under ``<showcase>/.juice`` instead of leaking into the
    repository from which a user launched the command.
    """

    workspace = Path(entry_file).resolve().parent
    if not workspace.is_dir():  # Defensive only: every package entry has a parent.
        raise RuntimeError(f"Showcase workspace is unavailable: {workspace}")
    return workspace


def add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the identical public runtime controls to a Showcase parser.

    Business inputs intentionally stay on the individual parser as positional
    arguments. Each entry folder is its fixed workspace, so no command-line
    path can accidentally direct a Showcase into the caller's repository.
    """

    parser.add_argument(
        "--model",
        default=None,
        help="Optional workspace model name override (otherwise use workspace configuration).",
    )
    parser.add_argument(
        "--model-effort",
        default=None,
        help="Optional model reasoning-effort override.",
    )
    parser.add_argument(
        "--agent-type",
        choices=("react", "codeact"),
        default="react",
        help="Agent protocol used when the selected ModeProfile creates actors.",
    )
    parser.add_argument(
        "--permission-mode",
        choices=("default", "accept"),
        default="default",
        help="Tool approval policy for a newly created runner.",
    )
    parser.add_argument(
        "--resume",
        metavar="RUNNER_ID",
        default=None,
        help="Resume this durable runner instead of creating a new conversation.",
    )


def _juice_from_args(
    args: argparse.Namespace,
    *,
    workspace: Path,
    juice_cls: type[Juice] = Juice,
) -> Juice:
    """Construct one workspace-bound public SDK client from parsed arguments."""

    kwargs: dict[str, Any] = {"workspace": workspace}
    if args.model:
        kwargs["model_name"] = args.model
    if args.model_effort is not None:
        kwargs["model_effort"] = args.model_effort
    return juice_cls(**kwargs)


def create_or_resume_runner(
    args: argparse.Namespace,
    *,
    agent_mode: str,
    workspace: Path,
    create_options: ModeOptions | None = None,
    resume_options: ModeOptions | None = None,
    juice_cls: type[Juice] = Juice,
) -> Any:
    """Create or restore a ModeProfile through ``Juice.runners`` only.

    The runner manifest owns Mode and permission state after creation. A resume
    therefore passes only the runner id, protocol, and optional root rebuild
    dependencies; it must not silently overwrite the persisted permission mode.
    """

    juice = _juice_from_args(args, workspace=workspace, juice_cls=juice_cls)
    resume_id = str(args.resume or "").strip()
    if resume_id:
        options = dict(resume_options or {})
        logger.info("恢复 Showcase Runner: runner_id=%s", resume_id)
        return juice.runners.resume(
            runner_id=resume_id,
            agent_type=args.agent_type,
            **options,
        )

    options = dict(create_options or {})
    logger.info(
        "创建 Showcase Runner: mode=%s permission_mode=%s agent_type=%s",
        agent_mode,
        args.permission_mode,
        args.agent_type,
    )
    return juice.runners.create(
        permission_mode=args.permission_mode,
        agent_mode=agent_mode,
        agent_type=args.agent_type,
        **options,
    )


def _event_summary(event: Mapping[str, Any]) -> str:
    """Render a compact stable summary without depending on private event types."""

    kind = str(event.get("kind") or "event")
    actor = str(event.get("actor_name") or "")
    prefix = f"[{kind}]" + (f" {actor}" if actor else "")
    if kind == "round_end":
        return f"{prefix} outcome={event.get('outcome', '')} output={event.get('output', '')}"
    if kind == "stream_cancelled":
        return f"{prefix} reason={event.get('stop_reason') or event.get('reason') or ''}"
    if kind == "team_update":
        return f"{prefix} {json.dumps(event.get('team_event') or {}, ensure_ascii=False, default=str)}"
    if kind == "runner_lifecycle":
        return f"{prefix} {json.dumps(event.get('lifecycle_event') or {}, ensure_ascii=False, default=str)}"
    if kind == "action_step":
        step = event.get("action_step")
        step_num = event.get("step_num")
        # ActionStep is intentionally an implementation object. Its useful
        # public trace is represented by a short string rather than its full
        # potentially enormous observation payload.
        output = getattr(step, "output", None) if step is not None else None
        return f"{prefix} step={step_num if step_num is not None else ''} output={output or ''}"
    return f"{prefix} {json.dumps(dict(event), ensure_ascii=False, default=str)}"


def render_event(event: Any, *, writer: EventWriter = print) -> None:
    """Write one Runner stream event for interactive showcase users."""

    if isinstance(event, Mapping):
        writer(_event_summary(event))
        return
    writer(f"[event] {event}")


def stream_runner(
    runner: Any,
    task: str,
    *,
    writer: EventWriter = print,
) -> Any:
    """Consume a runner stream and return the final public round output."""

    final_output: Any = None
    for event in runner.stream(task):
        render_event(event, writer=writer)
        if isinstance(event, Mapping) and event.get("kind") == "round_end":
            final_output = event.get("output")
    return final_output

def run_showcase(
    args: argparse.Namespace,
    *,
    agent_mode: str,
    workspace: Path,
    task: str,
    create_options: ModeOptions | None = None,
    resume_options: ModeOptions | None = None,
    writer: EventWriter = print,
    juice_cls: type[Juice] = Juice,
) -> Any:
    """Run one business task, print its durable id, and always release workers.

    ``stop()`` is a lifecycle cleanup rather than error handling: Group and
    Team can own background actors even after a foreground response is emitted.
    Calling it in ``finally`` makes interruption safe and leaves the runner in
    a resumable, persisted state.
    """

    runner = create_or_resume_runner(
        args,
        agent_mode=agent_mode,
        workspace=workspace,
        create_options=create_options,
        resume_options=resume_options,
        juice_cls=juice_cls,
    )
    writer(f"Runner ID: {runner.runner_id}")
    try:
        return stream_runner(runner, task, writer=writer)
    finally:
        runner.stop()
        logger.info("Showcase Runner 已回收: runner_id=%s", runner.runner_id)


__all__ = [
    "add_runtime_arguments",
    "create_or_resume_runner",
    "render_event",
    "run_showcase",
    "showcase_workspace",
    "stream_runner",
]
