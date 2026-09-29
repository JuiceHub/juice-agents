"""Persistent graph runs, events, checkpoints, results, and artifacts."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from juice_agents.core.utils import sanitize_path_component

_STATUSES = {"pending", "running", "paused", "completed", "failed", "stopped"}


def _write_json(path: Path, payload: Any) -> None:
    """Atomically replace a durable graph document.

    A control request can arrive while the graph worker is checkpointing.  A
    direct ``write_text`` truncates the manifest before replacing its content,
    which lets the other thread observe an empty/nonexistent run.  Replacing a
    complete sibling file makes readers see either the prior valid manifest or
    the new valid manifest, never an intermediate one.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    return default if not text else json.loads(text)


class GraphRunStore:
    """Store all graph runs under one Runner root directory."""

    def __init__(self, runner_root: str | Path) -> None:
        self.runner_root = Path(runner_root).expanduser().resolve()
        self.graphs_dir = self.runner_root / "graphs"
        # The graph worker and a control request can both update a manifest.
        # The lock serializes read-modify-write transitions within one process;
        # `_write_json` supplies the matching safe-reader guarantee.
        self._lock = threading.RLock()

    def run_dir(self, graph_run_id: str) -> Path:
        return self.graphs_dir / sanitize_path_component(graph_run_id, fallback="graph_run")

    def manifest_path(self, graph_run_id: str) -> Path:
        return self.run_dir(graph_run_id) / "manifest.json"

    def create(
        self,
        *,
        graph_name: str,
        payload: dict[str, Any],
        config: dict[str, Any] | None,
        source: dict[str, Any],
        source_run_id: str = "",
        owner_agent_name: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            run_id = f"graph_{sanitize_path_component(graph_name, fallback='graph')}_{uuid4().hex[:12]}"
            run_dir = self.run_dir(run_id)
            now = time.time()
            manifest = {
            "graph_run_id": run_id,
            "graph_name": graph_name,
            "status": "pending",
            "requested_status": "",
            "source_run_id": source_run_id,
            "owner_agent_name": str(owner_agent_name or "").strip(),
            "source": dict(source),
            "payload": dict(payload),
            "config": dict(config or {}),
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "updated_at": now,
            "current_node": "",
            "completed_nodes": 0,
            "error": "",
            "closed_reason": "",
            "run_dir": str(run_dir),
            "events_path": str(run_dir / "events.jsonl"),
            "checkpoint_path": str(run_dir / "checkpoint.json"),
            "result_path": str(run_dir / "result.json"),
            "artifacts_dir": str(run_dir / "artifacts"),
            "source_path": str(run_dir / "source.py"),
            }
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "artifacts").mkdir(exist_ok=True)
            _write_json(self.manifest_path(run_id), manifest)
            (run_dir / "events.jsonl").touch()
            source_path = str(source.get("path") or "").strip()
            if source_path and Path(source_path).exists():
                (run_dir / "source.py").write_text(Path(source_path).read_text(encoding="utf-8"), encoding="utf-8")
            return manifest

    def get(self, graph_run_id: str) -> dict[str, Any]:
        with self._lock:
            manifest = _read_json(self.manifest_path(graph_run_id), default={})
            if not isinstance(manifest, dict) or not manifest:
                raise FileNotFoundError(f"graph run 不存在: {graph_run_id}")
            return dict(manifest)

    def update(self, graph_run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            manifest = self.get(graph_run_id)
            manifest.update(changes)
            manifest["updated_at"] = time.time()
            _write_json(self.manifest_path(graph_run_id), manifest)
            return manifest

    def list(self, *, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows: list[dict[str, Any]] = []
            if not self.graphs_dir.exists():
                return rows
            for path in sorted(self.graphs_dir.glob("*/manifest.json")):
                payload = _read_json(path, default={})
                if not isinstance(payload, dict):
                    continue
                if statuses and str(payload.get("status") or "") not in statuses:
                    continue
                rows.append(dict(payload))
            return sorted(rows, key=lambda item: float(item.get("updated_at") or 0.0), reverse=True)

    def checkpoint(self, graph_run_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            _write_json(self.run_dir(graph_run_id) / "checkpoint.json", payload)

    def load_checkpoint(self, graph_run_id: str) -> dict[str, Any]:
        with self._lock:
            payload = _read_json(self.run_dir(graph_run_id) / "checkpoint.json", default={})
            return dict(payload) if isinstance(payload, dict) else {}

    def append_event(self, graph_run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            path = self.run_dir(graph_run_id) / "events.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            self.update(
                graph_run_id,
                current_node=str(event.get("node") or ""),
                completed_nodes=int(self.get(graph_run_id).get("completed_nodes") or 0) + (1 if event.get("node") else 0),
            )

    def result(self, graph_run_id: str, payload: Any) -> None:
        with self._lock:
            _write_json(self.run_dir(graph_run_id) / "result.json", payload)

    def request(self, graph_run_id: str, action: str) -> dict[str, Any]:
        with self._lock:
            normalized = str(action or "").strip().lower()
            if normalized not in {"pause", "resume", "stop"}:
                raise ValueError(f"未知 graph run action: {action}")
            manifest = self.get(graph_run_id)
            if normalized == "resume":
                resumable_stale_run = (
                    manifest.get("status") == "stopped"
                    and manifest.get("closed_reason") == "stale_on_resume"
                )
                if manifest.get("status") != "paused" and not resumable_stale_run:
                    raise ValueError("只有 paused 或异常退出后收敛的 graph run 可以 resume")
                return self.update(
                    graph_run_id,
                    requested_status="",
                    status="pending",
                    error="",
                    finished_at=None,
                    closed_reason="",
                )
            if normalized == "pause" and manifest.get("status") not in {"pending", "running"}:
                raise ValueError("只有 pending/running graph run 可以 pause")
            if normalized == "stop" and manifest.get("status") not in {"pending", "running", "paused"}:
                raise ValueError("只有未完成 graph run 可以 stop")
            return self.update(graph_run_id, requested_status=normalized)

    def reconcile_stale_runs(self, *, reason: str = "stale_on_resume") -> list[dict[str, Any]]:
        """Stop runs that cannot still have an executor after process resume."""

        reconciled: list[dict[str, Any]] = []
        for manifest in self.list(statuses={"pending", "running"}):
            reconciled.append(
                self.update(
                    str(manifest["graph_run_id"]),
                    status="stopped",
                    requested_status="",
                    finished_at=time.time(),
                    error="",
                    closed_reason=str(reason or "stale_on_resume"),
                )
            )
        return reconciled


# Kept here for graph-domain imports; the manager itself lives with the other
# runtime owners and intentionally has no Runner dependency.
from juice_agents.core.managers.graphs import GraphRunManager

__all__ = ["GraphRunStore", "GraphRunManager"]
