"""RPC method handlers for the stdio gateway adapter."""

from __future__ import annotations

import logging
from typing import Any, Iterator

from juice_agents.core.config.runtime_config import read_workspace_config, update_workspace_config
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.teams import TeamManifest, TeamRegistry
from juice_agents.core.runner.types.ask import AskHandler

from .runtime import DirectRunnerRuntime, SessionStatus
from .serialization import serialize_runner_stream_event
from .sessions import list_sessions


logger = logging.getLogger(__name__)


class RpcHandlers:
    """Dispatch JSON-RPC method names to runtime operations."""

    def __init__(self, ask_handler: AskHandler | None = None) -> None:
        self._runtime: DirectRunnerRuntime | None = None
        self._ask_handler = ask_handler

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Route a JSON-RPC method string to its concrete handler."""
        handler = getattr(self, f"handle_{method}", None)
        if handler is None:
            raise ValueError(f"Unknown method: {method}")
        return handler(params)

    def _bootstrap_runtime(self, params: dict[str, Any] | None = None) -> DirectRunnerRuntime:
        """Create a lightweight runtime shell for cold-start read/config RPCs."""

        if self._runtime is None:
            payload = params or {}
            self._runtime = DirectRunnerRuntime(
                base_dir=payload.get("base_dir", "."),
                permission_mode=payload.get("permission_mode", "default"),
                agent_mode=payload.get("agent_mode", "agent"),
                agent_type=payload.get("agent_type", "react"),
                runtime_config_path=payload.get("runtime_config_path"),
                model_name=payload.get("model_name", "doubao_lite"),
                model_effort=payload.get("model_effort", "disabled"),
                worktree=payload.get("worktree"),
                ask_handler=self._ask_handler,
            )
        return self._runtime

    @staticmethod
    def _team_registry(params: dict[str, Any]) -> TeamRegistry:
        """Resolve Team declarations without bootstrapping a Runner runtime."""

        return TeamRegistry(
            config_context=ConfigurationContext.from_workspace(params.get("base_dir", "."))
        )

    def handle_start_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a fresh runtime session for the requested workspace and mode."""
        base_dir = params.get("base_dir", ".")
        permission_mode = params.get("permission_mode", "default")
        agent_mode = params.get("agent_mode", "agent")
        agent_type = params.get("agent_type", "react")
        runtime_config_path = params.get("runtime_config_path")
        model_name = params.get("model_name", "doubao_lite")
        model_effort = params.get("model_effort", "disabled")
        worktree = params.get("worktree")

        self._runtime = DirectRunnerRuntime(
            base_dir=base_dir,
            permission_mode=permission_mode,
            agent_mode=agent_mode,
            agent_type=agent_type,
            runtime_config_path=runtime_config_path,
            model_name=model_name,
            model_effort=model_effort,
            worktree=None if worktree is None else str(worktree),
            ask_handler=self._ask_handler,
        )
        status = self._runtime.start_session()
        return _serialize_session_status(status)

    def handle_resume_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resume an existing runtime session from persisted runner state."""
        runner_id = params["runner_id"]
        base_dir = params.get("base_dir", ".")
        runtime_config_path = params.get("runtime_config_path")
        model_name = params.get("model_name", "doubao_lite")
        model_effort = params.get("model_effort", "disabled")

        self._runtime = DirectRunnerRuntime(
            base_dir=base_dir,
            runtime_config_path=runtime_config_path,
            model_name=model_name,
            model_effort=model_effort,
            ask_handler=self._ask_handler,
        )
        status = self._runtime.resume_session(runner_id)
        return _serialize_session_status(status)

    def handle_stream_message(self, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Forward a user message and yield serialized streaming steps."""
        if self._runtime is None:
            raise RuntimeError("No active session")

        message = params["message"]
        agent_mode_override = params.get("agent_mode_override")
        cancel_check = params.get("_cancel_check")
        stream_options: dict[str, Any] = {
            "agent_mode_override": (
                None if agent_mode_override is None else str(agent_mode_override)
            ),
        }
        if callable(cancel_check):
            stream_options["cancel_check"] = cancel_check
        for event in self._runtime.stream_message(message, **stream_options):
            payload = serialize_runner_stream_event(event)
            payload["agent_sessions_report"] = self._describe_agent_sessions_for_stream()
            yield payload

    def _describe_agent_sessions_for_stream(self) -> dict[str, Any] | None:
        """
        Best-effort Agent session snapshot for streaming notifications.

        The stdio server handles one long `stream_message` request at a time, so
        the CLI cannot issue a fresh `describe_agent_sessions` RPC while a run is
        still streaming. Attaching the current read-only report here keeps Ctrl+T
        agent selection current without introducing concurrent RPC handling.
        """
        if self._runtime is None:
            return None
        try:
            return self._runtime.describe_agent_sessions()
        except Exception as exc:  # pragma: no cover - defensive transport guard
            logger.warning("Failed to attach agent session report to stream event: %s", exc)
            return None

    def handle_describe_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the currently active session status snapshot."""
        if self._runtime is None:
            raise RuntimeError("No active session")
        return _serialize_session_status(self._runtime.describe_session())

    def handle_enter_worktree(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._runtime is None:
            raise RuntimeError("No active session")
        name = params.get("name")
        return self._runtime.enter_worktree(None if name is None else str(name))

    def handle_exit_worktree(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._runtime is None:
            raise RuntimeError("No active session")
        return self._runtime.exit_worktree(discard=bool(params.get("discard", False)))

    def handle_list_worktrees(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if self._runtime is None:
            raise RuntimeError("No active session")
        return self._runtime.list_worktrees()

    def handle_worktree_status(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._runtime is None:
            raise RuntimeError("No active session")
        name = params.get("name")
        return self._runtime.worktree_status(None if name is None else str(name))

    def handle_list_async_tasks(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Return public async task status rows for the active session."""
        if self._runtime is None:
            raise RuntimeError("No active session")
        statuses = {
            str(status or "").strip()
            for status in list(params.get("statuses") or [])
            if str(status or "").strip()
        }
        return self._runtime.list_async_tasks(statuses=statuses or None)

    def handle_list_graphs(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._bootstrap_runtime(params).list_graphs()

    def handle_view_graph(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._bootstrap_runtime(params).view_graph(str(params.get("name") or ""))

    def handle_run_graph(self, params: dict[str, Any]) -> dict[str, Any]:
        payload = params.get("payload") or {}
        config = params.get("config")
        if not isinstance(payload, dict):
            raise ValueError("payload 必须为 object")
        if config is not None and not isinstance(config, dict):
            raise ValueError("config 必须为 object")
        return self._bootstrap_runtime(params).run_graph(
            str(params.get("name") or ""),
            payload,
            config,
        )

    def handle_list_graph_runs(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._bootstrap_runtime(params).list_graph_runs()

    def handle_control_graph_run(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._bootstrap_runtime(params).control_graph_run(
            str(params.get("graph_run_id") or ""),
            str(params.get("action") or ""),
        )

    def handle_permission_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._bootstrap_runtime(params).permission_status()

    def handle_read_async_task_output(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return tail of stdout.log/stderr.log for a single async task."""
        if self._runtime is None:
            raise RuntimeError("No active session")
        async_task_id = str(params.get("async_task_id") or "").strip()
        if not async_task_id:
            raise ValueError("async_task_id 不能为空")
        max_lines_raw = params.get("max_lines")
        # 默认拉 200 行；显式传 0 也按 200 处理，避免空响应。
        max_lines = int(max_lines_raw) if max_lines_raw else 200
        if max_lines <= 0:
            max_lines = 200
        return self._runtime.read_async_task_output(
            async_task_id=async_task_id,
            max_lines=max_lines,
        )

    def handle_describe_agent_sessions(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return Agent sessions for CLI transcript switching."""
        del params
        if self._runtime is None:
            raise RuntimeError("No active session")
        return self._runtime.describe_agent_sessions()

    def handle_send_agent_message(self, params: dict[str, Any]) -> dict[str, Any]:
        """Queue a user instruction for the selected Agent."""
        if self._runtime is None:
            raise RuntimeError("No active session")
        agent_name = str(params.get("agent_name") or "").strip()
        message = str(params.get("message") or "").strip()
        if not agent_name:
            raise ValueError("agent_name 不能为空")
        if not message:
            raise ValueError("message 不能为空")
        return self._runtime.send_agent_message(agent_name=agent_name, message=message)

    def handle_interrupt_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        """Interrupt the selected Agent without cancelling the root stream."""
        if self._runtime is None:
            raise RuntimeError("No active session")
        agent_name = str(params.get("agent_name") or "").strip()
        if not agent_name:
            raise ValueError("agent_name 不能为空")
        return self._runtime.interrupt_agent(
            agent_name=agent_name,
            reason=str(params.get("reason") or "user_agent_interrupt"),
        )

    def handle_memory_status(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        runtime = self._bootstrap_runtime()
        return runtime.memory_status()

    def handle_memory_search(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._bootstrap_runtime(params)
        return runtime.memory_search(
            str(params.get("query") or ""),
            limit=int(params.get("limit") or 20),
        )

    def handle_memory_view(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._bootstrap_runtime(params)
        path = params.get("path")
        return runtime.memory_view(None if path is None else str(path))

    def handle_create_cron_task(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._bootstrap_runtime(params)
        return runtime.create_cron_task(
            cron=str(params.get("cron") or ""),
            prompt=str(params.get("prompt") or ""),
            recurring=bool(params.get("recurring", True)),
        )

    def handle_list_cron_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._bootstrap_runtime(params)
        return runtime.list_cron_tasks()

    def handle_delete_cron_task(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._bootstrap_runtime(params)
        return runtime.delete_cron_task(str(params.get("id") or params.get("task_id") or ""))

    def handle_fire_due_cron_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._bootstrap_runtime(params)
        now = params.get("now")
        return runtime.fire_due_cron_tasks(now=None if now is None else float(now))

    def handle_cron_status(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._bootstrap_runtime(params)
        return runtime.cron_status()

    def handle_set_goal(self, params: dict[str, Any]) -> dict[str, Any]:
        """Set the current runner goal."""
        if self._runtime is None:
            raise RuntimeError("No active session")
        return self._runtime.set_goal(
            str(params.get("objective") or ""),
            max_turns=params.get("max_turns"),
            max_runtime_seconds=params.get("max_runtime_seconds"),
        )

    def handle_get_goal(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if self._runtime is None:
            raise RuntimeError("No active session")
        return self._runtime.get_goal()

    def handle_pause_goal(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if self._runtime is None:
            raise RuntimeError("No active session")
        return self._runtime.pause_goal()

    def handle_resume_goal(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if self._runtime is None:
            raise RuntimeError("No active session")
        return self._runtime.resume_goal()

    def handle_clear_goal(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if self._runtime is None:
            raise RuntimeError("No active session")
        return self._runtime.clear_goal()

    def handle_set_memory_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update workspace YAML memory overrides and reload the active runner."""
        runtime = self._bootstrap_runtime(params)
        return runtime.set_memory_config(
            feature=str(params.get("feature") or ""),
            enabled=bool(params.get("enabled")),
        )

    def handle_set_browser_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update workspace YAML browser overrides and reload the active runner."""
        runtime = self._bootstrap_runtime(params)
        return runtime.set_browser_config(enabled=bool(params.get("enabled")))

    def handle_set_image_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update workspace YAML image overrides and reload the active runner."""
        runtime = self._bootstrap_runtime(params)
        return runtime.set_image_config(enabled=bool(params.get("enabled")))

    def handle_list_skills(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return skills visible to the current root agent capabilities."""
        runtime = self._bootstrap_runtime(params)
        category = params.get("category")
        return runtime.list_skills(category=None if category is None else str(category))

    def handle_list_plugins(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return enabled plugins without starting an unrelated Runner."""

        return self._bootstrap_runtime(params).list_plugins()

    def handle_list_available_agents(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return declarations available to `/agents` in the selected mode."""
        runtime = self._bootstrap_runtime(params)
        name = params.get("name")
        mode_id = params.get("mode_id")
        return runtime.list_available_agents(
            name=None if name is None else str(name),
            mode_id=None if mode_id is None else str(mode_id),
        )

    def handle_list_mode_resources(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return workspace resources grouped by registered mode id."""
        runtime = self._bootstrap_runtime(params)
        mode_id = params.get("mode_id")
        return runtime.list_mode_resources(
            mode_id=None if mode_id is None else str(mode_id),
        )

    def handle_view_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read a visible skill's SKILL.md or a supporting file."""
        runtime = self._bootstrap_runtime(params)
        return runtime.view_skill(
            str(params.get("name") or ""),
            file_path=params.get("file_path"),
        )

    def handle_view_plugin(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read enabled plugin metadata or a plugin-relative text file."""

        return self._bootstrap_runtime(params).view_plugin(
            str(params.get("name") or ""),
            file_path=params.get("file_path"),
        )

    def handle_skills_config_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return editable skill enablement state."""
        runtime = self._bootstrap_runtime(params)
        return runtime.skills_config_status()

    def handle_set_skills_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update workspace skill enablement and reload active runner."""
        runtime = self._bootstrap_runtime(params)
        raw_disabled = params.get("disabled")
        disabled = raw_disabled if isinstance(raw_disabled, (list, tuple)) else []
        return runtime.set_skills_config(
            enabled=params.get("enabled") if "enabled" in params else None,
            disabled=[str(item) for item in disabled],
        )

    def handle_self_evolution_config_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the workspace self-evolution switch."""

        return self._bootstrap_runtime(params).self_evolution_config_status()

    def handle_set_self_evolution_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Persist self-evolution visibility and reload a live runner."""

        return self._bootstrap_runtime(params).set_self_evolution_config(
            enabled=bool(params.get("enabled")),
        )

    def handle_graphs_config_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the workspace automatic-Graph switch."""

        return self._bootstrap_runtime(params).graphs_config_status()

    def handle_set_graphs_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Persist Graph tool visibility and reload a live runner."""

        return self._bootstrap_runtime(params).set_graphs_config(enabled=bool(params.get("enabled")))

    def handle_plugins_config_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return editable plugin enablement state."""

        return self._bootstrap_runtime(params).plugins_config_status()

    def handle_set_plugins_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update workspace plugin enablement and reload the active runner."""

        raw_disabled = params.get("disabled")
        disabled = raw_disabled if isinstance(raw_disabled, (list, tuple)) else []
        return self._bootstrap_runtime(params).set_plugins_config(
            enabled=params.get("enabled") if "enabled" in params else None,
            disabled=[str(item) for item in disabled],
        )

    def handle_switch_permission_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        """切换权限模式（default/accept）；不重建 root agent。"""
        runtime = self._bootstrap_runtime(params)
        status = runtime.switch_permission_mode(params["permission_mode"])
        return _serialize_session_status(status)

    def handle_enter_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        """Enter plan mode explicitly for clients that do not use /mode."""
        del params
        if self._runtime is None:
            raise RuntimeError("No active session")
        return _serialize_session_status(self._runtime.enter_plan())

    def handle_switch_agent_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        """切换 Agent 执行模式（agent/plan/team/group）；空闲时刷新 root 配置。"""
        runtime = self._bootstrap_runtime(params)
        agent_mode = params["agent_mode"]
        agent_type = params.get("agent_type", "react")
        model_name = params.get("model_name")
        model_effort = params.get("model_effort")
        status = runtime.switch_agent_mode(agent_mode, agent_type, model_name, model_effort)
        return _serialize_session_status(status)

    def handle_list_models(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """List runtime models from the configured catalog."""
        if self._runtime is None:
            base_dir = params.get("base_dir", ".")
            self._runtime = DirectRunnerRuntime(
                base_dir=base_dir,
                runtime_config_path=params.get("runtime_config_path"),
                model_name=params.get("model_name", "doubao_lite"),
                model_effort=params.get("model_effort", "disabled"),
                ask_handler=self._ask_handler,
            )
        return list(self._runtime.list_models())

    def handle_switch_model(self, params: dict[str, Any]) -> dict[str, Any]:
        """Reconfigure the current session with a different logical model."""
        runtime = self._bootstrap_runtime(params)
        status = runtime.switch_model(
            params["model_name"],
            model_effort=params.get("model_effort"),
        )
        return _serialize_session_status(status)

    def handle_stop_session(self, params: dict[str, Any]) -> dict[str, bool]:
        """Stop active backend work for the current session before CLI exit."""
        if self._runtime is None:
            return {"stopped": True}
        return self._runtime.stop_session(
            source=str(params.get("source") or "user_exit")
        )

    def handle_cancel_stream(self, params: dict[str, Any]) -> dict[str, bool]:
        """请求中断正在进行的 stream_message; cooperative，下一 step 边界生效。

        允许在 streaming 期间被并发派发：handler 仅设置 runner 上的中断标志，
        不阻塞、不持久化，不会与正在迭代的生成器竞争 session 状态。
        """
        del params
        if self._runtime is None:
            return {"cancelled": False}
        return self._runtime.request_cancel_stream()

    def handle_list_sessions(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """List persisted sessions under the requested workspace root."""
        base_dir = params.get("base_dir", ".")
        summaries = list_sessions(base_dir)
        return [_serialize_session_summary(summary) for summary in summaries]

    def handle_load_workspace_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Load workspace-local YAML runtime overrides from `.juice/config.yaml`."""
        base_dir = params.get("base_dir", ".")
        return read_workspace_config(base_dir)

    def handle_list_team_configs(self, params: dict[str, Any]) -> dict[str, Any]:
        """List Team manifests and their global Agent name references."""
        registry = self._team_registry(params)
        return {
            "teams": [
                {
                    "team_name": manifest.team_name,
                    "description": manifest.description,
                    "member_names": list(manifest.member_names),
                    "manifest_path": str(registry.manifest_path(manifest.team_name)),
                }
                for manifest in registry.list_manifests()
            ]
        }

    def handle_get_team_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return one Team manifest by name."""
        team_name = str(params.get("team_name") or "").strip()
        if not team_name:
            raise ValueError("team_name 不能为空")
        return self._team_registry(params).get_manifest(team_name).to_dict()

    def handle_create_team_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create an empty Team unless explicit member references are supplied."""
        member_names = params.get("member_names", [])
        if not isinstance(member_names, list):
            raise ValueError("member_names 必须为数组")
        shared_agent_names = params.get("shared_agent_names")
        if shared_agent_names is not None and not isinstance(shared_agent_names, dict):
            raise ValueError("shared_agent_names 必须为 object")
        registry = self._team_registry(params)
        manifest = registry.create(
            TeamManifest(
                team_name=str(params.get("team_name") or "").strip(),
                member_names=tuple(str(name) for name in member_names),
                shared_agent_names=shared_agent_names,
                description=str(params.get("description") or ""),
            )
        )
        return {
            "team_name": manifest.team_name,
            "manifest_path": str(registry.manifest_path(manifest.team_name)),
        }

    def handle_update_team_manifest(self, params: dict[str, Any]) -> dict[str, Any]:
        names = params.get("member_names")
        if names is not None and not isinstance(names, list):
            raise ValueError("member_names 必须为数组")
        shared_agent_names = params.get("shared_agent_names")
        if shared_agent_names is not None and not isinstance(shared_agent_names, dict):
            raise ValueError("shared_agent_names 必须为 object")
        registry = self._team_registry(params)
        manifest = registry.get_manifest(str(params.get("team_name") or "").strip())
        changes: dict[str, Any] = {}
        if params.get("description") is not None:
            changes["description"] = str(params["description"])
        if names is not None:
            changes["member_names"] = [str(name) for name in names]
        if shared_agent_names is not None:
            changes["shared_agent_names"] = shared_agent_names
        updated = registry.update(manifest.copy_with(**changes))
        return {
            "team_name": updated.team_name,
            "manifest_path": str(registry.manifest_path(updated.team_name)),
        }

    def handle_delete_team_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Delete a team config directory (runner-isolated state is left untouched)."""
        team_name = str(params.get("team_name") or "").strip()
        if not team_name:
            raise ValueError("team_name 不能为空")
        self._team_registry(params).delete(team_name)
        return {"team_name": team_name, "deleted": True}

    def handle_save_workspace_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Merge partial runtime overrides into `.juice/config.yaml`."""
        base_dir = params.get("base_dir", ".")
        patch = params.get("config", {})
        if not isinstance(patch, dict):
            raise ValueError("config must be an object")

        def _merge(current: dict[str, Any]) -> dict[str, Any]:
            for key, value in patch.items():
                if isinstance(current.get(key), dict) and isinstance(value, dict):
                    merged = dict(current[key])
                    merged.update(value)
                    current[key] = merged
                else:
                    current[key] = value
            return current

        update_workspace_config(base_dir, _merge)
        return {"saved": True}


def _serialize_session_status(status: SessionStatus) -> dict[str, Any]:
    """Convert a strongly typed status snapshot into JSON-RPC payload data."""
    return {
        "runner_id": str(status.runner_id),
        "permission_mode": str(status.permission_mode),
        "agent_mode": str(status.agent_mode),
        "root_agent_name": str(status.root_agent_name),
        "base_dir": str(status.base_dir),
        "started": bool(status.started),
        "resumed": bool(status.resumed),
        "agent_type": str(status.agent_type),
        "model_name": str(status.model_name),
        "model_effort": str(status.model_effort),
        "backend": str(status.backend),
        "provider_model_name": str(status.provider_model_name),
        "goal": dict(status.goal or {}) if status.goal else None,
        "worktree": dict(status.worktree or {}) if status.worktree else None,
    }


def _serialize_session_summary(summary) -> dict[str, Any]:
    """Convert a discovered session manifest summary into JSON payload data."""
    return {
        "runner_id": str(summary.runner_id),
        "permission_mode": str(summary.permission_mode),
        "agent_mode": str(summary.agent_mode),
        "root_agent_name": str(summary.root_agent_name),
        "updated_at": str(summary.updated_at),
        "root_dir": str(summary.root_dir),
        "first_user_request_preview": str(getattr(summary, "first_user_request_preview", "") or ""),
        "goal_status": str(getattr(summary, "goal_status", "") or ""),
        "goal_objective_preview": str(getattr(summary, "goal_objective_preview", "") or ""),
    }
