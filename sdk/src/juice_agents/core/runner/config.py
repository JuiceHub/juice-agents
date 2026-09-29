"""Declarative Runner mode configuration.

This module is intentionally data-only.  A mode describes *which* static
bindings and runtime capabilities a conversation receives; it never injects
callbacks, coordinators, agents, or executors into :class:`Runner`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


class Capability(StrEnum):
    """Framework capabilities that a declarative mode may enable."""

    AGENTS = "agents"
    TOOLS = "tools"
    GRAPHS = "graphs"
    ASYNC_TASKS = "async_tasks"
    COLLABORATION = "collaboration"
    PLANNING = "planning"


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Tool surface exposed to an agent in this Runner configuration."""

    read_only: bool = False
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    # Read-only policies normally keep every Tool whose declaration marks it
    # read-only.  A small explicit exception list supports workflow tools
    # such as writing the plan artifact without smuggling a mode callback into
    # Runner or Agent code.
    read_only_exceptions: frozenset[str] = field(default_factory=frozenset)
    # Agent dispatch is a tool capability, but its target allow-list is still
    # declarative policy rather than a plan/team-specific tool branch.
    allowed_agent_names: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ContinuationPolicy:
    """Bounded continuation rules for one user request."""

    continue_on_pending_work: bool = True
    max_rounds: int = 32

    def __post_init__(self) -> None:
        if self.max_rounds <= 0:
            raise ValueError("ContinuationPolicy.max_rounds 必须为正整数")


@dataclass(frozen=True, slots=True)
class TeamRunConfig:
    """Durable Team dispatch policy; mutable work lives in TeamManager."""

    auto_dispatch: bool = True


@dataclass(frozen=True, slots=True)
class AgentBinding:
    """Static declaration reference for a managed Agent.

    ``agent_name`` is resolved by ``AgentRegistry`` when an ``AgentManager``
    acquires an instance.  It deliberately contains no live instance override.
    ``lifecycle`` uses the public names from the new architecture; persistent
    workers are owned by the manager, functional workers are released after a
    request.
    """

    agent_name: str
    role: str
    lifecycle: str = "functional"

    def __post_init__(self) -> None:
        name = str(self.agent_name or "").strip()
        role = str(self.role or "").strip()
        lifecycle = str(self.lifecycle or "functional").strip()
        if not name:
            raise ValueError("AgentBinding.agent_name 不能为空")
        if not role:
            raise ValueError("AgentBinding.role 不能为空")
        if lifecycle not in {"persistent", "functional"}:
            raise ValueError("AgentBinding.lifecycle 只支持 persistent 或 functional")
        object.__setattr__(self, "agent_name", name)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "lifecycle", lifecycle)

    def to_dict(self) -> dict[str, str]:
        return {
            "agent_name": self.agent_name,
            "role": self.role,
            "lifecycle": self.lifecycle,
        }

    # The manager protocol deliberately accepts small binding DTOs from
    # different composition roots.  These two read-only aliases make this
    # canonical Runner DTO usable without exposing a live AgentRef object.
    @property
    def name(self) -> str:
        return self.agent_name

    @property
    def agent_ref(self) -> str:
        return self.agent_name

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentBinding":
        return cls(
            agent_name=str(value.get("agent_name") or value.get("name") or ""),
            role=str(value.get("role") or ""),
            lifecycle=str(value.get("lifecycle") or "functional"),
        )


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    """The complete, serializable execution contract for a Runner mode."""

    mode_id: str
    root_binding: AgentBinding
    member_binding: AgentBinding
    capability_flags: frozenset[Capability] = field(default_factory=lambda: frozenset(Capability))
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    concurrency_limit: int = 1
    continuation_policy: ContinuationPolicy = field(default_factory=ContinuationPolicy)
    team: TeamRunConfig | None = None

    def __post_init__(self) -> None:
        mode_id = str(self.mode_id or "").strip()
        if not mode_id:
            raise ValueError("RunnerConfig.mode_id 不能为空")
        if self.concurrency_limit <= 0:
            raise ValueError("RunnerConfig.concurrency_limit 必须为正整数")
        if mode_id == "team" and self.team is None:
            raise ValueError("Team RunnerConfig 缺少 team 运行配置；旧 Team 状态不支持迁移")
        if self.team is not None and not isinstance(self.team, TeamRunConfig):
            raise TypeError("RunnerConfig.team 必须是 TeamRunConfig")
        object.__setattr__(self, "mode_id", mode_id)
        object.__setattr__(self, "capability_flags", frozenset(Capability(item) for item in self.capability_flags))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode_id": self.mode_id,
            "root_binding": self.root_binding.to_dict(),
            "member_binding": self.member_binding.to_dict(),
            "capability_flags": sorted(capability.value for capability in self.capability_flags),
            "tool_policy": {
                "read_only": self.tool_policy.read_only,
                "allowed_tools": sorted(self.tool_policy.allowed_tools),
                "read_only_exceptions": sorted(self.tool_policy.read_only_exceptions),
                "allowed_agent_names": sorted(self.tool_policy.allowed_agent_names),
            },
            "concurrency_limit": self.concurrency_limit,
            "continuation_policy": {
                "continue_on_pending_work": self.continuation_policy.continue_on_pending_work,
                "max_rounds": self.continuation_policy.max_rounds,
            },
            "team": None if self.team is None else {"auto_dispatch": self.team.auto_dispatch},
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunnerConfig":
        tools = dict(value.get("tool_policy") or {})
        continuation = dict(value.get("continuation_policy") or {})
        return cls(
            mode_id=str(value.get("mode_id") or ""),
            root_binding=AgentBinding.from_dict(dict(value.get("root_binding") or {})),
            member_binding=AgentBinding.from_dict(dict(value.get("member_binding") or {})),
            capability_flags=frozenset(Capability(item) for item in value.get("capability_flags") or []),
            tool_policy=ToolPolicy(
                read_only=bool(tools.get("read_only", False)),
                allowed_tools=frozenset(str(item) for item in tools.get("allowed_tools") or []),
                read_only_exceptions=frozenset(
                    str(item) for item in tools.get("read_only_exceptions") or []
                ),
                allowed_agent_names=frozenset(
                    str(item) for item in tools.get("allowed_agent_names") or []
                ),
            ),
            concurrency_limit=int(value.get("concurrency_limit") or 1),
            continuation_policy=ContinuationPolicy(
                continue_on_pending_work=bool(continuation.get("continue_on_pending_work", True)),
                max_rounds=int(continuation.get("max_rounds") or 32),
            ),
            team=(TeamRunConfig(auto_dispatch=bool(dict(value["team"]).get("auto_dispatch", True)))
                  if isinstance(value.get("team"), Mapping) else None),
        )


