"""FastAPI adapter that exposes Juice runners to the browser web app.

The web gateway stays deliberately thin: it owns HTTP/WebSocket transport,
workspace-safe file preview helpers, and ask-response coordination. Agent,
runner, memory, skills, and model behavior continue to live behind
``DirectRunnerRuntime`` so the CLI and web app do not fork runtime semantics.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import queue
import logging
import mimetypes
import os
import threading
import time
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from adapters.stdio_gateway.runtime import DirectRunnerRuntime
from adapters.stdio_gateway.serialization import serialize_runner_stream_event
from adapters.stdio_gateway.sessions import list_sessions
from juice_agents.core.config.runtime_config import read_workspace_config, update_workspace_config
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.teams import TeamManifest, TeamRegistry
from juice_agents.core.agent.tools.builtin.web.browser.session import get_browser_session, get_existing_browser_session
from juice_agents.core.runner.types.ask import AskRequest, normalize_ask_response

logger = logging.getLogger(__name__)

IGNORED_DIRS = {".git", ".juice", ".cache", ".pytest_cache", "__pycache__", "node_modules", "dist"}
PREVIEW_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
FILE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
WEB_ARCHIVE_MARKER = ".web_archive.json"


def is_loopback_host(host: str) -> bool:
    """Accept literal loopback IPs or localhost, never names resolved through DNS."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_local_authority(authority: str) -> bool:
    """Validate Host / Origin authorities, including bracketed IPv6 and ports."""
    if not authority or authority != authority.strip():
        return False
    try:
        parsed = urlsplit(f"//{authority}")
        # urllib may otherwise parse userinfo, paths, or a malformed port as a
        # different host than the authority presented by the browser.
        return (
            parsed.netloc == authority
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and parsed.port != 0
            and is_loopback_host(parsed.hostname or "")
        )
    except ValueError:
        return False


def _is_local_origin(origin: str | None) -> bool:
    """Browsers always send Origin for WebSockets; absent means a local client."""
    if origin is None:
        return True
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme in {"http", "https"}
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and _is_local_authority(parsed.netloc)
        )
    except ValueError:
        return False


async def _allow_local_websocket(websocket: WebSocket) -> bool:
    """Reject DNS rebinding and cross-site WebSocket attempts before accept."""
    host = websocket.headers.get("host", "")
    origin = websocket.headers.get("origin")
    if _is_local_authority(host) and _is_local_origin(origin):
        return True
    logger.warning("Rejected nonlocal web gateway websocket: host=%s origin=%s", host, origin)
    await websocket.close(code=1008)
    return False


