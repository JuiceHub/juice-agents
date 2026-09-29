"""Session discovery helpers used by the stdio gateway adapter."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from juice_agents.core.runner.session_titles import extract_first_user_request_preview
from juice_agents.core.utils import sanitize_path_component


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionSummary:
    """Minimal persisted session metadata surfaced to the CLI session picker."""

    runner_id: str
    permission_mode: str
    agent_mode: str
    root_agent_name: str
    updated_at: str
    root_dir: Path
    first_user_request_preview: str = ""
    goal_status: str = ""
    goal_objective_preview: str = ""


def _format_updated_at(timestamp: float) -> str:
    """Format file mtimes into stable strings for the CLI session list."""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _first_user_request_preview(manifest_path: Path, payload: dict) -> str:
    """Read the root Agent snapshot used by the current Runner schema.

    Legacy runner schemas are intentionally not resumable.  Session discovery
    only understands Manager-owned ``agents/<id>/session.json`` snapshots.
    """

    root_agent_id = sanitize_path_component(
        str(payload.get("root_agent_id") or "root"),
        fallback="agent",
    )
    session_path = manifest_path.parent / "agents" / root_agent_id / "session.json"
    try:
        session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return extract_first_user_request_preview(session_payload)


def list_sessions(base_dir: str | Path) -> list[SessionSummary]:
    """Discover stored runner manifests under the workspace-local .juice directory."""
    resolved_base_dir = Path(base_dir).expanduser().resolve()
    juice_dir = resolved_base_dir / ".juice"
    if not juice_dir.exists():
        return []

    discovered: list[tuple[float, SessionSummary]] = []
    for manifest_path in juice_dir.glob("runners/*/manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("跳过无法解析的 session manifest: %s (%s)", manifest_path, exc)
            continue

        stat = manifest_path.stat()
        goal_ref = payload.get("goal_ref") if isinstance(payload.get("goal_ref"), dict) else {}
        goal_objective = str(goal_ref.get("objective") or "").strip()
        first_user_request_preview = str(payload.get("first_user_request_preview") or "").strip()
        if not first_user_request_preview:
            first_user_request_preview = _first_user_request_preview(manifest_path, payload)
        discovered.append(
            (
                stat.st_mtime,
                SessionSummary(
                    runner_id=str(payload.get("runner_id") or manifest_path.parent.name).strip()
                    or manifest_path.parent.name,
                    permission_mode=str(
                        payload.get("permission_mode") or ""
                    ).strip() or "unknown",
                    agent_mode=str(payload.get("agent_mode") or "").strip() or "unknown",
                    root_agent_name=str(payload.get("root_agent_name") or "unknown").strip()
                    or "unknown",
                    updated_at=_format_updated_at(stat.st_mtime),
                    root_dir=manifest_path.parent,
                    first_user_request_preview=first_user_request_preview,
                    goal_status=str(goal_ref.get("status") or "").strip(),
                    goal_objective_preview=goal_objective[:80],
                ),
            )
        )

    discovered.sort(key=lambda item: item[0], reverse=True)
    return [summary for _, summary in discovered]
