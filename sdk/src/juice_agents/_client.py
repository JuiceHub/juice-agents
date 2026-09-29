"""High-level, workspace-bound Juice SDK client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core.cron import (
    create_cron_task,
    cron_status,
    delete_cron_task,
    fire_due_cron_tasks,
    list_cron_tasks,
)
from .core.prebuilt.factory import (
    create_prebuilt_runner,
    resume_prebuilt_runner,
)
from .core.runner import Runner
from .core.runner.config import RunnerConfig
from .core.runner.types.ask import AskHandler


class RunnerManager:
    """Create and restore Runner conversations inside one workspace."""

    def __init__(
        self,
        *,
        workspace: Path,
        runtime_config_path: str | Path | None,
        ask_handler: AskHandler | None,
    ) -> None:
        self.workspace = workspace
        self.runtime_config_path = runtime_config_path
        self.ask_handler = ask_handler

    def _bind(
        self,
        runner: Runner,
        *,
        runtime_config_path: str | Path | None = None,
    ) -> Runner:
        # Ask providers are process-local UI callbacks and therefore are
        # intentionally rebound rather than persisted in the Runner manifest.
        runner.set_ask_handler(self.ask_handler)

        return runner

    def create(
        self,
        *,
        permission_mode: str = "default",
        runner_config: RunnerConfig | str | None = None,
        **options: Any,
    ) -> Runner:
        """Create a new Runner with a fresh, empty root conversation."""

        runtime_config_path = options.pop(
            "runtime_config_path", self.runtime_config_path
        )
        team_name = options.pop("team_name", None)
        if options:
            raise TypeError("RunnerManager.create 不接受旧 Agent 注入参数: " + ", ".join(sorted(options)))
        return self._bind(
            create_prebuilt_runner(
                permission_mode=permission_mode,
                runner_config=runner_config,
                base_dir=self.workspace,
                team_name=team_name,
                runtime_config_path=runtime_config_path,
            ),
            runtime_config_path=runtime_config_path,
        )

    def resume(
        self,
        *,
        runner_id: str,
        **options: Any,
    ) -> Runner:
        """Restore one Runner manifest, actor session, and async state."""

        runtime_config_path = options.pop(
            "runtime_config_path", self.runtime_config_path
        )
        if options:
            raise TypeError("RunnerManager.resume 不接受旧 Agent 注入参数: " + ", ".join(sorted(options)))
        return self._bind(
            resume_prebuilt_runner(
                runner_id=runner_id,
                base_dir=self.workspace,
                runtime_config_path=runtime_config_path,
            ),
            runtime_config_path=runtime_config_path,
        )


class CronManager:
    """Workspace schedule primitives; the UI process owns the wall clock."""

    def __init__(self, *, workspace: Path, runners: RunnerManager) -> None:
        self.workspace = workspace
        self.runners = runners

    def create(self, *, cron: str, prompt: str, recurring: bool = True) -> dict[str, Any]:
        return create_cron_task(self.workspace, cron, prompt, recurring)

    def list(self) -> list[dict[str, Any]]:
        return list_cron_tasks(self.workspace)

    def delete(self, *, task_id: str) -> dict[str, Any]:
        return delete_cron_task(self.workspace, task_id)

    def status(self) -> dict[str, Any]:
        return cron_status(self.workspace)

    def due(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Atomically claim and return tasks due at ``now``."""

        return fire_due_cron_tasks(self.workspace, now=now)

    def tick(
        self,
        *,
        now: float | None = None,
        permission_mode: str = "default",
        runner_config: RunnerConfig | str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute every claimed task in a new Runner conversation."""

        results: list[dict[str, Any]] = []
        for task in self.due(now=now):
            runner = self.runners.create(
                permission_mode=permission_mode,
                runner_config=runner_config,
            )
            try:
                output = runner.run(str(task.get("prompt") or ""))
                results.append(
                    {
                        "task": task,
                        "runner_id": runner.runner_id,
                        "status": "completed",
                        "output": output,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "task": task,
                        "runner_id": runner.runner_id,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        return results


class Juice:
    """Workspace-bound entry point for the Juice Agents SDK."""

    def __init__(
        self,
        *,
        workspace: str | Path = ".",
        runtime_config_path: str | Path | None = None,
        ask_handler: AskHandler | None = None,
    ) -> None:
        resolved_workspace = Path(workspace).expanduser().resolve()
        if not resolved_workspace.exists() or not resolved_workspace.is_dir():
            raise ValueError(f"workspace must be an existing directory: {resolved_workspace}")
        self.workspace = resolved_workspace
        self.runners = RunnerManager(
            workspace=resolved_workspace,
            runtime_config_path=runtime_config_path,
            ask_handler=ask_handler,
        )
        self.cron = CronManager(workspace=resolved_workspace, runners=self.runners)


__all__ = ["CronManager", "Juice", "RunnerManager"]
