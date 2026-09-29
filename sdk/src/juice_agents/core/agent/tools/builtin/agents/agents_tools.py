"""Agent 声明的作用域发现与只读查看工具。

list/view 服务由 AgentManager 持有的 root/member 身份决定，而不是由 Team、
Group 或 mode callback 决定。它们受 Agent 能力面约束，不受
``self_evolution.enabled`` 约束。

写入侧是 `builtin/evolution/agent_manage.py`，它继承这里的
`AgentConfigToolBase`，因此读写共用同一套 Runner 身份解析、作用域投影与
授权判定 —— 授权只能来自 Runner 绑定的身份，构造参数与可编辑 YAML 都不是权威。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Literal

from juice_agents.core.agent.builtin.configs import build_builtin_agent_config
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.agents.registry import AgentRegistry
from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.registry.common import normalize_name

from ...runtime.base_tools import CONTENT_OBSERVATION_CHARS, LIST_OBSERVATION_CHARS, Tool
from ...runtime.owner_context import resolve_owner_context

logger = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """Runner-owned identity used by every routing and authorization decision."""

    mode_id: str
    role: str
    agent_name: str
    config_name: str
    is_root: bool
    has_runner: bool


@dataclass(frozen=True, slots=True)
class AgentTarget:
    name: str
    mode_id: str
    role: str
    target_scope: Literal["self", "authorized"]
    directory: Path

    @property
    def path(self) -> Path:
        return self.directory / f"{self.name}.yaml"


class AgentConfigToolBase(Tool):
    """Resolve an Agent target from trusted runtime identity and explicit scope."""

    # Team root may inspect reusable shared Agents before adding members.
    # Keep this capability on read-only tools so agent_manage cannot edit a
    # declaration merely because it is a possible future Team member.
    _browse_team_shared_candidates = False

    def __init__(
        self,
        *,
        owner_agent: Any | None = None,
        workspace_dir: str | Path | None = None,
        agent_config_dir: str | Path | None = None,
        caller_mode_id: str | None = None,
        caller_role: str | None = None,
    ) -> None:
        super().__init__()
        self.owner_agent = owner_agent
        self.workspace_dir = None if workspace_dir is None else Path(workspace_dir).resolve()
        self.agent_config_dir = None if agent_config_dir is None else Path(agent_config_dir).resolve()
        # These constructor hints are intentionally accepted only by detached
        # tools. Once an owner is attached to a Runner, its manager metadata
        # is the sole authority and these values cannot elevate privileges.
        self.caller_mode_id = None if caller_mode_id is None else str(caller_mode_id).strip()
        self.caller_role = None if caller_role is None else str(caller_role).strip()

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _runner(self) -> Any | None:
        return getattr(getattr(self.owner_agent, "runner_context", None), "runner", None)

    def _context(self) -> ConfigurationContext:
        return resolve_owner_context(self.owner_agent, explicit_workspace_dir=self.workspace_dir)

    def _identity(self) -> AgentIdentity:
        runner = self._runner()
        config_name = str(getattr(self.owner_agent, "name", "") or "").strip()
        if runner is None:
            mode_id = self.caller_mode_id or "agent"
            role = self.caller_role or "root"
            is_root = role == "root"
            return AgentIdentity(
                mode_id=mode_id,
                role=role,
                agent_name=config_name,
                config_name=config_name,
                is_root=is_root,
                has_runner=False,
            )

        # Resolve identity by the AgentManager-owned live records.  A stale
        # RunnerContext or editable declaration cannot impersonate a peer.
        managed = next(
            (
                item
                for item in runner.agent_manager.live_agents.values()
                if item.instance is self.owner_agent
            ),
            None,
        )
        if managed is None:
            raise PermissionError("Agent evolution 工具 owner 未由当前 AgentManager 持有")
        runtime_role = str(managed.role or "").strip()
        is_root = bool(managed.is_root)
        role = "root" if is_root else (runtime_role or "member")
        return AgentIdentity(
            # Retained only as descriptive metadata for clients. It no longer
            # selects a directory, role, or execution branch.
            mode_id=str(getattr(runner, "mode_id", "") or "").strip(),
            role=role,
            agent_name=managed.agent_name,
            config_name=config_name,
            is_root=is_root,
            has_runner=True,
        )

    @staticmethod
    def _role_dirs(_mode_id: str = "") -> tuple[str, str]:
        """Return generic declaration labels; no mode owns a namespace."""

        return "root", "members"

    def _scope_dir(self, identity: AgentIdentity, scope: Literal["self", "authorized"]) -> Path:
        # Detached factories historically pass one already-scoped directory.
        # It remains useful for direct SDK composition, but never overrides
        # the trusted global Registry projection of a live Runner Agent.
        if not identity.has_runner and self.agent_config_dir is not None:
            return self.agent_config_dir
        del scope
        return self._context().agents_dir

    def _can_manage_authorized(self, identity: AgentIdentity) -> bool:
        return identity.is_root or identity.role == "evolution_worker"

    def _authorized_names(self, identity: AgentIdentity) -> set[str] | None:
        """Return static direct relationships from the owner's declaration."""

        if not identity.has_runner:
            return None
        if identity.is_root:
            # Root has no Registry declaration of its own.  Its effective
            # call set is Runner-owned (Plan policy / selected Team / mode
            # filtering) and must be shared with `/agents` and dispatch.
            runner = self._runner()
            available = getattr(runner, "list_available_agents", None)
            if callable(available):
                names = {
                    str(item.get("name") or "").strip()
                    for item in available().get("agents", [])
                    if str(item.get("name") or "").strip()
                }
                if self._browse_team_shared_candidates and identity.mode_id == "team":
                    from juice_agents.core.registry.teams import TeamRegistry

                    candidates = TeamRegistry(config_context=self._context()).list_shared_member_candidates()
                    names.update(item["name"] for item in candidates)
                return names
        # Child declarations remain relationship truth immediately after a
        # save. Their live snapshot may intentionally lag until fresh acquire.
        self_dir = self._scope_dir(identity, "self")
        try:
            declared = AgentRegistry(
                config_context=self._context(),
                config_dir=self_dir,
            ).load_config(identity.config_name)
        except FileNotFoundError:
            declared = getattr(self.owner_agent, "_declared_agent_config", None)
            if not isinstance(declared, AgentConfig):
                return set()
        return set(declared.managed_agent_names)

    def _resolve_target(
        self,
        name: str,
        target_scope: str = "auto",
        *,
        allow_new_relationship: bool = False,
    ) -> AgentTarget:
        normalized_name = normalize_name(name)
        normalized_scope = str(target_scope or "auto").strip().lower()
        if normalized_scope not in {"auto", "self", "authorized"}:
            raise ValueError("target_scope 必须为 auto/self/authorized")
        identity = self._identity()
        scope: Literal["self", "authorized"]
        if normalized_scope == "auto":
            # Same-name root/child declarations are deliberately resolved to
            # self. Callers must say authorized to select the child/peer.
            scope = "self" if normalized_name == identity.config_name else "authorized"
        else:
            scope = normalized_scope  # type: ignore[assignment]
        if scope == "self" and normalized_name != identity.config_name:
            raise PermissionError(f"self scope 只能访问自身 Agent 配置: {identity.config_name}")
        if scope == "authorized" and not self._can_manage_authorized(identity):
            raise PermissionError(f"{identity.role} 只能编辑自身 Agent 配置: {identity.config_name}")
        if scope == "authorized" and identity.role == "evolution_worker" and normalized_name == identity.config_name:
            raise ValueError("evolution_worker 访问自身请使用 target_scope=self/auto")
        authorized_names = self._authorized_names(identity) if scope == "authorized" else None
        if (
            scope == "authorized"
            and authorized_names is not None
            and normalized_name not in authorized_names
            and not allow_new_relationship
        ):
            raise PermissionError(
                f"Agent 不属于当前 managed Agent 的直属管理作用域: {normalized_name}"
            )
        role = identity.role if scope == "self" else "member"
        return AgentTarget(
            name=normalized_name,
            mode_id=identity.mode_id,
            role=role,
            target_scope=scope,
            directory=self._scope_dir(identity, scope),
        )

    def _builtin_config(self, target: AgentTarget) -> dict[str, Any] | None:
        template_by_name = {"explore": "explore", "plan": "plan"}
        if target.name == "general":
            template = "general" if target.target_scope == "self" and target.role == "root" else "general_subagent"
        else:
            template = template_by_name.get(target.name)
        if template is None:
            return None
        return build_builtin_agent_config(template).to_dict()

    @staticmethod
    def _target_metadata(target: AgentTarget) -> dict[str, Any]:
        return {
            "agent_mode": target.mode_id,
            "mode_id": target.mode_id,
            "role": target.role,
            "target_scope": target.target_scope,
        }

    def _view_target(self, target: AgentTarget) -> dict[str, Any]:
        path = target.path
        if path.is_file():
            config = AgentRegistry(config_context=self._context(), config_dir=target.directory).load_config(target.name)
            return {
                "name": config.name,
                "source": "workspace",
                "path": str(path),
                **self._target_metadata(target),
                "config": config.to_dict(),
            }
        builtin = self._builtin_config(target)
        if builtin is None:
            raise FileNotFoundError(f"Agent 配置不存在: {target.name}")
        return {
            "name": str(builtin["name"]),
            "source": "builtin",
            "path": None,
            **self._target_metadata(target),
            "config": builtin,
        }

    def _visible_targets(self) -> list[AgentTarget]:
        identity = self._identity()
        targets: list[AgentTarget] = []
        self_target = self._resolve_target(identity.config_name, "self")
        if self_target.path.is_file():
            targets.append(self_target)
        if self._can_manage_authorized(identity):
            authorized_dir = self._scope_dir(identity, "authorized")
            authorized_names = self._authorized_names(identity)
            names = (
                sorted(path.stem for path in authorized_dir.glob("*.yaml"))
                if authorized_names is None
                else sorted(authorized_names)
            )
            for name in names:
                target = self._resolve_target(name, "authorized")
                if target.path.is_file():
                    targets.append(target)
        return targets


