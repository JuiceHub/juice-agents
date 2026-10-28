"""业务 Showcase 共用的公开 SDK 运行时入口。

通过 :class:`juice_agents.Juice` 创建或恢复 Runner，集中处理参数、
终端交互、流式事件展示与资源清理，供各示例入口复用。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from juice_agents import Juice
from juice_agents.core.utils import load_env_file

logger = logging.getLogger(__name__)

ModeOptions = Mapping[str, Any]
EventWriter = Callable[[str], None]
_console_lock = threading.Lock()
_REPO_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _showcase_environment(workspace: Path) -> Iterator[None]:
    """Share repository credentials while keeping each example's workspace.

    Resolve paths from source files rather than cwd so IDE and absolute-file
    launches behave alike. Merge files before exporting: example values beat
    repository defaults, and existing process variables always take priority.
    Keep the environment alive until Runner cleanup joins its worker threads;
    remove only variables introduced here when an embedded caller regains control.
    """

    values: dict[str, str] = {}
    for path in dict.fromkeys((_REPO_ROOT / ".env", workspace / ".env")):
        if path.is_file():
            values.update(load_env_file(path))
            logger.info("加载示例环境配置: %s", path)
    added: list[str] = []
    try:
        for name, value in values.items():
            if name not in os.environ:
                os.environ[name] = value
                added.append(name)
        yield
    finally:
        for name in added:
            os.environ.pop(name, None)


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
        default=None,
        help="Optional root-agent protocol override (otherwise use model configuration).",
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


def console_ask(request: Mapping[str, Any]) -> dict[str, Any]:
    """Handle SDK questions/approvals without silently approving tool actions.

    Concurrent Team members share stdin, so keep one question and its answer
    together. Noninteractive runs must select their approval policy explicitly.
    """

    if not sys.stdin.isatty():
        error = "示例需要终端交互；请在终端运行，或明确使用 --permission-mode accept 允许工具操作。"
        logger.warning(error)
        return {"status": "error", "error": error}
    with _console_lock:
        print(f"\n[ask] {request.get('question', '')}")
        options = request.get("options") or []
        for index, option in enumerate(options, 1):
            print(f"  {index}. {option['label']} {option.get('description', '')}")
        try:
            answer = input("选择编号（多选用逗号分隔）；自由回答或空行取消: ").strip()
        except (EOFError, KeyboardInterrupt):
            return {"status": "cancelled"}
        if not answer:
            return {"status": "cancelled"}
        indices = [part.strip() for part in answer.split(",")]
        if options and all(part.isdigit() and 1 <= int(part) <= len(options) for part in indices):
            if len(indices) > 1 and not request.get("multiple", False):
                return {"status": "error", "error": "当前问题仅允许选择一个选项"}
            return {"status": "answered", "selected": [options[int(part) - 1] for part in indices]}
        if not options or request.get("allow_custom", True):
            return {"status": "answered", "custom_response": answer}
        return {"status": "error", "error": "请选择有效的选项编号"}


def _juice_from_args(
    args: argparse.Namespace,
    *,
    workspace: Path,
    juice_cls: type[Juice] = Juice,
) -> Juice:
    """Construct one workspace-bound public SDK client from parsed arguments."""

    # Model/protocol selection belongs to AgentConfig, not the SDK client.
    return juice_cls(workspace=workspace, ask_handler=console_ask)


def create_or_resume_runner(
    args: argparse.Namespace,
    *,
    agent_mode: str,
    workspace: Path,
    create_options: ModeOptions | None = None,
    resume_options: ModeOptions | None = None,
    juice_cls: type[Juice] = Juice,
) -> Any:
    """Create or restore a declarative mode through ``Juice.runners`` only.

    The runner manifest owns Mode and permission state after creation. A resume
    therefore passes only the runner id and supported resume options; it must
    not silently overwrite the persisted permission mode.
    """

    juice = _juice_from_args(args, workspace=workspace, juice_cls=juice_cls)
    resume_id = str(args.resume or "").strip()
    if resume_id:
        options = dict(resume_options or {})
        logger.info("恢复 Showcase Runner: runner_id=%s", resume_id)
        return juice.runners.resume(
            runner_id=resume_id,
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
        runner_config=agent_mode,
        **options,
    )


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read a live SDK object or its transport-serialized mapping uniformly."""

    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _compact(value: Any, *, limit: int = 1200) -> str:
    """Bound trace payloads while preserving useful text and structured results."""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "… [truncated]"


