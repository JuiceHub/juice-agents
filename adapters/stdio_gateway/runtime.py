"""Runtime adapter boundary that exposes juice_agents.core through stdio JSON-RPC."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from juice_agents import Juice
from juice_agents.core.runner.types.ask import AskHandler
from juice_agents.core.registry.plugins import PluginRegistry
from juice_agents.core.registry.skills import SkillRegistry
from juice_agents.core.config.model_catalog import (
    DEFAULT_RUNTIME_CONFIG_PATH as CORE_DEFAULT_RUNTIME_CONFIG_PATH,
    DEFAULT_RUNTIME_MODEL_NAME,
    list_runtime_models,
    normalize_runtime_model_effort,
    validate_runtime_model_effort,
)
from juice_agents.core.runner.types.definitions import normalize_agent_mode, normalize_permission_mode
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry import AgentRegistry
from juice_agents.core.registry.agents.types import normalize_agent_type
from juice_agents.core.runner import Runner, RunnerBusyError, mode_registry
from juice_agents.core.memory.config import get_memory_config, set_workspace_memory_feature
from juice_agents.core.memory.store import MemoryStore
from juice_agents.core.config.runtime_config import read_runtime_config, update_workspace_config
from juice_agents.core.managers import WorkflowRunManager
from juice_agents.core.permissions import get_permission_status
from juice_agents.core.registry.workflows import WorkflowRegistry
from juice_agents.core.cron import (
    create_cron_task,
    cron_status,
    delete_cron_task,
    fire_due_cron_tasks,
    list_cron_tasks,
)

if TYPE_CHECKING:
    from juice_agents.core.models.reward_models import BaseRewardModel, GenerativeRewardModel


logger = logging.getLogger(__name__)
DEFAULT_RUNTIME_CONFIG_PATH = CORE_DEFAULT_RUNTIME_CONFIG_PATH


@dataclass(frozen=True)
class SessionStatus:
    """Serializable snapshot returned to the CLI after each gateway operation."""

    runner_id: str
    permission_mode: str
    agent_mode: str
    root_agent_name: str
    base_dir: Path
    started: bool
    resumed: bool
    agent_type: str = "react"
    model_name: str = "unknown-model"
    model_effort: str = "disabled"
    backend: str = "unknown-backend"
    provider_model_name: str = "unknown-model"
    goal: dict[str, Any] | None = None
    worktree: dict[str, Any] | None = None


class DirectRunnerRuntime:
    """Thin adapter that keeps protocol concerns out of juice_agents.core itself."""

    def __init__(
        self,
        *,
        base_dir: str | Path,
        runtime_config_path: str | Path | None = None,
        model_name: str | None = None,
        model_effort: str | None = None,
        permission_mode: str | None = None,
        agent_mode: str | None = None,
        agent_type: str | None = None,
        ask_handler: AskHandler | None = None,
        worktree: str | None = None,
    ) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.runtime_config_path = (
            Path(runtime_config_path).expanduser().resolve() if runtime_config_path else DEFAULT_RUNTIME_CONFIG_PATH.resolve()
        )
        if not self.base_dir.is_dir():
            raise ValueError(f"Workspace is missing or is not a directory: {self.base_dir}")
        effective = ConfigurationContext.from_workspace(
            self.base_dir, project_config_path=self.runtime_config_path
        ).read_merged_config().get("runtime", {})
        self.model_name = str(model_name or effective.get("model_name") or DEFAULT_RUNTIME_MODEL_NAME).strip()
        self.model_effort = normalize_runtime_model_effort(
            model_effort if model_effort is not None else effective.get("model_effort")
        )
        # 两个维度互不约束：plan 只是一种 agent_mode，不再限制权限模式。
        self.permission_mode = normalize_permission_mode(permission_mode or effective.get("permission_mode") or "default")
        self.agent_mode = normalize_agent_mode(agent_mode or effective.get("agent_mode") or "agent")
        self.agent_type = normalize_agent_type(agent_type or effective.get("agent_type") or "react")
        # Keep construction read-only. `/agents`, `/teams` and model queries
        # may create this adapter solely for a cold workspace lookup; runtime
        # launch selections stay in memory even after a Runner starts.
        self._ask_handler = ask_handler
        self.worktree = None if worktree is None else str(worktree)
        self._juice = Juice(
            workspace=self.base_dir,
            runtime_config_path=self.runtime_config_path,
            ask_handler=ask_handler,
        )
        self._runner: Any | None = None
        # Strategy configuration is transport-local. In particular, reward
        # model objects must never be written into the workspace configuration.
        self._strategy: Any | None = None
        self._strategy_params: dict[str, Any] | None = None
        # `/rm` owns this choice independently of breadth. A linear strategy
        # must not discard the model that a later breadth change will use.
        self._reward_model: GenerativeRewardModel | None = None
        self._started = False
        self._resumed = False
        self._mode_before_plan = "agent"
        # Queued cancellation stays transport-local; active cancellation is
        # forwarded to Runner.request_stop_stream().
        self._cancel_requested = threading.Event()

    def _install_ask_handler(self) -> None:
        """把 adapter 提供的 ask handler 绑定到当前 Runner。"""

        if self._runner is not None and hasattr(self._runner, "set_ask_handler"):
            self._runner.set_ask_handler(self._ask_handler)

    def start_session(self) -> SessionStatus:
        """Create a new runner for the requested permission mode and agent mode."""
        if self.worktree is not None:
            raise NotImplementedError(self._worktree_message())
        logger.info(
            "创建新的 Juice 会话: base_dir=%s permission_mode=%s agent_mode=%s",
            self.base_dir,
            self.permission_mode,
            self.agent_mode,
        )
        if self._runner is not None:
            self._close_runner(reason="runner_switch")
        self._runner = self._create_declarative_runner()
        self._install_strategy_handler()
        self._install_ask_handler()
        self._started = False
        self._resumed = False
        return self.describe_session()

    def _runtime_overrides(self) -> dict[str, Any]:
        """Keep launch selections process-local instead of copying inherited defaults."""
        return {"runtime": {
            "permission_mode": self.permission_mode,
            "agent_mode": self.agent_mode,
            "agent_type": self.agent_type,
            "model_name": self.model_name,
            "model_effort": self.model_effort,
        }}

    def effective_config(self) -> dict[str, Any]:
        """Return current configuration through the same context used by execution."""
        return ConfigurationContext.from_workspace(
            self.base_dir,
            project_config_path=self.runtime_config_path,
            overrides=self._runtime_overrides(),
        ).read_merged_config()

    def _create_declarative_runner(self) -> Runner:
        """Create one Runner from a serializable configuration template only."""

        return self._juice.runners.create(
            permission_mode=self.permission_mode,
            runner_config=mode_registry.resolve(self.agent_mode),
            runtime_config_path=self.runtime_config_path,
            runtime_overrides=self._runtime_overrides(),
        )

    def _close_runner(self, *, reason: str) -> None:
        """Release manager-owned resources before replacing a Runner.

        Configuration changes alter the static declarations used by a fresh
        AgentManager acquisition.  They cannot mutate a live Agent in place,
        so the gateway closes the previous manager composition and constructs
        a new declarative Runner.
        """

        if self._runner is None:
            return
        close = getattr(self._runner, "close", None)
        if callable(close):
            close()
            return
        stop = getattr(self._runner, "stop", None)
        if callable(stop):
            stop(reason=reason)

    def _rebuild_live_runner(self, *, reason: str) -> None:
        """Recreate a live Runner after a static workspace setting changed."""

        if self._runner is None:
            return
        self._close_runner(reason=reason)
        self._runner = self._create_declarative_runner()
        self._install_strategy_handler()
        self._install_ask_handler()
        self._started = True
        self._resumed = False
        logger.info(
            "gateway_runner_rebuilt reason=%s runner_id=%s mode_id=%s",
            reason,
            self._runner.runner_id,
            self.agent_mode,
        )

    def _preview_session_status(self) -> SessionStatus:
        """Expose runtime choices before a live runner is created."""

        runtime_model = self._resolve_runtime_model_metadata()
        return SessionStatus(
            runner_id="",
            permission_mode=self.permission_mode,
            agent_mode=self.agent_mode,
            root_agent_name=mode_registry.resolve(self.agent_mode).root_binding.agent_name,
            base_dir=self.base_dir,
            started=False,
            resumed=False,
            agent_type=self.agent_type,
            model_name=self.model_name or "unknown-model",
            model_effort=self.model_effort,
            backend=str(runtime_model.get("backend") or "unknown-backend"),
            provider_model_name=self._resolve_provider_model_name(runtime_model),
            goal=None,
            worktree=None,
        )

    def switch_permission_mode(self, mode: str) -> SessionStatus:
        """切换权限模式（default/accept）。

        Permission is rebound at an idle Runner boundary.  The root snapshot
        remains in the same Runner namespace, so this does not create a new
        conversation just to change approval policy.
        """

        normalized_mode = normalize_permission_mode(mode)
        if self._runner is None:
            self.permission_mode = normalized_mode
            return self._preview_session_status()
        self._runner.reconfigure(permission_mode=normalized_mode)
        self.permission_mode = normalized_mode
        return self.describe_session()

    def switch_agent_mode(
        self,
        agent_mode: str,
        agent_type: str | None = None,
        model_name: str | None = None,
        model_effort: str | None = None,
    ) -> SessionStatus:
        """切换 Agent 执行模式（agent/plan/team/group）。

        Each mode is a static RunnerConfig template.  A live switch retains
        the Runner id and releases/reacquires root only at an idle boundary.
        """

        normalized_agent_mode = normalize_agent_mode(agent_mode)
        # A mode-only RPC leaves the current action protocol unchanged. The
        # current value already includes configuration inheritance and any
        # explicit launch/session choice, so do not reapply a react default.
        normalized_agent_type = normalize_agent_type(self.agent_type if agent_type is None else agent_type)
        prospective_model_name = self.model_name
        prospective_model_effort = self.model_effort
        if str(model_name or "").strip():
            prospective_model_name = str(model_name or "").strip()
        if model_effort is not None:
            prospective_model_effort = normalize_runtime_model_effort(model_effort)
        if self._runner is None:
            if normalized_agent_mode == "plan" and self.agent_mode != "plan":
                self._mode_before_plan = self.agent_mode
            self.agent_mode = normalized_agent_mode
            self.agent_type = normalized_agent_type
            self.model_name = prospective_model_name
            self.model_effort = prospective_model_effort
            return self._preview_session_status()
        return_mode = self.agent_mode if normalized_agent_mode == "plan" and self.agent_mode != "plan" else None
        # Validate and apply on Runner before changing adapter fields.  On a
        # busy/error result the old mode remains completely intact.
        overrides = self._runtime_overrides()
        overrides["runtime"].update(
            agent_mode=normalized_agent_mode,
            agent_type=normalized_agent_type,
            model_name=prospective_model_name,
            model_effort=prospective_model_effort,
        )
        self._runner.reconfigure(
            mode_id=normalized_agent_mode,
            plan_return_mode=return_mode,
            runtime_overrides=overrides,
        )
        if return_mode is not None:
            self._mode_before_plan = return_mode
        self.agent_mode = normalized_agent_mode
        self.agent_type = normalized_agent_type
        self.model_name = prospective_model_name
        self.model_effort = prospective_model_effort
        return self.describe_session()

    def enter_plan(self) -> SessionStatus:
        """进入 plan 执行模式；权限模式保持不变。"""

        return self.switch_agent_mode("plan", agent_type=self.agent_type)

    def resume_session(self, runner_id: str) -> SessionStatus:
        """Reconnect to an existing persisted runner from the workspace store."""
        logger.info("恢复 Juice 会话: base_dir=%s runner_id=%s", self.base_dir, runner_id)
        if self._runner is not None:
            self._close_runner(reason="runner_switch")
        try:
            self._runner = self._juice.runners.resume(
                runner_id=runner_id,
                runtime_config_path=self.runtime_config_path,
                runtime_overrides=self._runtime_overrides(),
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"runner not found: {runner_id}") from exc
        except ValueError as exc:
            raise ValueError(f"failed to resume runner {runner_id}: {exc}") from exc
        self._started = True
        self._resumed = True
        self._install_strategy_handler()
        self._install_ask_handler()
        self.permission_mode = str(
            getattr(self._runner, "permission_mode", self.permission_mode) or self.permission_mode
        )
        self.agent_mode = str(getattr(self._runner, "agent_mode", self.agent_mode) or self.agent_mode)
        self._mode_before_plan = str(
            getattr(self._runner, "state", {}).get("plan_return_mode") or "agent"
        )
        self._apply_approved_plan_exit()
        return self.describe_session()

    def _resolve_live_root_agent_type(self) -> str:
        if self._runner is None:
            return self.agent_type
        try:
            root = self._runner.agent_manager.get(self._runner.root_agent_id).instance
        except Exception:
            return self.agent_type
        resolved_type = getattr(root, "_resolved_agent_type", None)
        # Test doubles and third-party agents may synthesize arbitrary
        # attributes (for example ``MagicMock``). Only accept a concrete,
        # supported protocol marker written by AgentRegistry.
        if isinstance(resolved_type, str) and resolved_type.strip().lower() in {"react", "codeact"}:
            return normalize_agent_type(resolved_type)
        class_name = type(root).__name__.lower()
        return "codeact" if "codeact" in class_name else self.agent_type

    def stream_message(
        self,
        task: str,
        *,
        agent_mode_override: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterator[Any]:
        """Send a task to the active runner and yield raw action steps back to the caller."""
        if self._runner is None:
            self.start_session()
        # 每次新一轮 stream 入口清空 cancel 标志，避免上一轮的取消影响新会话。
        self._cancel_requested.clear()
        if cancel_check is not None and cancel_check():
            return

        # A temporary mode returns only after its stream has reached an idle
        # boundary.  Do not rebuild in ``finally``: cancellation or a tool
        # failure must leave the durable Runner available for inspection.
        override_mode = (
            None if agent_mode_override is None else normalize_agent_mode(agent_mode_override)
        )
        if override_mode is not None and override_mode != self.agent_mode:
            original_agent_mode = self.agent_mode
            original_agent_type = self.agent_type
            self.switch_agent_mode(override_mode, agent_type=self.agent_type)
            yield from self.stream_message(task, cancel_check=cancel_check)
            self.switch_agent_mode(original_agent_mode, agent_type=original_agent_type)
            return

        # The SDK owns capability parsing, goal continuation, and async-task
        # notification wakeups. This adapter only forwards events.
        for event in self._runner.stream(task):
            yield event
        self._started = True
        self._apply_approved_plan_exit()

    def _apply_approved_plan_exit(self) -> bool:
        """Apply an approved plan exit only after the stream has settled."""

        if self._runner is None or str(getattr(self._runner, "mode_id", "")) != "plan":
            return False
        approved = self._runner.pending_plan_exit()
        if approved is None:
            return False
        target = str(
            getattr(self._runner, "state", {}).get("plan_return_mode")
            or self._mode_before_plan
            or "agent"
        ).strip()
        try:
            self._runner.reconfigure(mode_id=target)
        except RunnerBusyError as exc:
            # Keep the durable approval pending while any read-only worker or
            # asynchronous investigation is still active. Never cancel work
            # simply to complete a mode transition.
            logger.info("plan_exit_deferred runner_id=%s reason=%s", self._runner.runner_id, exc)
            return False
        self.agent_mode = target
        logger.info(
            "plan_exit_applied runner_id=%s target_mode=%s plan_file=%s",
            self._runner.runner_id,
            target,
            approved.get("plan_file", ""),
        )
        return True

    def describe_session(self) -> SessionStatus:
        """Expose the active runner state in a transport-friendly snapshot."""
        if self._runner is None:
            raise RuntimeError("当前还没有活动会话")
        runtime_model = self._resolve_runtime_model_metadata()
        runner_permission_mode = str(
            getattr(self._runner, "permission_mode", self.permission_mode) or self.permission_mode
        )
        if runner_permission_mode not in {"default", "accept"}:
            runner_permission_mode = self.permission_mode
        runner_agent_mode = str(getattr(self._runner, "agent_mode", self.agent_mode) or self.agent_mode)
        if runner_agent_mode not in mode_registry.configs():
            runner_agent_mode = self.agent_mode
        return SessionStatus(
            runner_id=str(getattr(self._runner, "runner_id", "") or ""),
            permission_mode=runner_permission_mode,
            agent_mode=runner_agent_mode,
            root_agent_name=str(
                getattr(self._runner, "root_agent_name", "root_agent") or "root_agent"
            ),
            base_dir=self.base_dir,
            started=self._started,
            resumed=self._resumed,
            agent_type=self._resolve_live_root_agent_type(),
            model_name=self.model_name or "unknown-model",
            model_effort=self.model_effort,
            backend=str(runtime_model.get("backend") or "unknown-backend"),
            provider_model_name=self._resolve_provider_model_name(runtime_model),
            goal=self.get_goal(),
            worktree=self.worktree_status(),
        )

    @staticmethod
    def _worktree_message() -> str:
        return "Managed worktrees are not implemented. Use git worktree add, then juice --workspace <directory>."

    def enter_worktree(self, name: str | None = None) -> dict[str, Any]:
        raise NotImplementedError(self._worktree_message())

    def exit_worktree(self, *, discard: bool = False) -> dict[str, Any]:
        raise NotImplementedError(self._worktree_message())

    def list_worktrees(self) -> dict[str, Any]:
        raise NotImplementedError(self._worktree_message())

    def worktree_status(self, name: str | None = None) -> dict[str, Any]:
        if self._runner is None:
            return {}
        status = getattr(self._runner, "worktree_status", None)
        return {} if not callable(status) else dict(status(name))

    def set_goal(
        self,
        objective: str,
        *,
        max_turns: int | None = None,
        max_runtime_seconds: int | float | None = None,
    ) -> dict[str, Any]:
        """Set the active runner goal and return its summary."""

        if self._runner is None:
            self.start_session()
        if self._runner is None:
            raise RuntimeError("No active session")
        if str(getattr(self._runner, "agent_mode", self.agent_mode) or "") == "plan":
            raise ValueError("goal cannot start in plan mode; exit plan mode first")
        self._runner.set_goal(
            objective,
            max_turns=max_turns,
            max_runtime_seconds=max_runtime_seconds,
        )
        return self.get_goal()

    def get_goal(self) -> dict[str, Any]:
        """Return the active/current goal summary."""

        if self._runner is None:
            return {}
        summary = getattr(self._runner, "goal_summary", None)
        return {} if not callable(summary) else dict(summary() or {})

    def pause_goal(self) -> dict[str, Any]:
        if self._runner is None:
            raise RuntimeError("No active session")
        return dict(self._runner.pause_goal(reason="user_paused") or {})

    def resume_goal(self) -> dict[str, Any]:
        if self._runner is None:
            raise RuntimeError("No active session")
        self._runner.resume_goal()
        return self.get_goal()

    def clear_goal(self) -> dict[str, Any]:
        if self._runner is None:
            raise RuntimeError("No active session")
        return dict(self._runner.clear_goal())

    def list_models(self) -> list[dict[str, Any]]:
        """Return runtime model catalog entries for CLI selection."""
        return list_runtime_models(
            runtime_config_path=self.runtime_config_path,
            workspace_dir=self.base_dir,
        )

    def list_available_agents(
        self,
        *,
        name: str | None = None,
        mode_id: str | None = None,
    ) -> dict[str, Any]:
        """Return declarations usable by this Runner or a cold workspace query.

        The Registry owns declaration filtering. Passing the live Runner lets it
        also apply runner-scoped disables without constructing a new Runner for
        a read-only `/agents` request.
        """

        selected_mode = str(
            mode_id
            or getattr(self._runner, "agent_mode", None)
            or self.agent_mode
            or "agent"
        ).strip()
        registry = AgentRegistry(
            config_context=ConfigurationContext.from_workspace(
                self.base_dir,
                project_config_path=self.runtime_config_path,
            )
        )
        return registry.list_available_agents(
            name=name,
            mode_id=selected_mode,
            runner=self._runner,
        )

    def _image_generation_enabled(self) -> bool:
        """Read merged image.enabled for catalog and live runner reconfiguration."""

        try:
            payload = read_runtime_config(self.runtime_config_path, workspace_dir=self.base_dir)
        except Exception:
            return False
        raw_image = payload.get("image") if isinstance(payload, dict) else {}
        if not isinstance(raw_image, dict):
            return False
        return bool(raw_image.get("enabled", False))

    def list_async_tasks(self, *, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        """Return async task snapshots from the active runner."""
        if self._runner is None:
            raise RuntimeError("No active session")
        return [
            dict(item)
            for item in self._runner.list_async_tasks(statuses=statuses)
        ]

    def read_async_task_output(
        self,
        *,
        async_task_id: str,
        max_lines: int = 200,
    ) -> dict[str, Any]:
        """读取单个 async task 的 stdout/stderr 末尾 N 行，返回 metadata + tail。"""
        if self._runner is None:
            raise RuntimeError("No active session")
        task = self._runner.get_async_task(async_task_id)
        if task is None:
            raise ValueError(f"async_task_id 不存在: {async_task_id}")
        output_dir = str(task.get("output_dir") or "").strip()
        payload = self._runner.async_task_manager.read_output(async_task_id)
        return {
            "async_task_id": str(task.get("async_task_id") or ""),
            "output_dir": output_dir,
            "status": payload["status"],
            "events": payload["events"],
            "latest_result": payload["latest_result"],
            "stdout_tail": "\n".join(str(payload["stdout"]).splitlines()[-max_lines:]),
            "stderr_tail": "\n".join(str(payload["stderr"]).splitlines()[-max_lines:]),
            "max_lines": int(max_lines),
        }

    def describe_agent_sessions(self) -> dict[str, Any]:
        """Return Manager-owned Agent transcript snapshots from the active runner."""
        if self._runner is None:
            raise RuntimeError("No active session")
        return dict(self._runner.describe_agent_sessions())

    def send_agent_message(self, *, agent_name: str, message: str) -> dict[str, Any]:
        """Queue a user control message for an acquired Agent."""
        if self._runner is None:
            raise RuntimeError("No active session")
        return dict(
            self._runner.queue_agent_user_message(
                agent_name=agent_name,
                message=message,
            )
        )

    def interrupt_agent(self, *, agent_name: str, reason: str = "user_agent_interrupt") -> dict[str, Any]:
        """Request cooperative interruption for one Agent without cancelling the root stream."""
        if self._runner is None:
            raise RuntimeError("No active session")
        return dict(self._runner.interrupt_agent(agent_name=agent_name, reason=reason))

    def memory_status(self) -> dict[str, Any]:
        """Return workspace-local memory status without requiring a model call."""
        config = get_memory_config(
            runtime_config_path=self.runtime_config_path,
            workspace_dir=self.base_dir,
        )
        store = MemoryStore.for_workspace(self.base_dir)
        runner_id = "" if self._runner is None else str(self._runner.runner_id)
        return store.status(
            enabled=config.enabled,
            dream_enabled=config.dream.enabled,
            dream_config=config.dream,
            runner_id=runner_id,
        )

    def memory_search(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        """Search topic memory files."""
        config = get_memory_config(
            runtime_config_path=self.runtime_config_path,
            workspace_dir=self.base_dir,
        )
        if not config.enabled:
            return {"enabled": False, "hits": [], "error": "memory disabled by config"}
        return {"enabled": True, "hits": MemoryStore.for_workspace(self.base_dir).search(query, limit=limit)}

    def memory_view(self, path: str | None = None) -> dict[str, Any]:
        """Read MEMORY.md or a memory-relative file."""
        config = get_memory_config(
            runtime_config_path=self.runtime_config_path,
            workspace_dir=self.base_dir,
        )
        if not config.enabled:
            return {"enabled": False, "path": path or "MEMORY.md", "content": "", "error": "memory disabled by config"}
        store = MemoryStore.for_workspace(self.base_dir)
        resolved = store._resolve(path)
        return {"enabled": True, "path": store._relative(resolved), "content": store.read(path)}

    def create_cron_task(self, *, cron: str, prompt: str, recurring: bool = True) -> dict[str, Any]:
        """Create a workspace-local scheduled prompt."""

        logger.info("创建 cron 定时任务: base_dir=%s cron=%s", self.base_dir, cron)
        return create_cron_task(self.base_dir, cron=cron, prompt=prompt, recurring=recurring)

    def list_cron_tasks(self) -> dict[str, Any]:
        """List workspace-local scheduled prompts."""

        return {"tasks": list_cron_tasks(self.base_dir)}

    def delete_cron_task(self, task_id: str) -> dict[str, Any]:
        """Delete a workspace-local scheduled prompt."""

        return delete_cron_task(self.base_dir, task_id)

    def fire_due_cron_tasks(self, *, now: float | None = None) -> dict[str, Any]:
        """Mark due cron tasks fired and return prompts for the CLI queue."""

        return {"fired": fire_due_cron_tasks(self.base_dir, now=now)}

    def cron_status(self) -> dict[str, Any]:
        """Return a workspace-local cron summary."""

        return cron_status(self.base_dir)

    def set_memory_config(self, *, feature: str, enabled: bool) -> dict[str, Any]:
        """Persist a workspace YAML memory override and reload current root agent."""
        status = set_workspace_memory_feature(
            workspace_dir=self.base_dir,
            runtime_config_path=self.runtime_config_path,
            feature=feature,
            enabled=enabled,
        )
        logger.info(
            "更新 workspace memory 配置: base_dir=%s feature=%s enabled=%s",
            self.base_dir,
            feature,
            enabled,
        )
        if self._runner is not None:
            self._rebuild_live_runner(reason="memory_config_changed")
        return MemoryStore.for_workspace(self.base_dir).status(
            enabled=status.enabled,
            dream_enabled=status.dream.enabled,
            dream_config=status.dream,
            runner_id="" if self._runner is None else str(self._runner.runner_id),
        )

    def set_browser_config(self, *, enabled: bool) -> dict[str, Any]:
        """Persist browser tool visibility and reload the active prebuilt runner."""

        normalized_enabled = bool(enabled)

        def _update(current: dict[str, Any]) -> dict[str, Any]:
            raw_browser = current.get("browser")
            browser = dict(raw_browser) if isinstance(raw_browser, dict) else {}
            browser["enabled"] = normalized_enabled
            current["browser"] = browser
            return current

        update_workspace_config(self.base_dir, _update)
        logger.info(
            "更新 workspace browser 配置: base_dir=%s enabled=%s",
            self.base_dir,
            normalized_enabled,
        )
        if self._runner is not None:
            self._rebuild_live_runner(reason="browser_config_changed")
            logger.info(
                "browser 配置变更后已热重装 runner: runner_id=%s enabled=%s",
                getattr(self._runner, "runner_id", ""),
                normalized_enabled,
            )
        return {
            "enabled": normalized_enabled,
            "runner_id": "" if self._runner is None else str(getattr(self._runner, "runner_id", "") or ""),
        }

    def set_image_config(self, *, enabled: bool) -> dict[str, Any]:
        """Persist image generation tool visibility and reload the active prebuilt runner."""

        normalized_enabled = bool(enabled)

        def _update(current: dict[str, Any]) -> dict[str, Any]:
            raw_image = current.get("image")
            image = dict(raw_image) if isinstance(raw_image, dict) else {}
            image["enabled"] = normalized_enabled
            current["image"] = image
            return current

        update_workspace_config(self.base_dir, _update)
        logger.info(
            "更新 workspace image 配置: base_dir=%s enabled=%s",
            self.base_dir,
            normalized_enabled,
        )
        if self._runner is not None:
            self._rebuild_live_runner(reason="image_config_changed")
            logger.info(
                "image 配置变更后已热重装 runner: runner_id=%s enabled=%s",
                getattr(self._runner, "runner_id", ""),
                normalized_enabled,
            )
        return {
            "enabled": normalized_enabled,
            "runner_id": "" if self._runner is None else str(getattr(self._runner, "runner_id", "") or ""),
        }

    def _skill_registry(self) -> SkillRegistry:
        return SkillRegistry(
            local_dir=self.base_dir / ".juice" / "skills",
            config_path=self.runtime_config_path,
            workspace_dir=self.base_dir,
            available_tools=self._current_root_tool_names(),
        )

    def _plugin_registry(self) -> PluginRegistry:
        """Build a workspace-scoped registry without creating a live Runner."""

        context = ConfigurationContext.from_workspace(
            self.base_dir,
            project_config_path=self.runtime_config_path,
        )
        return PluginRegistry(
            workspace_dir=self.base_dir,
            project_dir=self.base_dir,
            config_context=context,
        )

    def list_plugins(self) -> dict[str, Any]:
        """List enabled plugins and non-fatal discovery diagnostics."""

        registry = self._plugin_registry()
        plugins = [record.to_dict() for record in registry.list()]
        return {
            "success": True,
            "plugins": plugins,
            "diagnostics": registry.diagnostics(),
            "count": len(plugins),
            "hint": "Use view_plugin(name) to inspect manifest metadata or a plugin file",
        }

    def view_plugin(self, name: str, *, file_path: str | None = None) -> dict[str, Any]:
        """Read enabled plugin metadata or one workspace-safe text file."""

        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("plugin name 不能为空")
        value = self._plugin_registry().view(normalized_name, file_path=file_path)
        if isinstance(value, str):
            return {
                "success": True,
                "name": normalized_name,
                "file_path": file_path,
                "content": value,
            }
        return {"success": True, "name": normalized_name, "plugin": value}

    def plugins_config_status(self) -> dict[str, Any]:
        """Return workspace plugin enablement state, including disabled plugins."""

        registry = self._plugin_registry()
        plugins = [record.to_dict() for record in registry.list(include_disabled=True)]
        try:
            raw_plugins = read_runtime_config(
                self.runtime_config_path,
                workspace_dir=self.base_dir,
            ).get("plugins", {})
        except (FileNotFoundError, ValueError):
            raw_plugins = {}
        config = dict(raw_plugins) if isinstance(raw_plugins, dict) else {}
        raw_enabled = config.get("enabled", True)
        globally_enabled = raw_enabled if isinstance(raw_enabled, bool) else True
        raw_disabled = config.get("disabled", [])
        disabled = sorted(
            {
                str(item or "").strip()
                for item in (raw_disabled if isinstance(raw_disabled, (list, tuple)) else ())
                if str(item or "").strip()
            }
        )
        return {
            "success": True,
            "enabled": globally_enabled,
            "disabled": disabled,
            "plugins": plugins,
            "diagnostics": registry.diagnostics(),
            "count": len(plugins),
        }

    def set_plugins_config(
        self,
        *,
        enabled: bool | None = None,
        disabled: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Persist workspace plugin visibility and reload the active runner."""

        normalized_disabled = sorted(
            {
                str(item or "").strip()
                for item in tuple(disabled or ())
                if str(item or "").strip()
            }
        )

        def _update(current: dict[str, Any]) -> dict[str, Any]:
            raw_plugins = current.get("plugins")
            plugins = dict(raw_plugins) if isinstance(raw_plugins, dict) else {}
            if enabled is not None:
                plugins["enabled"] = bool(enabled)
            plugins["disabled"] = normalized_disabled
            current["plugins"] = plugins
            return current

        update_workspace_config(self.base_dir, _update)
        logger.info(
            "更新 workspace plugins 配置: base_dir=%s enabled=%s disabled=%s",
            self.base_dir,
            enabled,
            normalized_disabled,
        )
        if self._runner is not None:
            self._rebuild_live_runner(reason="plugins_config_changed")
        return self.plugins_config_status()

    def list_skills(self, *, category: str | None = None) -> dict[str, Any]:
        """List skills visible to the active root agent capabilities."""
        registry = self._skill_registry()
        skills = [meta.to_dict() for meta in registry.list(category=category)]
        return {
            "success": True,
            "skills": skills,
            "categories": registry.categories(),
            "category_descriptions": registry.category_descriptions(),
            "count": len(skills),
            "hint": "Use /skills <name> to see full content and linked files",
        }

    def view_skill(self, name: str, *, file_path: str | None = None) -> dict[str, Any]:
        """Read a visible skill file through the same registry rules as list_skills."""
        return self._skill_registry().view(name, file_path=file_path)

    def skills_config_status(self) -> dict[str, Any]:
        """Return editable skill enablement state for the current workspace."""

        return self._skill_registry().config_status()

    def set_skills_config(
        self,
        *,
        enabled: bool | None = None,
        disabled: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Persist workspace skill visibility and reload the active runner."""

        normalized_disabled = sorted(
            {
                str(item or "").strip().lower()
                for item in tuple(disabled or ())
                if str(item or "").strip()
            }
        )

        def _update(current: dict[str, Any]) -> dict[str, Any]:
            raw_skills = current.get("skills")
            skills = dict(raw_skills) if isinstance(raw_skills, dict) else {}
            if enabled is not None:
                skills["enabled"] = bool(enabled)
            skills["disabled"] = normalized_disabled
            current["skills"] = skills
            return current

        update_workspace_config(self.base_dir, _update)
        logger.info(
            "更新 workspace skills 配置: base_dir=%s enabled=%s disabled=%s",
            self.base_dir,
            enabled,
            normalized_disabled,
        )
        if self._runner is not None:
            self._rebuild_live_runner(reason="skills_config_changed")
        return self.skills_config_status()

    def _automatic_capability_status(self, section: str) -> dict[str, Any]:
        """Return a boolean feature switch with safe default-on semantics."""

        try:
            payload = read_runtime_config(self.runtime_config_path, workspace_dir=self.base_dir)
            raw = payload.get(section, {}) if isinstance(payload, dict) else {}
        except (FileNotFoundError, ValueError):
            raw = {}
        value = raw.get("enabled", True) if isinstance(raw, dict) else True
        return {"success": True, "enabled": value if isinstance(value, bool) else True}

    def _set_automatic_capability(self, *, section: str, enabled: bool) -> dict[str, Any]:
        """Persist a feature switch and reassemble an already live Runner."""

        normalized_enabled = bool(enabled)

        def _update(current: dict[str, Any]) -> dict[str, Any]:
            raw_section = current.get(section)
            section_payload = dict(raw_section) if isinstance(raw_section, dict) else {}
            section_payload["enabled"] = normalized_enabled
            current[section] = section_payload
            return current

        update_workspace_config(self.base_dir, _update)
        logger.info(
            "更新 workspace 自动能力配置: base_dir=%s section=%s enabled=%s",
            self.base_dir,
            section,
            normalized_enabled,
        )
        if self._runner is not None:
            self._rebuild_live_runner(reason=f"{section}_config_changed")
            logger.info("自动能力配置已热重装 runner: section=%s", section)
        return self._automatic_capability_status(section)

    def self_evolution_config_status(self) -> dict[str, Any]:
        return self._automatic_capability_status("self_evolution")

    def set_self_evolution_config(self, *, enabled: bool) -> dict[str, Any]:
        return self._set_automatic_capability(section="self_evolution", enabled=enabled)

    def workflows_config_status(self) -> dict[str, Any]:
        return self._automatic_capability_status("workflows")

    def set_workflows_config(self, *, enabled: bool) -> dict[str, Any]:
        return self._set_automatic_capability(section="workflows", enabled=enabled)

    def switch_model(self, model_name: str, model_effort: str | None = None) -> SessionStatus:
        """Reconfigure the current runner with a new logical runtime model.

        模型切换是热更新：仅更新配置，不重建 runner，保持对话连续性。
        """
        normalized_name = str(model_name or "").strip()
        if not normalized_name:
            raise ValueError("model_name 不能为空")
        normalized_effort = validate_runtime_model_effort(
            model_name=normalized_name,
            model_effort=self.model_effort if model_effort is None else model_effort,
            runtime_config_path=self.runtime_config_path,
            workspace_dir=self.base_dir,
        )
        # Validate the idle execution boundary before publishing any state or
        # project override. The launch layer must follow explicit model edits.
        if self._runner is not None:
            overrides = self._runtime_overrides()
            overrides["runtime"].update(model_name=normalized_name, model_effort=normalized_effort)
            self._runner.reconfigure(runtime_overrides=overrides)
        patch: dict[str, str] = {}
        if normalized_name != self.model_name:
            patch["model_name"] = normalized_name
        if normalized_effort != self.model_effort:
            patch["model_effort"] = normalized_effort
        self.model_name = normalized_name
        self.model_effort = normalized_effort
        if patch:
            update_workspace_config(
                self.base_dir,
                lambda current: {
                    **current,
                    "runtime": {**dict(current.get("runtime") or {}), **patch},
                },
            )
        if self._runner is None:
            return self._preview_session_status()

        # 热更新 runner 的模型配置，不重建整个 runner
        # self._rebuild_live_runner(reason="model_changed")  # 移除：会创建新 runner_id
        logger.info(
            "模型热切换: runner_id=%s model_name=%s model_effort=%s",
            self._runner.runner_id,
            self.model_name,
            self.model_effort,
        )
        return self.describe_session()

    @property
    def pending_strategy_configuration(self) -> dict[str, Any] | None:
        """Carry explicit choices into a replacement runtime in this process."""

        if self._strategy_params is None:
            return None
        return dict(self._strategy_params)

    @property
    def pending_reward_model_configuration(self) -> dict[str, Any] | None:
        """Transfer a logical name, never provider objects or API credentials."""
        if self._reward_model is None:
            return None
        return {"model_name": self._reward_model.model_name}

    def _current_strategy(self) -> Any | None:
        """Inspect the live root without acquiring an Agent or a provider."""
        root = self._resolve_root_agent()
        return getattr(root, "exploration_strategy", None) or self._strategy

    def describe_reward_model(self) -> dict[str, Any]:
        """Report configured scoring separately from whether it is active."""
        strategy = self._current_strategy()
        scorer = self._reward_model or getattr(strategy, "reward_model", None)
        breadth = getattr(strategy, "breadth", 1)
        return {
            "model_name": getattr(self._reward_model, "model_name", None),
            "scorer_configured": scorer is not None,
            "breadth": breadth,
            "execution": "candidate_selection" if breadth > 1 and scorer is not None else "linear",
        }

    def configure_reward_model(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Query, set a catalog model, or clear scoring without changing breadth.

        Catalog validation uses the same workspace and project configuration as
        `/models`. Provider construction and scoring remain lazy; a query or a
        failed update leaves the existing strategy untouched.
        """
        from juice_agents.core.models.reward_models import GenerativeRewardModel

        params = params or {}
        unknown = set(params) - {"model_name", "reset"}
        if unknown:
            raise ValueError("Unknown reward model parameters: " + ", ".join(sorted(unknown)))
        reset = params.get("reset", False)
        if not isinstance(reset, bool):
            raise ValueError("reset must be a boolean")
        if reset and "model_name" in params:
            raise ValueError("reset cannot be combined with model_name")
        if not reset and "model_name" not in params:
            return self.describe_reward_model()

        scorer = None
        if not reset:
            model_name = params["model_name"]
            if not isinstance(model_name, str) or not model_name.strip():
                raise ValueError("model_name must be a non-empty catalog name")
            model_name = model_name.strip()
            if model_name not in {item["model_name"] for item in self.list_models()}:
                raise ValueError(f"Unknown reward model: {model_name}. Use /models to list available models.")
            scorer = GenerativeRewardModel.from_config(
                model_name=model_name,
                config_context=ConfigurationContext.from_workspace(
                    self.base_dir, project_config_path=self.runtime_config_path,
                ),
            )

        breadth = getattr(self._current_strategy(), "breadth", 1)
        # Publish the independent choice only after the root accepts the new
        # strategy. This also makes reset clear an SDK-injected scorer.
        self._apply_strategy(breadth=breadth, reward_model=scorer)
        self._reward_model = scorer
        logger.info("奖励模型已配置: model=%s breadth=%s", getattr(scorer, "model_name", None), breadth)
        return self.describe_reward_model()

    def _install_strategy_handler(self) -> None:
        """Apply configuration through the Manager's existing lazy binding hook.

        Creating a Runner must remain read-only with respect to Agent/model
        construction. Composing the binder also covers fresh roots acquired
        after a mode switch and functional roots recreated on later requests.
        """

        if self._runner is None:
            return
        manager = self._runner.agent_manager
        original_bind = manager.callbacks.bind_runtime

        def bind_with_strategy(managed: Any, cancel_event: threading.Event) -> None:
            if original_bind is not None:
                original_bind(managed, cancel_event)
            if managed.is_root and self._strategy is not None:
                managed.instance.set_exploration_strategy(self._strategy)

        manager.callbacks = replace(manager.callbacks, bind_runtime=bind_with_strategy)

    def switch_strategy(
        self,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Set candidate count; `/rm` separately controls catalog scoring."""

        params = params or {}
        unknown = set(params) - {"breadth", "reset"}
        if unknown:
            raise ValueError("Unknown strategy parameters: " + ", ".join(sorted(unknown)))
        reset = params.get("reset", False)
        if not isinstance(reset, bool):
            raise ValueError("reset must be a boolean")
        if reset and "breadth" in params:
            raise ValueError("reset cannot be combined with breadth")
        breadth = 1 if reset else params.get("breadth", 1)
        # bool is an int subclass, but is not a candidate count. Do not coerce
        # strings, lists or fractional numbers into a valid configuration.
        if type(breadth) is not int or breadth < 1:
            raise ValueError("breadth must be a positive integer")

        current_strategy = self._current_strategy()
        # Explicit catalog choices survive breadth=1 and strategy reset. An
        # injected SDK scorer retains the existing reset semantics instead.
        reward_model = self._reward_model
        if reward_model is None and not reset:
            reward_model = getattr(current_strategy, "reward_model", None)
        return self._apply_strategy(breadth=breadth, reward_model=reward_model, reset=reset)

    def _apply_strategy(
        self, *, breadth: int, reward_model: BaseRewardModel | None, reset: bool = False,
    ) -> dict[str, Any]:
        """Share root binding and pending state publication for `/rm` and `/strategy`."""
        from juice_agents.core.agent.context.strategies import GreedyStrategy, TreeOfThoughtsStrategy

        root = self._resolve_root_agent()
        strategy = (
            GreedyStrategy()
            if breadth == 1
            else TreeOfThoughtsStrategy(breadth=breadth, reward_model=reward_model)
        )
        selection_enabled = breadth > 1 and reward_model is not None
        strategy_info = {
            "type": "greedy" if breadth == 1 else "exploration",
            "params": {} if breadth == 1 else {"breadth": breadth},
            "execution": "candidate_selection" if selection_enabled else "linear",
            "scorer_configured": reward_model is not None,
        }
        # Apply before publishing the configuration so a failing setter does
        # not replace the previous pending choice.
        if root is not None:
            root.set_exploration_strategy(strategy)
        self._strategy = strategy
        self._strategy_params = {"reset": True} if reset else {"breadth": breadth}
        status = "applied" if root is not None else "pending"
        execution_message = (
            "Per-step candidate selection enabled"
            if selection_enabled
            else "线性执行 (Linear execution): "
            + ("breadth=1" if breadth == 1 else "no scorer configured")
        )
        logger.info(
            "探索策略已配置: status=%s breadth=%s execution=%s",
            status,
            breadth,
            strategy_info["execution"],
        )
        return {
            "status": status,
            "strategy": strategy_info,
            "message": execution_message + (
                "; strategy will apply when the root Agent is acquired" if status == "pending" else ""
            ),
        }

    def _workflow_registry(self) -> WorkflowRegistry:
        """Read workflow declarations without constructing a workflow runtime."""

        context = ConfigurationContext.from_workspace(
            self.base_dir,
            project_config_path=self.runtime_config_path,
        )
        return WorkflowRegistry(config_context=context)

    def _workflow_manager(self) -> WorkflowRunManager:
        """Return the active Runner-owned workflow manager.

        A workflow run is runtime state, so a cold CLI request first creates the
        same declarative Runner used by agent requests.  The adapter never
        constructs a second workflow execution owner.
        """

        if self._runner is None:
            self.start_session()
        manager = getattr(self._runner, "workflow_manager", None)
        if not isinstance(manager, WorkflowRunManager):
            raise RuntimeError("当前 Runner 未提供 WorkflowRunManager")
        return manager

    def _workflows_are_read_only(self) -> bool:
        config = mode_registry.resolve(self.agent_mode)
        return bool(config.tool_policy.read_only)

    def list_workflows(self) -> dict[str, Any]:
        workflows = [item.to_dict() for item in self._workflow_registry().list_metadata()]
        return {"workflows": workflows, "count": len(workflows)}

    def view_workflow(self, name: str) -> dict[str, Any]:
        return self._workflow_registry().view(name)

    def run_workflow(
        self,
        name: str,
        args: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._workflows_are_read_only():
            raise PermissionError("当前 RunnerConfig 为只读模式，禁止执行 workflow")
        if not isinstance(args, dict):
            raise ValueError("workflow args 必须为 object")
        called = self._workflow_manager().call(name, args, config)
        # 与 workflow_run 工具的同步返回保持同一形状。
        return {"status": "completed", "workflow_name": name, **called}

    def permission_status(self) -> dict[str, Any]:
        context = ConfigurationContext.from_workspace(
            self.base_dir,
            project_config_path=self.runtime_config_path,
        )
        return get_permission_status(
            context=context,
            runner=self._runner,
            permission_mode=self.permission_mode,
            agent_mode=self.agent_mode,
        )

    def stop_session(self, *, source: str = "user_exit") -> dict[str, bool]:
        """Stop active backend work for the current runner."""
        if self._runner is not None and callable(getattr(self._runner, "stop", None)):
            self._runner.stop(reason=source)
        return {"stopped": True}

    def request_cancel_stream(self, *, reason: str = "user_cancelled") -> dict[str, bool]:
        """请求中断当前正在进行的 stream_message; cooperative，仅在 step 边界生效。

        当前没有 active runner、或 runner 不支持中断时返回 cancelled=False；
        否则同时设置 runtime / runner 上的中断标志并返回 cancelled=True，由
        后续 step 边界与 _continue_active_goal 分别协作退出。
        """

        if self._runner is None:
            return {"cancelled": False}
        request = getattr(self._runner, "request_stop_stream", None)
        if not callable(request):
            return {"cancelled": False}
        request(reason=reason)
        # runtime 自己也维护标志，避免依赖 runner.is_stop_requested 的 truthy 行为
        # （MagicMock 默认 truthy；把判断收敛在 runtime 侧更稳）。
        cancel_requested = getattr(self, "_cancel_requested", None)
        if not callable(getattr(cancel_requested, "set", None)):
            self._cancel_requested = threading.Event()
        self._cancel_requested.set()
        return {"cancelled": True}

    def _resolve_runtime_model_metadata(self) -> dict[str, Any]:
        try:
            for item in self.list_models():
                if str(item.get("model_name") or "").strip() == self.model_name:
                    return dict(item)
        except Exception as exc:
            logger.debug("读取模型目录失败: %s", exc)
        return {}

    def _resolve_provider_model_name(self, runtime_model: dict[str, Any]) -> str:
        """
        Extract the active provider model name from the live root agent when possible.

        优先读 live root agent 上的 provider model；没有 live 数据时再回退到
        模型目录中的 `provider_model_name`。
        """
        root_agent = self._resolve_root_agent()
        model = getattr(root_agent, "model", None)
        runner_attrs = getattr(self._runner, "__dict__", {})

        for candidate in (
            getattr(model, "model_name", None),
            runner_attrs.get("model_name"),
            runtime_model.get("provider_model_name"),
        ):
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized

        return "unknown-model"

    def _resolve_root_agent(self) -> Any | None:
        """Read the root from AgentManager without letting Runner own it."""
        if self._runner is None:
            return None
        try:
            return self._runner.agent_manager.get(self._runner.root_agent_id).instance
        except (AttributeError, KeyError):
            return None

    def _current_root_tool_names(self) -> list[str]:
        """Return live or static root tool names without constructing an Agent."""
        root_agent = self._resolve_root_agent()
        tools = getattr(root_agent, "tools", None)
        if isinstance(tools, dict):
            return sorted(str(name) for name in tools)
        try:
            registry = AgentRegistry(
                config_context=ConfigurationContext.from_workspace(
                    self.base_dir,
                    project_config_path=self.runtime_config_path,
                )
            )
            declaration = registry.resolve(mode_registry.resolve(self.agent_mode).root_binding.agent_name)
            return sorted(ref.name for ref in declaration.tools)
        except (FileNotFoundError, ValueError):
            # A cold workspace has not necessarily been seeded yet.  Listing
            # skills remains read-only and must not force a Runner creation.
            logger.debug("根 Agent 声明尚不可用，skill 可见性为空")
        return []

    @staticmethod
    def coerce_output(value: Any) -> str:
        """Normalize various runtime payload types into printable text for the CLI."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)
        return str(value)


def _read_log_tail(path: Path, *, max_lines: int) -> str:
    """读取日志文件末尾 max_lines 行；文件不存在或读取失败时返回空串。"""
    if max_lines <= 0:
        return ""
    try:
        if not path.is_file():
            return ""
        # 日志通常不大；按行读全部然后取末尾。如果以后有大日志压力再换 seek。
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("读取异步任务日志失败 %s: %s", path, exc)
        return ""
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)