class AgentsListTool(AgentConfigToolBase):
    _browse_team_shared_candidates = True
    _execution_mode = "parallel_safe"
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "agents_list"
    is_read_only = True
    description = "列出当前 Agent 有权查看的静态 Agent YAML 配置。"
    inputs: dict[str, dict[str, Any]] = {}
    outputs = {"agents": {"type": "list", "description": "带作用域身份的 Agent 配置摘要"}}

    def forward(self) -> dict[str, Any]:
        agents = []
        for target in self._visible_targets():
            viewed = self._view_target(target)
            agents.append({
                "name": viewed["name"],
                "source": "workspace",
                "path": str(target.path),
                **self._target_metadata(target),
            })
        return {"agents": agents}


class AgentViewTool(AgentConfigToolBase):
    _browse_team_shared_candidates = True
    _execution_mode = "parallel_safe"
    max_observation_chars = CONTENT_OBSERVATION_CHARS
    name = "agent_view"
    is_read_only = True
    description = "按 self/authorized 作用域查看 Agent 配置。"
    inputs = {
        "name": {"type": "string", "description": "Agent 名称"},
        "target_scope": {"type": "string", "description": "auto/self/authorized", "required": False},
    }
    outputs = {"agent": {"type": "object", "description": "来源、目标身份、路径和完整配置"}}

    def forward(self, name: str, target_scope: str = "auto") -> dict[str, Any]:
        return {"agent": self._view_target(self._resolve_target(name, target_scope))}


__all__ = ["AgentIdentity", "AgentConfigToolBase", "AgentTarget", "AgentViewTool", "AgentsListTool"]