def create_app() -> FastAPI:
    """Build the local-only web gateway app."""

    app = FastAPI(title="Juice Web Gateway", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1):\d+",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_local_host(request: Request, call_next: Callable[..., Any]) -> Any:
        # A local bind alone does not stop DNS rebinding: the browser can send
        # an attacker-controlled Host to a service on 127.0.0.1.
        if not _is_local_authority(request.headers.get("host", "")):
            logger.warning("Rejected nonlocal web gateway HTTP Host: %s", request.headers.get("host"))
            return PlainTextResponse("local Host required", status_code=400)
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/bootstrap")
    async def bootstrap(base_dir: str = ".") -> dict[str, Any]:
        runtime = _runtime_for(base_dir)
        return {
            "base_dir": str(runtime.base_dir),
            "workspace_config": read_workspace_config(runtime.base_dir),
            "sessions": [_serialize_session(summary) for summary in _list_web_sessions(runtime.base_dir)],
            "models": runtime.list_models(),
            "memory": runtime.memory_status(),
        }

    @app.put("/api/workspace-config")
    async def save_workspace_config(payload: dict[str, Any]) -> dict[str, bool]:
        base_dir = payload.get("base_dir", ".")
        patch = payload.get("config", {})
        if not isinstance(patch, dict):
            raise HTTPException(status_code=400, detail="config must be an object")

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

    # ------------------------------------------------------------------
    # Team 配置：manifest + 全局 Agent 名称引用
    # ------------------------------------------------------------------

    @app.get("/api/teams/configs")
    async def list_team_configs(base_dir: str = ".") -> dict[str, Any]:
        return _serialize_team_list(_team_registry_for(base_dir))

    @app.get("/api/teams/configs/{team_name}")
    async def get_team_config(team_name: str, base_dir: str = ".") -> dict[str, Any]:
        try:
            return _team_registry_for(base_dir).get_manifest(team_name).to_dict()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/teams/configs")
    async def create_team_config(payload: dict[str, Any]) -> dict[str, Any]:
        team_name = str(payload.get("team_name") or "").strip()
        if not team_name:
            raise HTTPException(status_code=400, detail="team_name 不能为空")
        member_names = payload.get("member_names", [])
        if not isinstance(member_names, list):
            raise HTTPException(status_code=400, detail="member_names 必须为数组")
        shared_agent_names = payload.get("shared_agent_names")
        if shared_agent_names is not None and not isinstance(shared_agent_names, dict):
            raise HTTPException(status_code=400, detail="shared_agent_names 必须为 object")
        try:
            registry = _team_registry_for(payload.get("base_dir", "."))
            manifest = registry.create(
                TeamManifest(
                    team_name=team_name,
                    member_names=tuple(str(name) for name in member_names),
                    shared_agent_names=shared_agent_names,
                    description=str(payload.get("description") or ""),
                )
            )
            return {
                "team_name": manifest.team_name,
                "manifest_path": str(registry.manifest_path(manifest.team_name)),
            }
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put("/api/teams/configs/{team_name}/manifest")
    async def update_team_manifest(team_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        member_names = payload.get("member_names")
        if member_names is not None and not isinstance(member_names, list):
            raise HTTPException(status_code=400, detail="member_names 必须为数组")
        shared_agent_names = payload.get("shared_agent_names")
        if shared_agent_names is not None and not isinstance(shared_agent_names, dict):
            raise HTTPException(status_code=400, detail="shared_agent_names 必须为 object")
        try:
            registry = _team_registry_for(payload.get("base_dir", "."))
            manifest = registry.get_manifest(team_name)
            changes: dict[str, Any] = {}
            if payload.get("description") is not None:
                changes["description"] = str(payload["description"])
            if member_names is not None:
                changes["member_names"] = [str(name) for name in member_names]
            if shared_agent_names is not None:
                changes["shared_agent_names"] = shared_agent_names
            updated = registry.update(manifest.copy_with(**changes))
            return {
                "team_name": updated.team_name,
                "manifest_path": str(registry.manifest_path(updated.team_name)),
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/api/teams/configs/{team_name}")
    async def delete_team_config(team_name: str, base_dir: str = ".") -> dict[str, Any]:
        try:
            _team_registry_for(base_dir).delete(team_name)
            return {"team_name": team_name, "deleted": True}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/files/tree")
    async def files_tree(
        base_dir: str = ".",
        max_depth: int = Query(default=4, ge=1, le=8),
        max_entries: int = Query(default=600, ge=1, le=3000),
    ) -> dict[str, Any]:
        root = _resolve_workspace(base_dir)
        return {
            "base_dir": str(root),
            "tree": _build_file_tree(root, root, max_depth=max_depth, budget=[max_entries]),
        }

    @app.get("/api/files/read")
    async def files_read(base_dir: str = ".", path: str = "") -> dict[str, Any]:
        root = _resolve_workspace(base_dir)
        target = _resolve_inside(root, path)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        rel = _relative_posix(root, target)
        if target.suffix.lower() in FILE_IMAGE_SUFFIXES:
            media_type = mimetypes.guess_type(target.name)[0] or "image/png"
            return {
                "path": rel,
                "content": "",
                "language": "image",
                "kind": "image",
                "image_url": f"/api/files/asset?base_dir={quote(str(root))}&path={quote(rel)}",
                "media_type": media_type,
            }
        if target.stat().st_size > 512_000:
            raise HTTPException(status_code=413, detail="file is too large for preview")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="file is not utf-8 text") from exc
        return {
            "path": rel,
            "content": content,
            "language": _guess_language(target),
            "kind": "text",
        }

    @app.get("/api/files/asset")
    async def files_asset(base_dir: str = ".", path: str = "") -> FileResponse:
        root = _resolve_workspace(base_dir)
        target = _resolve_inside(root, unquote(path))
        if not target.exists() or not target.is_file() or target.suffix.lower() not in FILE_IMAGE_SUFFIXES:
            raise HTTPException(status_code=404, detail="image file not found")
        media_type = mimetypes.guess_type(target.name)[0] or "image/png"
        return FileResponse(target, media_type=media_type)

    @app.get("/api/browser/live/preview")
    async def browser_live_preview(base_dir: str = ".", runner_id: str = "") -> dict[str, Any]:
        if runner_id and not _is_safe_browser_runner_id(runner_id):
            raise HTTPException(status_code=400, detail="invalid runner_id")
        root = _resolve_workspace(base_dir)
        return await asyncio.to_thread(_browser_live_preview, root, runner_id)

    @app.post("/api/browser/live/open-external")
    async def browser_live_open_external(payload: dict[str, Any]) -> dict[str, Any]:
        root = _resolve_workspace(str(payload.get("base_dir") or "."))
        runner_id = str(payload.get("runner_id") or "")
        if runner_id and not _is_safe_browser_runner_id(runner_id):
            raise HTTPException(status_code=400, detail="invalid runner_id")
        url = str(payload.get("url") or "")
        try:
            return await asyncio.to_thread(_browser_live_open_external, root, runner_id, url)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/browser/live/new-tab")
    async def browser_live_new_tab(payload: dict[str, Any]) -> dict[str, Any]:
        root = _resolve_workspace(str(payload.get("base_dir") or "."))
        runner_id = str(payload.get("runner_id") or "").strip()
        if not runner_id or not _is_safe_browser_runner_id(runner_id):
            raise HTTPException(status_code=400, detail="runner_id is required to create a browser tab")
        url = str(payload.get("url") or "")
        make_active = bool(payload.get("make_active", True))
        return await asyncio.to_thread(_browser_live_new_tab, root, runner_id, url, make_active)

    @app.get("/api/browser/asset")
    async def browser_asset(base_dir: str = ".", path: str = "") -> FileResponse:
        root = _resolve_workspace(base_dir)
        target = _resolve_inside(root, unquote(path))
        if not target.exists() or target.suffix.lower() not in PREVIEW_IMAGE_SUFFIXES:
            raise HTTPException(status_code=404, detail="browser image not found")
        media_type = mimetypes.guess_type(target.name)[0] or "image/png"
        return FileResponse(target, media_type=media_type)

    @app.websocket("/ws")
    async def websocket_gateway(websocket: WebSocket) -> None:
        if not await _allow_local_websocket(websocket):
            return
        connection = WebSocketRuntimeConnection(websocket)
        await connection.run()

    @app.websocket("/ws/browser/live")
    async def browser_live_socket(websocket: WebSocket, base_dir: str = ".", runner_id: str = "", stream: str = "1") -> None:
        if not await _allow_local_websocket(websocket):
            return
        root = _resolve_workspace(base_dir)
        connection = BrowserLivePreviewConnection(websocket, root=root, runner_id=runner_id, stream_enabled=stream not in {"0", "false", "no"})
        await connection.run()

    return app


class BrowserLivePreviewConnection:
    """Stream a real Playwright Chromium page to the web UI and apply user input."""

    def __init__(self, websocket: WebSocket, *, root: Path, runner_id: str = "", stream_enabled: bool = True) -> None:
        self.websocket = websocket
        self.root = root
        self.runner_id = runner_id
        self.stream_enabled = bool(stream_enabled)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.outgoing: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.commands: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

    async def run(self) -> None:
        await self.websocket.accept()
        self.loop = asyncio.get_running_loop()
        self.worker = threading.Thread(target=self._browser_worker, name="juice-web-browser-live", daemon=True)
        self.worker.start()
        sender = asyncio.create_task(self._send_loop())
        try:
            while True:
                self.commands.put(await self.websocket.receive_json())
        except WebSocketDisconnect:
            logger.debug("browser live websocket disconnected")
        finally:
            self.stop_event.set()
            self.commands.put(None)
            await self.outgoing.put(None)
            await sender

    async def _send_loop(self) -> None:
        while True:
            message = await self.outgoing.get()
            if message is None:
                return
            await self.websocket.send_json(message)

    def _put(self, message: dict[str, Any]) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.outgoing.put(message), self.loop)

    def _browser_worker(self) -> None:
        token = ""
        try:
            key, session = _browser_live_session(self.root, self.runner_id)
            status = _browser_live_status(key, session)
            token = self._start_screencast_if_ready(session, token, status)
            self._put(status)
            while not self.stop_event.is_set():
                token = self._drain_browser_commands(key, session, token)
                time.sleep(0.01)
        except Exception as exc:
            logger.exception("browser live websocket failed")
            self._put({"type": "error", "message": str(exc)})
        finally:
            if token:
                try:
                    session.stop_screencast(token)
                except Exception:
                    logger.debug("failed to close browser live screencast", exc_info=True)

    def _start_screencast_if_ready(self, session: Any, token: str, status: dict[str, Any]) -> str:
        if token or not self.stream_enabled or not status.get("active_tab_id"):
            return token
        return str(session.start_screencast(lambda payload: self._put(payload)) or "")

    def _drain_browser_commands(self, key: str, session: Any, token: str) -> str:
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return token
            if command is None:
                self.stop_event.set()
                return token
            try:
                command_type = str(command.get("type") or "").strip()
                if token and command_type in {"switch_tab", "close_tab"}:
                    # A CDP screencast is bound to the page that was active when
                    # it started. Stop it before changing pages so the next
                    # status starts a stream for the new active page.
                    try:
                        session.stop_screencast(token)
                    except Exception:
                        logger.debug("failed to stop browser screencast before tab change", exc_info=True)
                    token = ""
                _apply_browser_live_command(session, command)
                status = _browser_live_status(key, session)
                token = self._start_screencast_if_ready(session, token, status)
                self._put(status)
            except Exception as exc:
                logger.warning("browser live command failed: %s", exc)
                if not token:
                    # A rejected switch/close must not leave the otherwise
                    # healthy active page without a live screencast.
                    try:
                        status = _browser_live_status(key, session)
                        token = self._start_screencast_if_ready(session, token, status)
                    except Exception:
                        logger.debug("failed to restore browser screencast after command error", exc_info=True)
                self._put({"type": "error", "message": str(exc)})