def _event_summary(event: Mapping[str, Any]) -> str | None:
    """Show actual decisions/results instead of dumping raw Runner state.

    ``thinking`` and ``step_complete`` repeat the following ``action_step``.
    Omitting those copies keeps a multi-agent demonstration readable while
    tool_start/tool_end still expose progress during slow tool execution.
    """

    kind = str(event.get("kind") or "event")
    agent = str(event.get("agent_name") or "")
    prefix = f"[{kind}]" + (f" {agent}" if agent else "")
    if kind == "round_end":
        # The final deliverable is intentionally complete; only intermediate
        # observations are truncated, so users can read the task's actual result.
        return f"{prefix} outcome={event.get('outcome', '')}\n{event.get('output', '')}"
    if kind == "stream_cancelled":
        return f"{prefix} reason={event.get('stop_reason') or event.get('reason') or ''}"
    if kind == "team_update":
        update = event.get("update") or {}
        snapshot = event.get("snapshot") or {}
        task_id = _field(update, "task_id")
        tasks = _field(snapshot, "tasks", []) or []
        task = next((item for item in tasks if _field(item, "task_id") == task_id), None) if task_id else None
        details = dict(update) if isinstance(update, Mapping) else {"update": str(update)}
        if task is not None:
            details.update({key: _field(task, key) for key in ("status", "title", "claimed_by") if _field(task, key)})
        return f"{prefix} {_compact(details)}"
    if kind == "runner_lifecycle":
        return f"{prefix} {_compact(event.get('lifecycle_event') or {})}"
    if kind == "action_event":
        event_type = event.get("event_type")
        data = event.get("data") or {}
        if event_type not in {"tool_start", "tool_end"}:
            return None
        tool = _field(data, "tool_name", "")
        details = _field(data, "tool_args", {}) if event_type == "tool_start" else _field(data, "observation", "")
        error = _field(data, "error")
        return f"[{event_type}] {agent} {tool} {_compact(details)}" + (f" error={_compact(error)}" if error else "")
    if kind == "action_step":
        step = event.get("action_step")
        step_num = event.get("step_num", _field(step, "step_num", ""))
        parts = [f"{prefix} step={step_num} model_output={_compact(_field(step, 'model_output', ''))}"]
        observations = _field(step, "observations", []) or []
        if observations:
            parts.append(f"observations={_compact(observations)}")
        error = _field(step, "error")
        if error:
            parts.append(f"error={_compact(str(error))}")
        return "\n".join(parts)
    return prefix


def render_event(event: Any, *, writer: EventWriter = print) -> None:
    """Write compact progress from live Runner events and serialized steps."""

    summary = _event_summary(event) if isinstance(event, Mapping) else f"[event] {_compact(str(event))}"
    if summary is not None:
        writer(summary)


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
    configure_runner: Callable[[Any], None] | None = None,
    writer: EventWriter = print,
    juice_cls: type[Juice] = Juice,
) -> Any:
    """Run one business task, print its durable id, and always release workers.

    ``stop()`` is a lifecycle cleanup rather than error handling: Group and
    Team can own background actors even after a foreground response is emitted.
    Calling it in ``finally`` makes interruption safe and leaves the runner in
    a resumable, persisted state.
    """

    # Enable logs only for an executed demonstration, never while importing it.
    # basicConfig respects an embedding application's existing logging handlers.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    writer(f"Workspace: {workspace}")
    writer(f"Task: {task}")
    with _showcase_environment(workspace):
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
            # Runner owns a detached root declaration. Replace its fields before lazy
            # acquisition; reusable worker declarations retain their own model policy.
            overrides = {
                key: value
                for key, value in {
                    "model_config_name": args.model,
                    "model_effort": args.model_effort,
                    "agent_type": args.agent_type,
                }.items()
                if value is not None
            }
            if overrides:
                runner.root_config = replace(runner.root_config, **overrides)
            # Configure runtime-only dependencies after create/resume and before
            # the first request. Keep the hook inside try so configuration errors
            # also release the Runner and its workers.
            if configure_runner is not None:
                configure_runner(runner)
            return stream_runner(runner, task, writer=writer)
        finally:
            runner.stop()
            logger.info("Showcase Runner 已回收: runner_id=%s", runner.runner_id)


__all__ = [
    "add_runtime_arguments",
    "console_ask",
    "create_or_resume_runner",
    "render_event",
    "run_showcase",
    "showcase_workspace",
    "stream_runner",
]
