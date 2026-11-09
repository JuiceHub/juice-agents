"""Execute due scheduled prompts in independent Runner conversations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .tasks import fire_due_cron_tasks

if TYPE_CHECKING:
    from ..runner.config import RunnerConfig
    from ..runner.types.ask import AskHandler

logger = logging.getLogger(__name__)


def execute_due_cron_tasks(
    workspace_dir: str | Path,
    *,
    now: float | None = None,
    permission_mode: str = "default",
    runner_config: RunnerConfig | str | None = None,
    runtime_config_path: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
    ask_handler: AskHandler | None = None,
) -> list[dict[str, Any]]:
    """Claim due tasks and execute each prompt in a fresh, closed Runner.

    The caller owns polling; this function never starts a background scheduler.
    Claiming retains ``fire_due_cron_tasks`` semantics: one-shot tasks are
    removed and recurring tasks advance even if Runner creation or execution
    fails. Failures are returned per task so later claimed tasks still run.
    """

    # Runner also loads the cron tools. Import only when executing so importing
    # the task store does not create a Runner / tool registry import cycle.
    from ..runner import Runner

    workspace = Path(workspace_dir).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace must be an existing directory: {workspace}")

    results: list[dict[str, Any]] = []
    for task in fire_due_cron_tasks(workspace, now=now):
        runner = None
        result: dict[str, Any] = {"task": task, "runner_id": None}
        try:
            runner = Runner.create(
                base_dir=workspace,
                permission_mode=permission_mode,
                runner_config=runner_config,
                runtime_config_path=runtime_config_path,
                runtime_overrides=runtime_overrides,
            )
            result["runner_id"] = runner.runner_id
            # Interactive callbacks belong to this process and cannot be
            # persisted with a scheduled prompt or Runner manifest.
            runner.set_ask_handler(ask_handler)
            logger.info(
                "cron_task_started task_id=%s runner_id=%s",
                task["id"], runner.runner_id,
            )
            output = runner.run(task["prompt"])
            result.update(status="completed", output=output)
            logger.info(
                "cron_task_completed task_id=%s runner_id=%s",
                task["id"], runner.runner_id,
            )
        except Exception as exc:
            # All due tasks have already been claimed. Isolate failures here
            # so one broken configuration cannot silently discard the rest.
            from ..agent.contracts import RoundExecutionError

            if isinstance(exc, RoundExecutionError):
                # A cancelled or failed round can contain useful partial
                # output. Preserve its real result instead of reporting the
                # exception's display text as a successful scheduled answer.
                result.update(status=exc.result.status, error=str(exc),
                              output=exc.result.output, result=exc.result.to_dict())
            else:
                result.update(status="failed", error=str(exc))
            logger.exception(
                "cron_task_failed task_id=%s runner_id=%s",
                task["id"], result["runner_id"],
            )
        finally:
            if runner is not None:
                try:
                    runner.close()
                except Exception as exc:
                    # Preserve the execution outcome while making a failed
                    # cleanup visible, including when execution already failed.
                    result["cleanup_error"] = str(exc)
                    logger.exception(
                        "cron_runner_cleanup_failed task_id=%s runner_id=%s",
                        task["id"], result["runner_id"],
                    )
        results.append(result)
    return results


__all__ = ["execute_due_cron_tasks"]