class WebSocketRuntimeConnection:
    """One browser connection with one active ``DirectRunnerRuntime`` shell."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.runtime: DirectRunnerRuntime | None = None
        self.outgoing: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pending_asks: dict[str, Future[dict[str, Any]]] = {}
        self.pending_ask_stream_ids: dict[str, str] = {}
        self.pending_asks_lock = threading.Lock()
        self._cancelled_stream_request_ids: set[str] = set()
        self._active_stream_request_id = ""
        self.stream_tasks: set[asyncio.Task[None]] = set()
        # Transport-level serialization avoids interleaving two websocket streams.
        # The SDK Runner also rejects concurrent requests as a final safety boundary.
        self._stream_mutex = asyncio.Lock()

    async def run(self) -> None:
        await self.websocket.accept()
        self.loop = asyncio.get_running_loop()
        sender = asyncio.create_task(self._send_loop())
        try:
            while True:
                command = await self.websocket.receive_json()
                await self._dispatch(command)
        except WebSocketDisconnect:
            logger.info("web gateway websocket disconnected")
        finally:
            for task in self.stream_tasks:
                task.cancel()
            self._cancel_pending_asks("websocket disconnected")
            if self.runtime is not None:
                self.runtime.stop_session(source="gateway_shutdown")
            await self.outgoing.put(None)
            await sender

    async def _send_loop(self) -> None:
        while True:
            message = await self.outgoing.get()
            if message is None:
                return
            await self.websocket.send_json(message)

    async def _dispatch(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("id") or "")
        command_type = str(command.get("type") or "")
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        if command_type == "answer_ask":
            await self._answer_ask(command_id, payload)
            return
        if command_type == "stream_message":
            task = asyncio.create_task(self._stream_message(command_id, payload))
            self.stream_tasks.add(task)
            task.add_done_callback(self.stream_tasks.discard)
            return
        if command_type == "cancel_stream":
            await self._cancel_stream(command_id, payload)
            return
        try:
            result = await asyncio.to_thread(self._handle_sync_command, command_type, payload)
            await self._send_result(command_id, result)
        except Exception as exc:
            logger.exception("web gateway command failed: %s", command_type)
            await self._send_error(command_id, str(exc))

    async def _cancel_stream(self, command_id: str, payload: dict[str, Any]) -> None:
        """Set the cooperative token, wake Ask, and acknowledge without a task barrier."""
        try:
            target = str(payload.get("request_id") or "").strip()
            is_active = self._cancel_stream_request(target)
            if is_active or not target:
                result = await asyncio.to_thread(self._handle_sync_command, "cancel_stream", payload)
            else:
                result = {"cancelled": True}
            logger.info("web stream cancellation requested: command_id=%s", command_id)
            await self._send_result(command_id, result)
        except Exception as exc:
            logger.exception("web gateway command failed: cancel_stream")
            await self._send_error(command_id, str(exc))

    def _handle_sync_command(self, command_type: str, payload: dict[str, Any]) -> Any:
        if command_type == "start_session":
            if self.runtime is not None:
                self.runtime.stop_session(source="runner_switch")
            self.runtime = self._new_runtime(payload)
            return _serialize_session_status(self.runtime.start_session())
        if command_type == "resume_session":
            if self.runtime is not None:
                self.runtime.stop_session(source="runner_switch")
            self.runtime = self._new_runtime(payload)
            return _serialize_session_status(self.runtime.resume_session(str(payload["runner_id"])))
        if command_type == "stop_session":
            if self.runtime is None:
                return {"stopped": False}
            return self.runtime.stop_session()
        if command_type == "cancel_stream":
            # 与 stop_session 区分：cancel_stream 只中断当前 streaming 回合，
            # 不持久化为 paused、不停止 async tasks，便于继续后续对话。
            if self.runtime is None:
                return {"cancelled": False}
            return self.runtime.request_cancel_stream()
        if command_type == "archive_runner":
            return _archive_web_runner(payload.get("base_dir") or ".", str(payload.get("runner_id") or ""))
        if command_type == "restore_runner":
            return _restore_web_runner(payload.get("base_dir") or ".", str(payload.get("runner_id") or ""))
        if command_type == "list_archived_sessions":
            return [_serialize_session(summary) for summary in _list_archived_web_sessions(payload.get("base_dir") or ".")]
        # Team manifests are static workspace declarations.  Keep these reads
        # ahead of `_ensure_runtime()` so `/teams` remains useful before the
        # first chat message and never creates an otherwise empty Runner.
        if command_type == "list_team_configs":
            return _serialize_team_list(_team_registry_for(payload.get("base_dir") or "."))
        if command_type == "get_team_config":
            team_name = str(payload.get("team_name") or "").strip()
            if not team_name:
                raise ValueError("team_name 不能为空")
            return _team_registry_for(payload.get("base_dir") or ".").get_manifest(team_name).to_dict()

        runtime = self._ensure_runtime(payload)
        if command_type == "switch_permission_mode":
            return _serialize_session_status(
                runtime.switch_permission_mode(
                    str(payload.get("permission_mode") or "default"),
                )
            )
        if command_type == "switch_agent_mode":
            return _serialize_session_status(
                runtime.switch_agent_mode(
                    str(payload.get("agent_mode") or "agent"),
                    agent_type=str(payload.get("agent_type") or runtime.agent_type),
                    model_name=payload.get("model_name"),
                    model_effort=payload.get("model_effort"),
                )
            )
        if command_type == "switch_model":
            return _serialize_session_status(
                runtime.switch_model(
                    str(payload["model_name"]),
                    model_effort=payload.get("model_effort"),
                )
            )
        if command_type == "list_models":
            return runtime.list_models()
        if command_type == "list_sessions":
            base_dir = payload.get("base_dir") or runtime.base_dir
            return [_serialize_session(summary) for summary in _list_web_sessions(base_dir)]
        if command_type == "describe_session":
            return _serialize_session_status(runtime.describe_session())
        if command_type == "list_skills":
            return runtime.list_skills(category=payload.get("category"))
        if command_type == "list_plugins":
            return runtime.list_plugins()
        if command_type == "list_available_agents":
            return runtime.list_available_agents(
                name=None if payload.get("name") is None else str(payload.get("name")),
                mode_id=None if payload.get("mode_id") is None else str(payload.get("mode_id")),
            )
        if command_type == "list_mode_resources":
            return runtime.list_mode_resources(
                mode_id=None if payload.get("mode_id") is None else str(payload.get("mode_id")),
            )
        if command_type == "view_skill":
            return runtime.view_skill(
                str(payload.get("name") or ""),
                file_path=payload.get("file_path"),
            )
        if command_type == "view_plugin":
            return runtime.view_plugin(
                str(payload.get("name") or ""),
                file_path=payload.get("file_path"),
            )
        if command_type == "skills_config_status":
            return runtime.skills_config_status()
        if command_type == "set_skills_config":
            raw_disabled = payload.get("disabled")
            disabled = raw_disabled if isinstance(raw_disabled, list) else []
            return runtime.set_skills_config(
                enabled=payload.get("enabled") if "enabled" in payload else None,
                disabled=[str(item) for item in disabled],
            )
        if command_type == "graphs_config_status":
            return runtime.graphs_config_status()
        if command_type == "self_evolution_config_status":
            return runtime.self_evolution_config_status()
        if command_type == "set_graphs_config":
            return runtime.set_graphs_config(enabled=bool(payload.get("enabled")))
        if command_type == "set_self_evolution_config":
            return runtime.set_self_evolution_config(enabled=bool(payload.get("enabled")))
        if command_type == "plugins_config_status":
            return runtime.plugins_config_status()
        if command_type == "set_plugins_config":
            raw_disabled = payload.get("disabled")
            disabled = raw_disabled if isinstance(raw_disabled, list) else []
            return runtime.set_plugins_config(
                enabled=payload.get("enabled") if "enabled" in payload else None,
                disabled=[str(item) for item in disabled],
            )
        if command_type == "set_image_config":
            return runtime.set_image_config(enabled=bool(payload.get("enabled")))
        if command_type == "memory_status":
            return runtime.memory_status()
        if command_type == "memory_search":
            return runtime.memory_search(
                str(payload.get("query") or ""),
                limit=int(payload.get("limit") or 20),
            )
        if command_type == "memory_view":
            return runtime.memory_view(str(payload.get("path") or "") or None)
        if command_type == "goal_status":
            return runtime.get_goal()
        if command_type == "get_goal":
            return runtime.get_goal()
        if command_type == "set_goal":
            return runtime.set_goal(
                str(payload.get("objective") or ""),
                max_turns=payload.get("max_turns"),
                max_runtime_seconds=payload.get("max_runtime_seconds"),
            )
        if command_type == "pause_goal":
            return runtime.pause_goal()
        if command_type == "resume_goal":
            return runtime.resume_goal()
        if command_type == "clear_goal":
            return runtime.clear_goal()
        if command_type == "create_cron_task":
            return runtime.create_cron_task(
                cron=str(payload.get("cron") or ""),
                prompt=str(payload.get("prompt") or ""),
                recurring=bool(payload.get("recurring", True)),
            )
        if command_type == "list_cron_tasks":
            return runtime.list_cron_tasks()
        if command_type == "delete_cron_task":
            return runtime.delete_cron_task(str(payload.get("id") or payload.get("task_id") or ""))
        if command_type == "cron_status":
            return runtime.cron_status()
        if command_type == "describe_agent_sessions":
            return runtime.describe_agent_sessions()
        if command_type == "send_agent_message":
            return runtime.send_agent_message(
                agent_name=str(payload.get("agent_name") or ""),
                message=str(payload.get("message") or ""),
            )
        if command_type == "interrupt_agent":
            return runtime.interrupt_agent(
                agent_name=str(payload.get("agent_name") or ""),
                reason=str(payload.get("reason") or "user_agent_interrupt"),
            )
        raise ValueError(f"unknown websocket command: {command_type}")

    async def _stream_message(self, command_id: str, payload: dict[str, Any]) -> None:
        try:
            # Websocket commands are serialized before entering the SDK Runner.
            async with self._stream_mutex:
                with self.pending_asks_lock:
                    if command_id in self._cancelled_stream_request_ids:
                        self._cancelled_stream_request_ids.discard(command_id)
                        skipped = True
                    else:
                        self._active_stream_request_id = command_id
                        skipped = False
                if skipped:
                    logger.info("skip cancelled queued web stream: request_id=%s", command_id)
                else:
                    try:
                        await asyncio.to_thread(self._run_stream_worker, command_id, payload)
                    finally:
                        with self.pending_asks_lock:
                            if self._active_stream_request_id == command_id:
                                self._active_stream_request_id = ""
                            self._cancelled_stream_request_ids.discard(command_id)
            result = (
                {"done": True, "stopped": True, "stop_reason": "user_cancelled"}
                if skipped
                else self._current_stream_result()
            )
            await self._send_result(command_id, result)
        except Exception as exc:
            logger.exception("web gateway stream failed")
            await self._send_error(command_id, str(exc))

    def _current_stream_result(self) -> dict[str, Any]:
        runner = getattr(self.runtime, "_runner", None) if self.runtime is not None else None
        stopped = False
        stop_reason = ""
        if runner is not None:
            getter = getattr(runner, "last_stream_was_cancelled", None)
            if callable(getter):
                try:
                    stopped = bool(getter())
                except Exception:
                    stopped = False
            stop_reason = str(getattr(runner, "stop_reason", None) or "")
        return {"done": True, "stopped": stopped, "stop_reason": stop_reason}

    def _run_stream_worker(self, command_id: str, payload: dict[str, Any]) -> None:
        runtime = self._ensure_runtime(payload)
        message = str(payload.get("message") or "")
        agent_mode_override = payload.get("agent_mode_override")
        for event in runtime.stream_message(
            message,
            agent_mode_override=(
                None if agent_mode_override is None else str(agent_mode_override)
            ),
            cancel_check=lambda: self._stream_request_is_cancelled(command_id),
        ):
            serialized = serialize_runner_stream_event(event)
            try:
                serialized["agent_sessions_report"] = runtime.describe_agent_sessions()
            except Exception:
                serialized["agent_sessions_report"] = None
            self._put_threadsafe({"id": command_id, "type": "stream_step", "payload": serialized})

    def _new_runtime(self, payload: dict[str, Any]) -> DirectRunnerRuntime:
        return DirectRunnerRuntime(
            base_dir=payload.get("base_dir", "."),
            permission_mode=payload.get("permission_mode", "default"),
            agent_mode=payload.get("agent_mode", "agent"),
            agent_type=payload.get("agent_type", "react"),
            runtime_config_path=payload.get("runtime_config_path"),
            model_name=payload.get("model_name", "doubao_lite"),
            model_effort=payload.get("model_effort", "disabled"),
            ask_handler=self._handle_ask_request,
        )

    def _ensure_runtime(self, payload: dict[str, Any]) -> DirectRunnerRuntime:
        if self.runtime is None:
            self.runtime = self._new_runtime(payload)
        return self.runtime

    def _handle_ask_request(self, request: AskRequest) -> dict[str, Any]:
        request_id = str(request.get("request_id") or "").strip()
        future: Future[dict[str, Any]] = Future()
        with self.pending_asks_lock:
            # Ask 不一定在 stream worker 本身执行：权限审批或嵌套工具可能在
            # agent 的工具线程池中调用 ask handler。threading.local 无法跨线程
            # 传播 request ID；Web stream 已由 _stream_mutex 串行化，因此锁内的
            # active request 才是 Ask 归属的唯一可靠真相源。
            stream_request_id = self._active_stream_request_id
            if stream_request_id and stream_request_id in self._cancelled_stream_request_ids:
                return dict(
                    normalize_ask_response(
                        {
                            "status": "cancelled",
                            "request_id": request_id,
                            "error": "cancelled by user",
                        },
                        request=request,
                    )
                )
            self.pending_asks[request_id] = future
            self.pending_ask_stream_ids[request_id] = stream_request_id
        logger.info(
            "registered web Ask: ask_request_id=%s stream_request_id=%s",
            request_id,
            stream_request_id or "<ambient>",
        )
        self._put_threadsafe({"id": stream_request_id, "type": "ask_request", "payload": dict(request)})
        try:
            return dict(normalize_ask_response(future.result(), request=request))
        finally:
            with self.pending_asks_lock:
                self.pending_asks.pop(request_id, None)
                self.pending_ask_stream_ids.pop(request_id, None)

    async def _answer_ask(self, command_id: str, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("request_id") or "").strip()
        with self.pending_asks_lock:
            future = self.pending_asks.get(request_id)
        if future is None:
            await self._send_error(command_id, f"unknown ask request: {request_id}")
            return
        future.set_result(dict(payload.get("response") or {}))
        await self._send_result(command_id, {"accepted": True})

    def _cancel_pending_asks(self, reason: str, *, status: str = "error") -> None:
        with self.pending_asks_lock:
            futures = list(self.pending_asks.values())
            self.pending_asks.clear()
            self.pending_ask_stream_ids.clear()
        for future in futures:
            if not future.done():
                future.set_result({"status": status, "error": reason})
        if futures:
            logger.info("woke %d pending web Ask request(s): status=%s", len(futures), status)

    def _cancel_stream_request(self, request_id: str) -> bool:
        """Atomically mark a stream cancelled and wake only its registered Ask."""
        target = str(request_id or "").strip()
        futures: list[Future[dict[str, Any]]] = []
        with self.pending_asks_lock:
            if target:
                self._cancelled_stream_request_ids.add(target)
                for ask_id, stream_id in list(self.pending_ask_stream_ids.items()):
                    if stream_id == target:
                        future = self.pending_asks.pop(ask_id, None)
                        self.pending_ask_stream_ids.pop(ask_id, None)
                        if future is not None:
                            futures.append(future)
            else:
                futures = list(self.pending_asks.values())
                self.pending_asks.clear()
                self.pending_ask_stream_ids.clear()
            is_active = bool(target and target == self._active_stream_request_id)
        for future in futures:
            if not future.done():
                future.set_result({"status": "cancelled", "error": "cancelled by user"})
        if futures:
            logger.info("woke %d pending web Ask request(s): status=cancelled", len(futures))
        return is_active

    def _stream_request_is_cancelled(self, request_id: str) -> bool:
        with self.pending_asks_lock:
            return request_id in self._cancelled_stream_request_ids

    def _put_threadsafe(self, message: dict[str, Any]) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.outgoing.put(message), self.loop)

    async def _send_result(self, command_id: str, payload: Any) -> None:
        await self.outgoing.put({"id": command_id, "type": "result", "payload": payload})

    async def _send_error(self, command_id: str, message: str) -> None:
        await self.outgoing.put({"id": command_id, "type": "error", "error": {"message": message}})


def _runtime_for(base_dir: str | Path) -> DirectRunnerRuntime:
    return DirectRunnerRuntime(base_dir=base_dir)


def _team_registry_for(base_dir: str | Path) -> TeamRegistry:
    """Access Team declarations without constructing or configuring a Runner."""

    return TeamRegistry(config_context=ConfigurationContext.from_workspace(base_dir))


def _serialize_team_list(registry: TeamRegistry) -> dict[str, Any]:
    """Return the one Team-list payload shared by HTTP and WebSocket queries."""

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


def _serialize_session(summary: Any) -> dict[str, Any]:
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


def _serialize_session_status(status: Any) -> dict[str, Any]:
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
    }


def _list_web_sessions(base_dir: str | Path) -> list[Any]:
    """Return sessions visible in the Web task list.

    Web archiving is deliberately a presentation-layer feature. The shared
    stdio ``list_sessions`` helper remains untouched so CLI history keeps the
    complete runner list.
    """

    return [summary for summary in list_sessions(base_dir) if not _is_web_archived(summary)]


def _list_archived_web_sessions(base_dir: str | Path) -> list[Any]:
    """Return Web-archived sessions for the settings restore view."""

    return [summary for summary in list_sessions(base_dir) if _is_web_archived(summary)]


def _is_web_archived(summary: Any) -> bool:
    """Read the Web archive marker defensively.

    A malformed marker still hides the runner because the user has already
    requested archive semantics; restore simply removes the marker.
    """

    marker = Path(getattr(summary, "root_dir", "")) / WEB_ARCHIVE_MARKER
    if not marker.exists():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    return bool(payload.get("archived", True))


def _archive_web_runner(base_dir: str | Path, runner_id: str) -> dict[str, Any]:
    """Archive one runner for the Web UI without deleting persisted state."""

    root = _resolve_workspace(base_dir)
    marker = _web_archive_marker(root, runner_id)
    marker.write_text(
        json.dumps(
            {
                "archived": True,
                "archived_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "source": "juice-web",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    logger.info("web runner archived: base_dir=%s runner_id=%s", root, runner_id)
    return {"archived": True, "runner_id": _safe_runner_id(runner_id)}


def _restore_web_runner(base_dir: str | Path, runner_id: str) -> dict[str, Any]:
    """Restore one Web-archived runner by removing its archive marker."""

    root = _resolve_workspace(base_dir)
    marker = _web_archive_marker(root, runner_id)
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    logger.info("web runner restored: base_dir=%s runner_id=%s", root, runner_id)
    return {"restored": True, "runner_id": _safe_runner_id(runner_id)}


def _web_archive_marker(root: Path, runner_id: str) -> Path:
    safe_runner_id = _safe_runner_id(runner_id)
    runner_dir = root / ".juice" / "runners" / safe_runner_id
    if not runner_dir.exists() or not runner_dir.is_dir() or not (runner_dir / "manifest.json").exists():
        raise FileNotFoundError(f"runner not found: {safe_runner_id}")
    return runner_dir / WEB_ARCHIVE_MARKER


def _safe_runner_id(runner_id: str) -> str:
    value = str(runner_id or "").strip()
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError("invalid runner_id")
    return value


def _resolve_workspace(base_dir: str | Path) -> Path:
    return Path(base_dir).expanduser().resolve()


def _resolve_inside(root: Path, requested_path: str | Path) -> Path:
    target = (root / str(requested_path)).expanduser().resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=403, detail="path escapes workspace")
    return target


def _relative_posix(root: Path, target: Path) -> str:
    return target.relative_to(root).as_posix()


def _is_file_tree_directory(path: Path) -> bool:
    """Return whether the file tree may safely expand this path as a directory."""

    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def _build_file_tree(root: Path, current: Path, *, max_depth: int, budget: list[int]) -> dict[str, Any]:
    is_directory = _is_file_tree_directory(current)
    node = {
        "name": current.name or str(current),
        "path": "" if current == root else _relative_posix(root, current),
        "type": "directory" if is_directory else "file",
    }
    if not is_directory or max_depth <= 0 or budget[0] <= 0:
        return node

    children: list[dict[str, Any]] = []
    try:
        entries = sorted(current.iterdir(), key=lambda item: (not _is_file_tree_directory(item), item.name.lower()))
    except OSError:
        entries = []
    for entry in entries:
        if budget[0] <= 0:
            break
        if entry.is_dir() and entry.name in IGNORED_DIRS:
            continue
        budget[0] -= 1
        children.append(_build_file_tree(root, entry, max_depth=max_depth - 1, budget=budget))
    node["children"] = children
    return node


def _latest_browser_image(root: Path, *, runner_id: str = "") -> Path | None:
    juice = root / ".juice" / "runners"
    if not juice.exists():
        return None
    candidates: list[Path] = []
    runner_glob = _safe_runner_glob(runner_id)
    candidates.extend(
        path for path in juice.glob(f"{runner_glob}/browser_screenshots/*") if path.suffix.lower() in PREVIEW_IMAGE_SUFFIXES
    )
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _safe_runner_glob(runner_id: str) -> str:
    value = str(runner_id or "").strip()
    if not value:
        return "*"
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid runner_id")
    return value


def _is_safe_browser_runner_id(runner_id: str) -> bool:
    value = str(runner_id or "").strip()
    return bool(value) and "/" not in value and "\\" not in value and value not in {".", ".."}


def _browser_session_keys(root: Path, runner_id: str = "", *, include_global_fallback: bool = True) -> list[str]:
    keys = ["default", str(root)]
    value = str(runner_id or "").strip()
    if value and "/" not in value and "\\" not in value and value not in {".", ".."}:
        runner_keys = [
            f"{value}:browser",
            f"{value}:root",
            f"{value}:general",
            f"{value}:agent",
        ]
        return [*runner_keys, *keys] if include_global_fallback else runner_keys
    return keys


def _active_browser_session(
    root: Path,
    runner_id: str = "",
    *,
    include_global_fallback: bool = True,
) -> tuple[str, Any] | tuple[str, None]:
    for key in _browser_session_keys(root, runner_id, include_global_fallback=include_global_fallback):
        session = get_existing_browser_session(key)
        if session is not None:
            return key, session
    return "", None


def _web_browser_session(root: Path, *, headless: bool = True) -> Any:
    state_root = root / ".juice" / "web_browser"
    return get_browser_session(
        "default",
        headless=headless,
        state_dir=state_root / "browser_state",
        recording_dir=state_root / "browser_recordings",
    )


def _browser_live_session(root: Path, runner_id: str = "", *, headless: bool = True) -> tuple[str, Any]:
    if runner_id and not _is_safe_browser_runner_id(runner_id):
        raise ValueError("invalid runner_id")
    key, session = _active_browser_session(root, runner_id, include_global_fallback=not bool(runner_id))
    if session is not None:
        return key, session
    keys = _browser_session_keys(root, runner_id)
    if runner_id and keys and keys[0].startswith(f"{runner_id}:"):
        key = keys[0]
        state_root = root / ".juice" / "runners" / runner_id
        return key, get_browser_session(
            key,
            headless=headless,
            state_dir=state_root / "browser_state",
            recording_dir=state_root / "browser_recordings",
        )
    return "default", _web_browser_session(root, headless=headless)


def _browser_live_preview(root: Path, runner_id: str = "") -> dict[str, Any]:
    key, session = _active_browser_session(root, runner_id, include_global_fallback=not bool(runner_id))
    if session is None:
        screenshot = _latest_browser_image(root, runner_id=runner_id)
        if screenshot is None:
            return {
                "connected": False,
                "title": "",
                "url": "",
                "image_url": "",
                "session_key": "",
                "tabs": [],
                "active_tab_id": "",
                "can_go_back": False,
                "can_go_forward": False,
                "viewport": {"width": 1280, "height": 900},
                "stream_state": "empty",
            }
        rel = _relative_posix(root, screenshot)
        return {
            "connected": True,
            "title": screenshot.name,
            "url": "",
            "image_url": f"/api/browser/asset?base_dir={quote(str(root))}&path={quote(rel)}",
            "session_key": "artifact",
            "tabs": [],
            "active_tab_id": "",
            "can_go_back": False,
            "can_go_forward": False,
            "viewport": {"width": 1280, "height": 900},
            "stream_state": "artifact",
        }
    try:
        return _browser_live_status(key, session)
    except Exception as exc:
        logger.warning("failed to render live browser preview: %s", exc)
        return {"connected": False, "title": "", "url": "", "image_url": "", "error": str(exc), "session_key": key, "stream_state": "error"}


def _browser_live_open_external(root: Path, runner_id: str, url: str = "") -> dict[str, Any]:
    if not _has_visible_browser_display():
        raise RuntimeError(
            "无法打开外部可见 Chromium：当前 gateway 进程没有 DISPLAY/WAYLAND_DISPLAY 图形环境。"
            "请在有桌面或远程桌面的终端中启动 ./juice-web；当前环境只能使用右侧 canvas live preview。"
        )
    key, session = _browser_live_session(root, runner_id, headless=False)
    if url.strip():
        session.open_url(url)
    if not hasattr(session, "ensure_visible_window"):
        raise RuntimeError("current browser session does not support visible Chromium windows")
    session.ensure_visible_window()
    status = _browser_live_status(key, session)
    status["stream_state"] = "external"
    status["external_window"] = True
    return status


def _browser_live_new_tab(root: Path, runner_id: str, url: str = "", make_active: bool = True) -> dict[str, Any]:
    key, session = _browser_live_session(root, runner_id)
    session.new_tab(url.strip() or None, make_active=make_active)
    return _browser_live_status(key, session)


def _has_visible_browser_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _browser_live_status(session_key: str, session: Any) -> dict[str, Any]:
    return _run_browser(session, lambda: _browser_live_status_on_owner(session_key, session))


def _browser_live_status_on_owner(session_key: str, session: Any) -> dict[str, Any]:
    snapshot = _browser_live_snapshot_on_owner(session)
    active_tab_id = str(snapshot.get("active_tab_id") or "")
    active = next((tab for tab in snapshot.get("tabs", []) if tab.get("active")), {})
    try:
        active_page = snapshot.get("active_page")
        can_go_back = bool(active_page.evaluate("() => window.history.length > 1")) if active_page is not None else False
    except Exception:
        logger.debug("failed to inspect browser history", exc_info=True)
        can_go_back = False
    return {
        "type": "status",
        "protocol": 2,
        "connected": True,
        "title": str(active.get("title") or "Browser"),
        "url": str(active.get("url") or ""),
        "image_url": "",
        "session_key": session_key,
        "tabs": snapshot.get("tabs", []),
        "active_tab_id": active_tab_id,
        "can_go_back": can_go_back,
        "can_go_forward": False,
        "viewport": {"width": getattr(session, "viewport_width", 1280), "height": getattr(session, "viewport_height", 900)},
        "headless": bool(getattr(session, "headless", True)),
        "external_window": not bool(getattr(session, "headless", True)),
        "stream_state": "live" if active_tab_id else "idle",
    }


def _browser_live_snapshot_on_owner(session: Any) -> dict[str, Any]:
    snapshot_reader = getattr(session, "tab_snapshot", None)
    tabs_obj = getattr(session, "_tabs", None)
    if isinstance(tabs_obj, dict):
        if callable(snapshot_reader):
            snapshot = snapshot_reader()
            tabs_obj = getattr(session, "_tabs", {})
            active_tab_id = str(snapshot.get("active_tab_id") or "")
            tabs = list(snapshot.get("tabs", []))
        else:
            active_tab_id = str(getattr(session, "_active_tab_id", "") or "")
            tabs = []
        active_page = None
        for tab_id, page in tabs_obj.items():
            is_active = str(tab_id) == active_tab_id
            if is_active:
                active_page = page
            if callable(snapshot_reader):
                continue
            tabs.append(
                {
                    "tab_id": str(tab_id),
                    "url": str(getattr(page, "url", "") or ""),
                    "title": _as_browser_title(page),
                    "active": is_active,
                }
            )
        return {"active_tab_id": active_tab_id, "tabs": tabs, "active_page": active_page}
    status = session.status()
    active_tab_id = str(status.get("active_tab_id") or "")
    return {"active_tab_id": active_tab_id, "tabs": status.get("tabs", []), "active_page": None}


def _browser_live_active_page_on_owner(session: Any) -> Any | None:
    snapshot = _browser_live_snapshot_on_owner(session)
    return snapshot.get("active_page")


def _browser_live_tab_exists_on_owner(session: Any, tab_id: str) -> bool:
    wanted = str(tab_id or "").strip()
    if not wanted:
        return False
    return any(str(tab.get("tab_id") or "") == wanted for tab in _browser_live_snapshot_on_owner(session).get("tabs", []))


def _as_browser_title(page: Any) -> str:
    try:
        return str(page.title() or "")
    except Exception:
        logger.debug("failed to read browser page title", exc_info=True)
        return ""


def _apply_browser_live_command(session: Any, command: dict[str, Any]) -> None:
    _run_browser(session, lambda: _apply_browser_live_command_on_owner(session, command))


def _run_browser(session: Any, callback: Callable[[], Any]) -> Any:
    runner = getattr(session, "run_on_browser_thread", None)
    return runner(callback) if callable(runner) else callback()


def _apply_browser_live_command_on_owner(session: Any, command: dict[str, Any]) -> None:
    command_type = str(command.get("type") or "").strip()
    if command_type == "switch_tab":
        tab_id = str(command.get("tab_id") or "")
        if _browser_live_tab_exists_on_owner(session, tab_id):
            session.switch_tab(tab_id)
        return
    if command_type == "close_tab":
        tab_id = str(command.get("tab_id") or "")
        if not _browser_live_tab_exists_on_owner(session, tab_id):
            raise ValueError(f"unknown browser tab_id: {tab_id}")
        session.close_tab(tab_id)
        return
    if command_type == "navigate":
        session.open_url(str(command.get("url") or ""))
        return
    page = _browser_live_active_page_on_owner(session)
    if page is None:
        return
    if command_type == "back":
        session.go_back()
        return
    if command_type == "forward":
        page.go_forward(wait_until="domcontentloaded")
        return
    if command_type == "refresh":
        page.reload(wait_until="domcontentloaded")
        return
    if command_type == "mouse":
        event = str(command.get("event") or "").strip()
        x = float(command.get("x") or 0)
        y = float(command.get("y") or 0)
        button = _browser_mouse_button(command.get("button"))
        if event == "move":
            page.mouse.move(x, y)
        elif event == "down":
            page.mouse.move(x, y)
            page.mouse.down(button=button)
        elif event == "up":
            page.mouse.move(x, y)
            page.mouse.up(button=button)
        elif event == "dblclick":
            page.mouse.dblclick(x, y, button=button)
        else:
            raise ValueError(f"unknown browser mouse event: {event}")
        return
    if command_type == "click":
        page.mouse.click(float(command.get("x") or 0), float(command.get("y") or 0), button=_browser_mouse_button(command.get("button")))
        return
    if command_type == "wheel":
        x = float(command.get("x") or 0)
        y = float(command.get("y") or 0)
        page.mouse.move(x, y)
        page.mouse.wheel(float(command.get("delta_x") or 0), float(command.get("delta_y") or 0))
        return
    if command_type == "key":
        text = command.get("text")
        key = str(command.get("key") or "")
        modifiers = [str(item) for item in command.get("modifiers", []) if str(item)]
        if isinstance(text, str) and text:
            page.keyboard.type(text)
        elif key:
            page.keyboard.press("+".join([*modifiers, key]) if modifiers else key)
        return
    if command_type == "paste":
        text = str(command.get("text") or "")
        try:
            page.keyboard.insert_text(text)
        except Exception:
            page.keyboard.type(text)
        return
    if command_type == "move":
        page.mouse.move(float(command.get("x") or 0), float(command.get("y") or 0))
        return
    raise ValueError(f"unknown browser live command: {command_type}")


def _browser_mouse_button(value: Any) -> str:
    button = str(value or "left").strip().lower()
    return button if button in {"left", "right", "middle"} else "left"


def _guess_language(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return {
        "py": "python",
        "ts": "typescript",
        "tsx": "tsx",
        "js": "javascript",
        "jsx": "jsx",
        "md": "markdown",
        "json": "json",
        "yaml": "yaml",
        "yml": "yaml",
        "css": "css",
        "html": "html",
    }.get(suffix, suffix or "text")
