"""Runner manifest and Manager-owned directory layout.

Runner persistence contains only request metadata and a serializable
``RunnerConfig``.  Agent sessions, task state, tool audits and graph runs are
owned by their respective managers beneath this root.
"""

from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..types.definitions import RunnerState

logger = logging.getLogger(__name__)

RUNNER_SCHEMA_VERSION = 5
_RUNNER_STATUSES = frozenset({"idle", "running", "waiting", "paused", "failed"})


def _write_json(path: Path, payload: Any) -> Any:
    """Persist a small JSON document under an already Runner-owned path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    return default if not text else json.loads(text)


@dataclass(frozen=True, slots=True)
class RunnerLayout:
    """The complete durable namespace for one Runner request."""

    root_dir: Path
    manifest_path: Path
    agents_dir: Path
    async_tasks_dir: Path
    async_task_registry_path: Path
    tools_dir: Path
    graphs_dir: Path
    observation_images_dir: Path
    browser_screenshots_dir: Path
    todos_dir: Path
    plans_dir: Path
    goal_path: Path

    def ensure_dirs(self) -> None:
        """Create only Manager/state directories; no mode-specific namespace."""

        for path in (
            self.root_dir,
            self.agents_dir,
            self.async_tasks_dir,
            self.tools_dir,
            self.graphs_dir,
            self.observation_images_dir,
            self.browser_screenshots_dir,
            self.todos_dir,
            self.plans_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def async_task_dir(self, async_task_id: str) -> Path:
        return self.async_tasks_dir / str(async_task_id or "").strip()

    def async_task_manifest_path(self, async_task_id: str) -> Path:
        return self.async_task_dir(async_task_id) / "manifest.json"

    def async_task_events_path(self, async_task_id: str) -> Path:
        return self.async_task_dir(async_task_id) / "events.json"

    def async_task_stdout_path(self, async_task_id: str) -> Path:
        return self.async_task_dir(async_task_id) / "stdout.log"

    def async_task_stderr_path(self, async_task_id: str) -> Path:
        return self.async_task_dir(async_task_id) / "stderr.log"


def build_layout(
    base_dir: str | Path,
    runner_id: str,
    *,
    root_dir: str | Path | None = None,
) -> RunnerLayout:
    resolved_base = Path(base_dir).expanduser().resolve()
    resolved_root = (
        Path(root_dir).expanduser().resolve()
        if root_dir is not None
        else resolved_base / ".juice" / "runners" / str(runner_id)
    )
    return RunnerLayout(
        root_dir=resolved_root,
        manifest_path=resolved_root / "manifest.json",
        agents_dir=resolved_root / "agents",
        async_tasks_dir=resolved_root / "async_tasks",
        async_task_registry_path=resolved_root / "async_tasks" / "registry.json",
        tools_dir=resolved_root / "tools",
        graphs_dir=resolved_root / "graphs",
        observation_images_dir=resolved_root / "observation_images",
        browser_screenshots_dir=resolved_root / "browser_screenshots",
        todos_dir=resolved_root / "todos",
        plans_dir=resolved_root / "plans",
        goal_path=resolved_root / "goal.json",
    )


def blank_runner_state(
    *,
    runner_id: str,
    permission_mode: str,
    agent_mode: str,
    root_agent_name: str,
) -> RunnerState:
    """Create an explicitly non-resumable manifest until config is attached."""

    now = time.time()
    return {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "runner_id": str(runner_id or "").strip(),
        "permission_mode": str(permission_mode or "default").strip() or "default",
        "agent_mode": str(agent_mode or "agent").strip() or "agent",
        "runner_config": {},
        "status": "idle",
        "status_reason": "created",
        "status_changed_at": now,
        "active_round": None,
        "last_stop": None,
        "root_agent_id": "",
        "root_agent_name": str(root_agent_name or "").strip(),
        "team_name": "",
        "team_run_id": "",
        # Unload is a Runner/mode-local visibility state, never a YAML edit.
        "disabled_agent_names_by_mode": {},
        # Plan approval is persisted until the gateway reaches a safe request
        # boundary.  A process restart must not silently forget an approved
        # plan or transition while a prior request is still active.
        "plan_return_mode": "",
        "pending_plan_exit": None,
        "created_at": now,
        "updated_at": now,
    }


def load_runner_state(path: str | Path) -> RunnerState:
    """Read only current-schema manifests; old state is never migrated."""

    payload = _read_json(Path(path), default={})
    if not isinstance(payload, dict):
        raise ValueError(f"runner manifest 非法: {path}")
    schema_version = payload.get("schema_version")
    if schema_version != RUNNER_SCHEMA_VERSION:
        raise ValueError(
            f"旧 Runner 状态不支持恢复: schema_version={schema_version!r}; "
            f"需要 {RUNNER_SCHEMA_VERSION}。原目录将保持不变。"
        )
    status = str(payload.get("status") or "").strip()
    if status not in _RUNNER_STATUSES:
        raise ValueError(f"runner manifest status 非法: {status!r}")
    config = payload.get("runner_config")
    if not isinstance(config, dict) or not config:
        raise ValueError("旧 Runner 状态不支持恢复: 缺少 runner_config 声明")
    return dict(payload)


def write_runner_state(path: str | Path, state: RunnerState) -> RunnerState:
    """Write the small request-state projection, excluding Manager internals."""

    incoming = deepcopy(dict(state or {}))
    status = str(incoming.get("status") or "idle").strip() or "idle"
    if status not in _RUNNER_STATUSES:
        raise ValueError(f"runner manifest status 非法: {status!r}")
    active_round = incoming.get("active_round")
    last_stop = incoming.get("last_stop")
    payload: RunnerState = {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "runner_id": str(incoming.get("runner_id") or "").strip(),
        "permission_mode": str(incoming.get("permission_mode") or "default").strip() or "default",
        "agent_mode": str(incoming.get("agent_mode") or "agent").strip() or "agent",
        "runner_config": dict(incoming.get("runner_config") or {}),
        "status": status,
        "status_reason": str(incoming.get("status_reason") or "").strip(),
        "status_changed_at": float(incoming.get("status_changed_at") or time.time()),
        "active_round": dict(active_round) if isinstance(active_round, dict) else None,
        "last_stop": dict(last_stop) if isinstance(last_stop, dict) else None,
        "root_agent_id": str(incoming.get("root_agent_id") or "").strip(),
        "root_agent_name": str(incoming.get("root_agent_name") or "").strip(),
        "team_name": str(incoming.get("team_name") or "").strip(),
        "team_run_id": str(incoming.get("team_run_id") or "").strip(),
        "disabled_agent_names_by_mode": {
            str(mode).strip(): sorted(
                {
                    str(name).strip()
                    for name in names
                    if str(name).strip()
                }
            )
            for mode, names in dict(incoming.get("disabled_agent_names_by_mode") or {}).items()
            if str(mode).strip() and isinstance(names, list)
        },
        "plan_return_mode": str(incoming.get("plan_return_mode") or "").strip(),
        "pending_plan_exit": (
            dict(incoming["pending_plan_exit"])
            if isinstance(incoming.get("pending_plan_exit"), dict)
            else None
        ),
        "first_user_request_preview": str(incoming.get("first_user_request_preview") or "").strip(),
        "created_at": float(incoming.get("created_at") or time.time()),
        "updated_at": time.time(),
    }
    return _write_json(Path(path), payload)


__all__ = [
    "RUNNER_SCHEMA_VERSION",
    "RunnerLayout",
    "blank_runner_state",
    "build_layout",
    "load_runner_state",
    "write_runner_state",
]
