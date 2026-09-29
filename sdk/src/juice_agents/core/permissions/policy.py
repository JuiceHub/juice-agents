"""Central allow/ask/deny decisions for Juice tool execution."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.config.runtime_config import update_workspace_config
from juice_agents.core.registry.graphs import GraphRegistry
from juice_agents.core.runner.types.ask import normalize_ask_response
from juice_agents.core.agent.tools.builtin.evolution.constants import SELF_EVOLUTION_TOOL_NAMES
from juice_agents.core.agent.tools.builtin.graphs.constants import GRAPH_TOOL_NAMES
from juice_agents.core.agent.tools.builtin.capabilities.manual_capabilities import (
    graphs_enabled,
    self_evolution_enabled,
)

PermissionBehavior = Literal["allow", "ask", "deny"]
# 权限维度：只管审批策略，不含任何执行模式语义。
PermissionMode = Literal["default", "accept"]
# 执行模式维度：决定 root agent 形态与工具面约束（如 plan 只读）。
AgentMode = str


@dataclass(frozen=True, slots=True)
class PermissionResult:
    """工具 check_permissions 返回的统一决策结构。"""

    behavior: Literal["allow", "deny", "ask", "passthrough"]
    reason: str = ""
    updated_args: dict[str, Any] | None = None


_EDIT_TOOLS = {"write", "edit", "graph_manage"}
_APPROVAL_TOOLS = _EDIT_TOOLS | {
    "shell",
    "python",
    "graph_tool",
    # 配置与 Skill 变更会永久改变后续 agent 行为，不能静默放行。
    "skill_manage",
    "agent_manage",
    "tool_manage",
}
_SHARED_CONFIG_WRITE_TOOLS = frozenset({"tool_manage", "skill_manage", "graph_manage"})


class PermissionDenied(PermissionError):
    """Raised before a tool executes when policy does not allow it."""


@dataclass(frozen=True, slots=True)
class PermissionAction:
    tool: str
    target: str
    source_hash: str
    is_read_only: bool

    def to_rule(self, behavior: str = "allow") -> dict[str, str]:
        return {
            "behavior": behavior,
            "tool": self.tool,
            "target": self.target,
            "source_hash": self.source_hash,
        }


def _normalize_mode(value: Any) -> PermissionMode:
    mode = str(value or "default").strip()
    if mode not in {"default", "accept"}:
        return "default"
    return mode  # type: ignore[return-value]


def _normalize_agent_mode(value: Any) -> AgentMode:
    mode = str(value or "agent").strip()
    return mode or "agent"


def _runner_for_agent(agent: Any) -> Any | None:
    context = getattr(agent, "runner_context", None)
    return getattr(context, "runner", None)


def _config_context(agent: Any) -> ConfigurationContext:
    runner = _runner_for_agent(agent)
    context = getattr(runner, "config_context", None)
    if isinstance(context, ConfigurationContext):
        return context
    declared = getattr(agent, "_declared_config_context", None)
    if isinstance(declared, ConfigurationContext):
        return declared
    return ConfigurationContext.from_workspace(getattr(agent, "runner_default_base_dir", None) or Path.cwd())


def _target_for(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "shell":
        return str(args.get("command") or "").strip()
    if tool_name == "python":
        return hashlib.sha256(str(args.get("code") or "").encode("utf-8")).hexdigest()
    if tool_name in {"write", "edit", "read"}:
        return str(args.get("path") or args.get("file_path") or "").strip()
    if tool_name in {"graph_tool", "graph_manage", "graph_view"}:
        return str(args.get("name") or "").strip()
    if tool_name in {"agent_manage", "tool_manage", "skill_manage"}:
        action = str(args.get("action") or "").strip()
        name = str(args.get("name") or "").strip()
        return f"{action}:{name}" if action else name
    if tool_name.startswith("browser_"):
        return str(args.get("url") or args.get("selector") or "").strip()
    return ""


def _source_hash(agent: Any, tool_name: str, args: dict[str, Any]) -> str:
    if tool_name != "graph_tool":
        return ""
    name = str(args.get("name") or "").strip()
    if not name:
        return ""
    try:
        return GraphRegistry(config_context=_config_context(agent)).get_metadata(name).source_hash
    except Exception:
        return ""


def _rules_from_config(context: ConfigurationContext) -> list[dict[str, Any]]:
    try:
        permissions = context.read_merged_config().get("permissions", {})
    except Exception:
        return []
    if not isinstance(permissions, dict):
        return []
    return [dict(item) for item in list(permissions.get("rules") or []) if isinstance(item, dict)]


def _session_permission_ref(runner: Any | None) -> dict[str, Any]:
    if runner is None:
        return {}
    # One-shot approvals are request coordination state, not a RunnerConfig
    # persistent Runner manifest field.  Keeping them on the live Runner
    # prevents permissions from reintroducing an implicit mode-state owner.
    permissions = getattr(runner, "_session_permissions", None)
    if not isinstance(permissions, dict):
        permissions = {}
        setattr(runner, "_session_permissions", permissions)
    permissions.setdefault("rules", [])
    return permissions


def _matches(rule: dict[str, Any], action: PermissionAction) -> bool:
    if str(rule.get("tool") or "") not in {"*", action.tool}:
        return False
    target = str(rule.get("target") or "")
    if target and target != action.target:
        return False
    source_hash = str(rule.get("source_hash") or "")
    if source_hash and source_hash != action.source_hash:
        return False
    return True


def _persist_workspace_rule(context: ConfigurationContext, rule: dict[str, str]) -> None:
    if context.workspace_dir is None:
        raise PermissionDenied("没有 workspace，无法保存 always allow 规则")

    def updater(payload: dict[str, Any]) -> dict[str, Any]:
        permissions = dict(payload.get("permissions") or {})
        rules = [dict(item) for item in list(permissions.get("rules") or []) if isinstance(item, dict)]
        if rule not in rules:
            rules.append(dict(rule))
        permissions["rules"] = rules
        payload["permissions"] = permissions
        return payload

    update_workspace_config(context.workspace_dir, updater)


class PermissionEngine:
    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.runner = _runner_for_agent(agent)
        self.context = _config_context(agent)

    @property
    def mode(self) -> PermissionMode:
        """权限维度：只回答"是否需要审批"，与 agent 执行模式无关。"""

        runner_mode = str(getattr(self.runner, "permission_mode", "") or "").strip()
        if runner_mode:
            return _normalize_mode(runner_mode)
        return "default"

    @property
    def agent_mode(self) -> AgentMode:
        """执行模式维度：plan 等模式的工具面约束由它决定。"""

        runner_mode = str(getattr(self.runner, "agent_mode", "") or "").strip()
        if runner_mode:
            return _normalize_agent_mode(runner_mode)
        return "agent"

    def action(self, tool: Any, args: dict[str, Any]) -> PermissionAction:
        name = str(getattr(tool, "name", "") or "").strip()
        return PermissionAction(
            tool=name,
            target=_target_for(name, args),
            source_hash=_source_hash(self.agent, name, args),
            is_read_only=bool(getattr(tool, "is_read_only", False)),
        )

    def _load_rules(self) -> list[dict[str, Any]]:
        session_rules = [
            dict(item)
            for item in list(_session_permission_ref(self.runner).get("rules") or [])
            if isinstance(item, dict)
        ]
        return session_rules + _rules_from_config(self.context)

    def _matches_rule(self, action: PermissionAction, behavior: str) -> bool:
        rules = self._load_rules()
        return any(
            str(rule.get("behavior") or "") == behavior and _matches(rule, action)
            for rule in rules
        )

    def _tool_check_permissions(self, tool: Any, args: dict[str, Any]) -> PermissionResult:
        """调用工具的 check_permissions 方法；无此方法时返回 passthrough。"""
        check_fn = getattr(tool, "check_permissions", None)
        if not callable(check_fn):
            return PermissionResult("passthrough")
        try:
            result = check_fn(
                dict(args),
                permission_mode=self.mode,
                agent_mode=self.agent_mode,
            )
            if isinstance(result, PermissionResult):
                return result
            return PermissionResult("passthrough")
        except Exception:
            return PermissionResult("passthrough")

    def _hard_denial_reason(self, action: PermissionAction) -> str:
        """Return a non-bypassable policy rejection, or an empty string.

        Workspace allow rules are convenience approvals, never authority to
        re-enable self-modification or a disabled automatic Graph surface.
        """

        if action.tool in SELF_EVOLUTION_TOOL_NAMES and not self_evolution_enabled(self.context):
            return "self_evolution.enabled=false，禁止修改 Agent、Tool、Skill 或 Graph"

        if action.tool in _SHARED_CONFIG_WRITE_TOOLS:
            runner_context = getattr(self.agent, "runner_context", None)
            runner = getattr(runner_context, "runner", None)
            agent_id = str(getattr(runner_context, "agent_id", "") or "").strip()
            managed = None
            manager = getattr(runner, "agent_manager", None)
            if manager is not None and agent_id:
                try:
                    managed = manager.get(agent_id)
                except (KeyError, RuntimeError):
                    managed = None

            # Tool constructor kwargs and Agent declarations are editable.
            # The Manager-held identity is the only authorization source. A
            # config can name a privileged role but cannot grant it to an
            # unbound instance; built-in roots and an explicit evolution
            # worker are allowed through the same role/capability rule.
            is_root = bool(getattr(managed, "is_root", False))
            role = str(getattr(managed, "role", "") or "").strip()
            is_evolution_worker = role == "evolution_worker"
            if not (is_root or is_evolution_worker):
                return (
                    f"{action.tool} 仅允许 managed root 或 evolution_worker 修改共享配置；"
                    f"当前 agent_id={agent_id or 'unbound'}, role={role or 'unbound'}"
                )

        automatic_graphs_enabled = graphs_enabled(self.context) and bool(
            getattr(self.agent, "graph_tools_enabled", True)
        )
        if action.tool not in GRAPH_TOOL_NAMES or automatic_graphs_enabled:
            return ""
        # Only a gateway-installed, current-stream `$graph:<name>` scope may
        # execute a Graph while automatic Graph tools are hidden.
        allowed_graph = str(getattr(self.agent, "_manual_graph_authorization", "") or "").strip()
        if action.tool == "graph_tool" and allowed_graph and action.target == allowed_graph:
            return ""
        return "graphs.enabled=false 或当前 Agent 已关闭 Graph 自动能力；仅 $graph:<name> 当前轮可执行指定 graph"

    def decide(
        self,
        action: PermissionAction,
        *,
        tool: Any = None,
        args: dict[str, Any] | None = None,
    ) -> PermissionBehavior:
        """统一决策管道，返回 allow/deny/ask。"""

        # Step 0: hard feature switches cannot be bypassed by stored rules.
        if self._hard_denial_reason(action):
            return "deny"

        # Step 1: 显式 deny rules（最高优先级）
        if self._matches_rule(action, "deny"):
            return "deny"

        # Step 2: 工具自决（plan mode 核心判定点）
        if tool is not None:
            tool_decision = self._tool_check_permissions(tool, args or {})
            if tool_decision.behavior == "deny":
                return "deny"
            if tool_decision.behavior == "allow":
                return "allow"

        # Step 3: 显式 allow rules
        if self._matches_rule(action, "allow"):
            return "allow"

        # Step 4: RunnerConfig policy is the only read-only authority; accept
        # approval never relaxes a declarative capability restriction.
        tool_policy = getattr(getattr(self.runner, "config", None), "tool_policy", None)
        if (
            bool(getattr(tool_policy, "read_only", False))
            and not action.is_read_only
            and action.tool not in frozenset(getattr(tool_policy, "read_only_exceptions", ()) or ())
        ):
            return "deny"

        # Step 5: 只读工具 / 非审批工具默认放行
        if action.is_read_only:
            return "allow"
        if action.tool not in _APPROVAL_TOOLS:
            return "allow"

        # Step 6: accept 权限模式自动放行审批工具
        if self.mode == "accept" and action.tool in _APPROVAL_TOOLS:
            return "allow"

        # Step 7: 显式 ask rules
        if self._matches_rule(action, "ask"):
            return "ask"

        return "ask"

    def require(self, tool: Any, args: dict[str, Any]) -> None:
        """统一权限入口。拒绝时抛出 PermissionDenied，需要审批时发起交互。"""

        action = self.action(tool, args)

        # Step 0: hard feature switches cannot be bypassed by stored rules.
        hard_denial = self._hard_denial_reason(action)
        if hard_denial:
            raise PermissionDenied(hard_denial)

        # Step 1: 显式 deny rules
        if self._matches_rule(action, "deny"):
            raise PermissionDenied(f"权限规则拒绝工具 {action.tool!r}")

        # Step 2: 工具自决
        tool_decision = self._tool_check_permissions(tool, args)
        if tool_decision.behavior == "deny":
            raise PermissionDenied(tool_decision.reason or f"工具 {action.tool!r} 拒绝执行")
        if tool_decision.behavior == "allow":
            return

        # Step 3: 显式 allow rules
        if self._matches_rule(action, "allow"):
            return

        # Step 4: immutable RunnerConfig policy is stricter than approval.
        tool_policy = getattr(getattr(self.runner, "config", None), "tool_policy", None)
        if (
            bool(getattr(tool_policy, "read_only", False))
            and not action.is_read_only
            and action.tool not in frozenset(getattr(tool_policy, "read_only_exceptions", ()) or ())
        ):
            raise PermissionDenied(f"RunnerConfig 不允许非只读工具 {action.tool!r}")

        # Step 5: 只读工具 / 非审批工具默认放行
        if action.is_read_only or action.tool not in _APPROVAL_TOOLS:
            return

        # Step 6: accept 权限模式自动放行审批工具
        if self.mode == "accept":
            return

        # Step 7: default 权限模式交互审批
        self._ask_user(action)

    def _ask_user(self, action: PermissionAction) -> None:
        """向用户发起审批交互。"""

        ask_user = getattr(self.runner, "ask_user", None)
        if not callable(ask_user):
            raise PermissionDenied(f"工具 {action.tool!r} 需要审批，但当前没有用户交互通道")
        request = {
            "request_id": f"permission-{uuid4().hex[:12]}",
            "question": f"是否允许执行工具 {action.tool}？\n目标：{action.target or 'unspecified'}",
            "options": [
                {"label": "允许一次", "value": "allow_once", "description": "仅当前 runner 允许该操作"},
                {"label": "始终允许", "value": "always_allow", "description": "保存到 workspace 权限规则"},
                {"label": "拒绝", "value": "deny", "description": "不执行该操作"},
            ],
            "multiple": False,
            "allow_custom": False,
        }
        response = normalize_ask_response(ask_user(request), request=request)
        selected = {str(item.get("value") or "") for item in response.get("selected", [])}
        if response.get("status") != "answered":
            raise PermissionDenied(f"工具 {action.tool!r} 未获得审批")
        rule = action.to_rule("allow")
        if "always_allow" in selected:
            _persist_workspace_rule(self.context, rule)
            return
        if "allow_once" in selected:
            session = _session_permission_ref(self.runner)
            session.setdefault("rules", []).append(rule)
            if self.runner is not None:
                self.runner.persist_runner_state()
            return
        raise PermissionDenied(f"用户拒绝工具 {action.tool!r}")


def check_agent_tool_permission(agent: Any, tool: Any, args: dict[str, Any]) -> None:
    PermissionEngine(agent).require(tool, dict(args or {}))


def get_permission_status(
    *,
    context: ConfigurationContext,
    runner: Any | None = None,
    permission_mode: str | None = None,
    agent_mode: str | None = None,
) -> dict[str, Any]:
    """汇总当前权限视图。

    审批模式与声明式 ``RunnerConfig`` 的工具策略分别上报；它们彼此独立。
    """

    try:
        permissions = context.read_merged_config().get("permissions", {})
    except Exception:
        permissions = {}
    permissions = dict(permissions) if isinstance(permissions, dict) else {}
    session = _session_permission_ref(runner)
    resolved_permission_mode = _normalize_mode(
        str(permission_mode or getattr(runner, "permission_mode", "") or "").strip() or "default"
    )
    resolved_agent_mode = _normalize_agent_mode(
        str(agent_mode or getattr(runner, "agent_mode", "") or "").strip() or "agent"
    )
    # Gateway requests can ask for a permission preview before a Runner has
    # been created.  The mode still denotes only a static RunnerConfig, so
    # resolve that declaration rather than recreating an execution-mode
    # branch here.  A live Runner remains the authoritative source once it
    # exists (for example, for a custom registered config).
    tool_policy = getattr(getattr(runner, "config", None), "tool_policy", None)
    if tool_policy is None:
        from juice_agents.core.runner.config import mode_registry

        try:
            tool_policy = mode_registry.resolve(resolved_agent_mode).tool_policy
        except ValueError:
            # Unknown modes are rejected during Runner creation.  Permission
            # status is an informational endpoint, so return a conservative
            # empty policy while the caller reports that validation error.
            tool_policy = None
    return {
        "permission_mode": resolved_permission_mode,
        "agent_mode": resolved_agent_mode,
        "read_only": bool(getattr(tool_policy, "read_only", False)),
        "workspace_rules": [dict(item) for item in list(permissions.get("rules") or []) if isinstance(item, dict)],
        "session_rules": [dict(item) for item in list(session.get("rules") or []) if isinstance(item, dict)],
    }


__all__ = [
    "AgentMode",
    "PermissionAction",
    "PermissionBehavior",
    "PermissionDenied",
    "PermissionEngine",
    "PermissionMode",
    "PermissionResult",
    "check_agent_tool_permission",
    "get_permission_status",
]
