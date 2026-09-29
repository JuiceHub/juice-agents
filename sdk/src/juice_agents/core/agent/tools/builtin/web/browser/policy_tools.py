"""Policy helpers for browser tools.

Browser tools can interact with logged-in web applications. This module keeps
host allow/block checks, high-risk feature gates, and user confirmation logic in
one place instead of scattering it across individual tool classes.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from juice_agents.core.config.runtime_config import read_runtime_config
from juice_agents.core.runner.types.ask import normalize_ask_response

from .session import (
    DEFAULT_BROWSER_RECORDINGS_DIR,
    DEFAULT_BROWSER_STATE_DIR,
    PlaywrightBrowserSession,
    get_browser_session,
)
from ....runtime.base_tools import CONTENT_OBSERVATION_CHARS, Tool

logger = logging.getLogger(__name__)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SENSITIVE_KEYWORDS = (
    "password",
    "passwd",
    "token",
    "secret",
    "验证码",
    "密码",
    "令牌",
    "delete",
    "remove",
    "purchase",
    "buy",
    "send",
    "submit",
    "save",
    "删除",
    "发送",
    "购买",
    "保存",
    "提交",
)


@dataclass(slots=True)
class BrowserPolicy:
    enabled: bool = True
    backend: str = "in_app"
    default_policy: str = "ask_for_sensitive"
    allow_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    block_hosts: tuple[str, ...] = ()
    risky_enabled: bool = False
    risky_require_confirmation: bool = True
    js_timeout_ms: int = 3000
    max_eval_result_chars: int = 20000
    storage_state_dir: str = DEFAULT_BROWSER_STATE_DIR
    recording_dir: str = DEFAULT_BROWSER_RECORDINGS_DIR


def _normalize_hosts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple()
    return tuple(str(item).strip().lower() for item in value if str(item).strip())


def load_browser_policy(agent: Any | None = None) -> BrowserPolicy:
    """Read browser policy from runtime/workspace config when available."""

    config_context = getattr(agent, "_declared_config_context", None)
    config_path = getattr(config_context, "project_config_path", None)
    workspace_dir = getattr(config_context, "workspace_dir", None)
    try:
        payload = read_runtime_config(config_path, workspace_dir=workspace_dir)
    except Exception:
        payload = {}
    raw_browser = payload.get("browser") if isinstance(payload, dict) else {}
    raw_browser = raw_browser if isinstance(raw_browser, dict) else {}
    raw_permissions = raw_browser.get("permissions") if isinstance(raw_browser.get("permissions"), dict) else {}
    raw_risky = raw_browser.get("risky_tools") if isinstance(raw_browser.get("risky_tools"), dict) else {}
    return BrowserPolicy(
        enabled=bool(raw_browser.get("enabled", True)),
        backend=str(raw_browser.get("backend") or "in_app").strip() or "in_app",
        default_policy=str(raw_permissions.get("default_policy") or "ask_for_sensitive").strip(),
        allow_hosts=_normalize_hosts(raw_permissions.get("allow_hosts")) or ("localhost", "127.0.0.1"),
        block_hosts=_normalize_hosts(raw_permissions.get("block_hosts")),
        risky_enabled=bool(raw_risky.get("enabled", False)),
        risky_require_confirmation=bool(raw_risky.get("require_confirmation", True)),
        js_timeout_ms=int(raw_risky.get("js_timeout_ms") or 3000),
        max_eval_result_chars=int(raw_risky.get("max_eval_result_chars") or 20000),
        storage_state_dir=str(raw_risky.get("storage_state_dir") or DEFAULT_BROWSER_STATE_DIR),
        recording_dir=str(raw_risky.get("recording_dir") or DEFAULT_BROWSER_RECORDINGS_DIR),
    )


def _host_from_url(url: str) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def _host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    if not host:
        return False
    for pattern in patterns:
        if host == pattern or host.endswith("." + pattern):
            return True
    return False


def is_local_url(url: str) -> bool:
    return _host_from_url(url) in LOCAL_HOSTS


def looks_sensitive(*values: Any) -> bool:
    text = " ".join(str(value or "").lower() for value in values)
    return any(keyword.lower() in text for keyword in SENSITIVE_KEYWORDS)


class BrowserToolMixin:
    """Shared owner binding, session lookup, and policy checks for browser tools."""

    owner_agent: Any | None = None
    is_risky_tool = False

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _policy(self) -> BrowserPolicy:
        return load_browser_policy(self.owner_agent)

    def _runner_root(self) -> Path:
        context = getattr(self.owner_agent, "runner_context", None)
        layout = getattr(context, "layout", None)
        root_dir = getattr(layout, "root_dir", None)
        if root_dir is not None:
            return Path(root_dir)
        config_context = getattr(self.owner_agent, "_declared_config_context", None)
        juice_root = getattr(config_context, "juice_root", None)
        return Path(juice_root) if juice_root is not None else Path(".juice")

    def _session_key(self) -> str:
        context = getattr(self.owner_agent, "runner_context", None)
        runner_id = getattr(context, "runner_id", None)
        if runner_id:
            return f"{runner_id}:browser"
        config_context = getattr(self.owner_agent, "_declared_config_context", None)
        workspace_dir = getattr(config_context, "workspace_dir", None)
        return str(workspace_dir or "default")

    def _session(self) -> PlaywrightBrowserSession:
        policy = self._policy()
        root = self._runner_root()
        session = get_browser_session(
            self._session_key(),
            state_dir=root / "browser_state",
            recording_dir=root / "browser_recordings",
        )
        session.reconfigure(
            state_dir=Path(policy.storage_state_dir)
            if Path(policy.storage_state_dir).is_absolute()
            else root / Path(policy.storage_state_dir).name,
            recording_dir=Path(policy.recording_dir)
            if Path(policy.recording_dir).is_absolute()
            else root / Path(policy.recording_dir).name,
        )
        return session

    def _ask_confirmation(self, *, action: str, reason: str, url: str = "") -> None:
        context = getattr(self.owner_agent, "runner_context", None)
        ask_user = getattr(context, "ask_user", None)
        if not callable(ask_user):
            raise PermissionError(f"浏览器动作需要用户确认，但当前没有交互通道: {reason}")
        request = {
            "request_id": f"browser-{uuid4().hex[:12]}",
            "question": f"是否允许浏览器执行高风险动作：{action}？\n原因：{reason}\nURL：{url or 'unknown'}",
            "options": [
                {"label": "允许", "value": "allow", "description": "执行本次浏览器动作"},
                {"label": "拒绝", "value": "deny", "description": "取消本次浏览器动作"},
            ],
            "multiple": False,
            "allow_custom": False,
        }
        response = normalize_ask_response(ask_user(request), request=request)
        selected = {str(item.get("value") or item.get("label") or "") for item in response.get("selected", [])}
        if response.get("status") != "answered" or "allow" not in selected:
            raise PermissionError(f"用户未批准浏览器动作: {action}")

    def _check_host(self, url: str) -> None:
        policy = self._policy()
        if not policy.enabled:
            raise PermissionError("browser.enabled=false，浏览器工具已禁用")
        if policy.backend != "in_app":
            raise PermissionError(f"当前仅支持 in_app 浏览器 backend，配置值: {policy.backend}")
        host = _host_from_url(url)
        if _host_matches(host, policy.block_hosts):
            raise PermissionError(f"浏览器访问被 block_hosts 拒绝: {host}")
        if policy.allow_hosts and not (_host_matches(host, policy.allow_hosts) or host in LOCAL_HOSTS):
            if policy.default_policy == "deny":
                raise PermissionError(f"浏览器访问未在 allow_hosts 中: {host}")

    def _check_mutation(self, *, action: str, selector: str = "", value: str = "", url: str = "") -> None:
        policy = self._policy()
        self._check_host(url)
        if is_local_url(url):
            return
        if policy.default_policy == "ask_always" or looks_sensitive(action, selector, value):
            self._ask_confirmation(action=action, reason="非本地站点敏感浏览器写操作", url=url)

    def _check_risky(self, *, action: str, url: str = "") -> BrowserPolicy:
        policy = self._policy()
        if not policy.risky_enabled:
            raise PermissionError("高风险浏览器工具默认关闭；请在 browser.risky_tools.enabled=true 后再使用")
        self._check_host(url)
        if policy.risky_require_confirmation:
            self._ask_confirmation(action=action, reason="高风险浏览器能力", url=url)
        return policy


class BrowserTool(BrowserToolMixin, Tool):
    """Base class for browser tool classes."""

    max_observation_chars = CONTENT_OBSERVATION_CHARS


__all__ = [
    "BrowserPolicy",
    "BrowserTool",
    "BrowserToolMixin",
    "is_local_url",
    "load_browser_policy",
    "looks_sensitive",
]