class ModeRegistry:
    """Process-local registry of immutable ``RunnerConfig`` templates only."""

    def __init__(self) -> None:
        self._configs: dict[str, RunnerConfig] = {}
        self._builtin_ids: set[str] = set()

    def register(self, config: RunnerConfig, *, replace: bool = False, builtin: bool = False) -> RunnerConfig:
        if not isinstance(config, RunnerConfig):
            raise TypeError("ModeRegistry 只接受 RunnerConfig")
        existing = self._configs.get(config.mode_id)
        if existing is not None and not replace:
            raise ValueError(f"RunnerConfig 已注册: {config.mode_id}")
        if existing is not None and config.mode_id in self._builtin_ids and not builtin:
            raise ValueError(f"不能覆盖内置 mode: {config.mode_id}")
        self._configs[config.mode_id] = config
        if builtin:
            self._builtin_ids.add(config.mode_id)
        return config

    def resolve(self, mode_id: str | RunnerConfig | None) -> RunnerConfig:
        if isinstance(mode_id, RunnerConfig):
            return mode_id
        normalized = str(mode_id or "agent").strip() or "agent"
        try:
            return self._configs[normalized]
        except KeyError as exc:
            available = ", ".join(sorted(self._configs)) or "<none>"
            raise ValueError(f"未知 RunnerConfig mode: {normalized}; 已注册: {available}") from exc

    def configs(self) -> Mapping[str, RunnerConfig]:
        return MappingProxyType(dict(self._configs))


mode_registry = ModeRegistry()


def _install_builtin_configs() -> None:
    """Install data templates.  They all execute through the same Runner path."""

    common = frozenset({
        Capability.AGENTS,
        Capability.TOOLS,
        Capability.GRAPHS,
        Capability.ASYNC_TASKS,
    })
    mode_registry.register(
        RunnerConfig(
            mode_id="agent",
            root_binding=AgentBinding("root", "root", "persistent"),
            member_binding=AgentBinding("general", "subagent"),
            capability_flags=common,
        ),
        builtin=True,
    )
    mode_registry.register(
        RunnerConfig(
            mode_id="plan",
            # Planning belongs to the same trusted runtime root.  Its
            # effective in-memory AgentConfig is selected by the mode and is
            # never a Registry declaration named ``plan``.
            root_binding=AgentBinding("root", "root", "persistent"),
            member_binding=AgentBinding("general", "researcher"),
            capability_flags=common | frozenset({Capability.PLANNING}),
            tool_policy=ToolPolicy(
                read_only=True,
                read_only_exceptions=frozenset({"plan", "exit_plan", "agent_tool", "async_task_stop"}),
                allowed_agent_names=frozenset({"general", "explore"}),
            ),
        ),
        builtin=True,
    )
    mode_registry.register(
        RunnerConfig(
            mode_id="group",
            root_binding=AgentBinding("root", "root", "persistent"),
            # Bindings refer to Registry declarations, never abstract roles.
            member_binding=AgentBinding("general_worker", "worker", "persistent"),
            capability_flags=common | frozenset({Capability.COLLABORATION}),
            concurrency_limit=4,
        ),
        builtin=True,
    )
    mode_registry.register(
        RunnerConfig(
            mode_id="team",
            root_binding=AgentBinding("root", "root", "persistent"),
            member_binding=AgentBinding("researcher", "teammate", "persistent"),
            capability_flags=common | frozenset({Capability.COLLABORATION}),
            concurrency_limit=4,
            team=TeamRunConfig(),
        ),
        builtin=True,
    )


_install_builtin_configs()


def register_mode(config: RunnerConfig, *, replace: bool = False) -> RunnerConfig:
    """Register a custom declarative mode; code-level flow injection is impossible."""

    return mode_registry.register(config, replace=replace)


__all__ = [
    "AgentBinding",
    "Capability",
    "ContinuationPolicy",
    "ModeRegistry",
    "RunnerConfig",
    "ToolPolicy",
    "TeamRunConfig",
    "mode_registry",
    "register_mode",
]
