"""
Agent registry：统一管理 Agent 配置与运行时 Agent 实例创建。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import logging
from pathlib import Path
from collections.abc import Iterable
from typing import Any, Callable, Mapping

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.config.model_catalog import (
    DEFAULT_RUNTIME_CONFIG_PATH,
    DEFAULT_RUNTIME_MODEL_NAME,
    normalize_runtime_model_effort,
    resolve_runtime_model,
)
from juice_agents.core.registry.agents.store import AgentConfigStore
from juice_agents.core.registry.agents.defaults import build_builtin_agent_configs
from juice_agents.core.registry.common.store import as_config_path, normalize_name
from juice_agents.core.registry.agents.availability import (
    ROOT_AGENT_NAME,
    filter_available_agent_configs,
)
from juice_agents.core.registry.agents.types import (
    DEFAULT_MODEL_CONFIG_NAME,
    DEFAULT_MODEL_EFFORT,
    AgentConfig,
    AgentRef,
    normalize_agent_type,
)
from juice_agents.core.registry.tools.types import ToolRef

logger = logging.getLogger(__name__)


def _attach_declared_agent_metadata(
    agent: Any,
    config: AgentConfig,
    *,
    config_context: ConfigurationContext | None = None,
    agent_config_dir: str | Path | None = None,
    resolved_agent_type: str | None = None,
) -> Any:
    """
    把声明态配置快照挂到运行时实例上，供严格导出能力复用。
    """
    declared_config = AgentConfig.from_dict(config.to_dict())
    setattr(agent, "_declared_agent_config", declared_config)
    setattr(agent, "_declared_tool_refs", tuple(ToolRef.from_raw(tool) for tool in declared_config.tools))
    setattr(agent, "_declared_config_context", config_context)
    # This path is declaration metadata, not a Runner/session reference.  It
    # lets Agent-side refresh code persist a later user edit without asking a
    # Registry to inspect a live Runner for routing information.
    setattr(
        agent,
        "_declared_agent_config_dir",
        None if agent_config_dir is None else str(Path(agent_config_dir)),
    )
    setattr(
        agent,
        "_resolved_agent_type",
        normalize_agent_type(
            resolved_agent_type
            or ("codeact" if "codeact" in type(agent).__name__.lower() else "react")
        ),
    )
    setattr(agent, "_agent_lifecycle", declared_config.lifecycle)
    setattr(agent, "_agent_isolation", declared_config.isolation)
    setattr(
        agent,
        "_runtime_managed_agent_refs",
        {
            name: {"agent_config_dir": None if config_context is None else str(config_context.agents_dir)}
            for name in declared_config.managed_agent_names
        },
    )
    return agent


def _is_default_model_value(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"", DEFAULT_MODEL_CONFIG_NAME, "runtime.shared_model"}


class AgentRegistry:
    """Agent YAML 的直接 CRUD 与 fresh runtime 创建入口。"""

    def __init__(
        self,
        config_context: ConfigurationContext | None = None,
        config_dir: str | Path | None = None,
        *,
        tool_config_dir: str | Path | None = None,
    ) -> None:
        self.config_context = config_context or ConfigurationContext.from_workspace(None)
        if config_dir is not None:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = self.config_context.agents_dir
        if tool_config_dir is not None:
            self.tool_config_dir = Path(tool_config_dir)
        else:
            self.tool_config_dir = self.config_context.tools_dir
        self._store = AgentConfigStore(self.config_dir)
        # Private Team/explicit configuration directories are closed scopes:
        # a missing private member must never resolve to a global built-in.
        self._builtin_configs = (
            {config.name: config for config in build_builtin_agent_configs()}
            if self.config_dir.resolve() == self.config_context.agents_dir.resolve()
            else {}
        )
        from juice_agents.core.registry.tools.registry import ToolRegistry

        self._tool_registry = ToolRegistry(
            config_context=self.config_context,
            config_dir=self.tool_config_dir,
        )

    @classmethod
    def default(
        cls,
        config_dir: str | Path | None = None,
        *,
        tool_config_dir: str | Path | None = None,
    ) -> "AgentRegistry":
        return cls(config_dir=config_dir, tool_config_dir=tool_config_dir)

    def load_config(self, name: str) -> AgentConfig:
        """Resolve local YAML first, then a detached built-in declaration.

        Only absence permits fallback; invalid or unreadable local YAML must
        remain visible as an error instead of silently discarding user edits.
        """

        normalized = normalize_name(name)
        if as_config_path(normalized, self.config_dir).exists():
            return AgentConfig.from_dict(self._store.load(normalized))
        if normalized in self._builtin_configs:
            return AgentConfig.from_dict(self._builtin_configs[normalized])
        raise FileNotFoundError(f"Agent 配置不存在: {normalized}")

    def get_source(self, name: str) -> str | None:
        """Return the effective declaration source without creating files."""

        normalized = normalize_name(name)
        if as_config_path(normalized, self.config_dir).exists():
            return "local"
        return "builtin" if normalized in self._builtin_configs else None

    def config_path(self, name: str) -> Path | None:
        """A built-in declaration has no local path until its first edit."""

        return as_config_path(name, self.config_dir) if self.get_source(name) == "local" else None

    def save_config(
        self,
        config: dict[str, Any] | AgentConfig,
        *,
        name: str | None = None,
    ) -> AgentConfig:
        """Validate and write one workspace Agent YAML directly."""
        candidate = self.validate(AgentConfig.from_dict(config, config_name=name))
        self._ensure_team_references_remain_valid(candidate)
        saved = self._store.save(candidate, name=candidate.name)
        logger.info("已保存 Agent 本地声明: name=%s", saved.name)
        return saved

    def list_configs(self) -> list[str]:
        return sorted(set(self._store.list()) | self._builtin_configs.keys())

    @staticmethod
    def _runner_constraints(
        runner: Any | None,
        *,
        mode_id: str,
    ) -> tuple[frozenset[str] | None, frozenset[str]]:
        """Read only the narrow Runner state relevant to availability.

        The Registry never retains the Runner; the optional value is used only
        while answering an active-session query.  A query for another mode
        must not inherit disable state from the current mode, so it receives
        declaration-only filtering instead.
        """

        if runner is None:
            return None, frozenset()
        runner_mode = str(
            getattr(runner, "mode_id", "")
            or getattr(runner, "agent_mode", "")
            or ""
        ).strip()
        if runner_mode and runner_mode != mode_id:
            return None, frozenset()

        # Runner owns mode-dependent selection (Plan policy and selected Team
        # manifest).  Read only its small pure projection; this must never
        # acquire a root or create a cold Runner just to answer `/agents`.
        scoped_names = getattr(runner, "available_agent_names", None)
        if callable(scoped_names):
            selected = scoped_names()
            allowed = None if selected is None else frozenset(selected)
        else:
            policy = getattr(getattr(runner, "config", None), "tool_policy", None)
            configured = frozenset(
                str(item).strip()
                for item in getattr(policy, "allowed_agent_names", ()) or ()
                if str(item).strip()
            )
            # An empty ToolPolicy allow-list means unrestricted discovery. It
            # is not the same as a child Agent's empty relationship list.
            allowed = configured or None

        disabled: set[str] = set()

        def _add_names(raw: Any) -> None:
            if isinstance(raw, str):
                disabled.add(raw.strip())
                return
            if isinstance(raw, Iterable):
                disabled.update(str(item).strip() for item in raw if str(item).strip())

        state = getattr(runner, "state", None)
        if isinstance(state, Mapping):
            for key in ("disabled_agent_names", "disabled_agents"):
                _add_names(state.get(key))
            for key in ("disabled_agent_names_by_mode", "disabled_agents_by_mode"):
                by_mode = state.get(key)
                if isinstance(by_mode, Mapping):
                    _add_names(by_mode.get(mode_id))

        # Keep compatibility with the already-existing runtime projection on
        # MultiStepAgent.  It is purely ephemeral and is safe to read while
        # producing an active-session response.
        manager = getattr(runner, "agent_manager", None)
        root_name = str(getattr(runner, "root_agent_name", ROOT_AGENT_NAME) or ROOT_AGENT_NAME)
        get_by_name = getattr(manager, "get_by_name", None)
        if callable(get_by_name):
            try:
                managed = get_by_name(root_name)
                _add_names(getattr(getattr(managed, "instance", None), "_runtime_disabled_managed_agent_names", ()))
            except (KeyError, RuntimeError):
                # A cold/idle runner may not have acquired root yet. That is
                # expected and must not make a listing allocate it.
                pass
        return allowed, frozenset(disabled)

    def list_available_agents(
        self,
        *,
        name: str | None = None,
        mode_id: str,
        runner: Any | None = None,
    ) -> dict[str, Any]:
        """List Registry declarations available to a mode without writes.

        It shares effective declarations with construction and dispatch and
        excludes root from child discovery. Cold queries never create runtime
        objects, directories, or declaration files.
        """

        normalized_mode = str(mode_id or "").strip()
        if not normalized_mode:
            raise ValueError("mode_id 不能为空")
        requested_name = None if name is None else str(name).strip()
        if requested_name == "":
            raise ValueError("agent name 不能为空")

        names = [requested_name] if requested_name is not None else self.list_configs()
        declarations: list[AgentConfig] = []
        for agent_name in names:
            if agent_name == ROOT_AGENT_NAME:
                continue
            try:
                declarations.append(self.load_config(agent_name))
            except FileNotFoundError:
                if requested_name is None:
                    raise
                # A named query for an unknown Agent is an empty result.

        allowed, disabled = self._runner_constraints(runner, mode_id=normalized_mode)
        available = filter_available_agent_configs(
            declarations,
            mode_id=normalized_mode,
            allowed_agent_names=allowed,
            disabled_agent_names=disabled,
        )
        return {
            "mode_id": normalized_mode,
            "agents": [
                {
                    "name": config.name,
                    "description": config.description,
                    "allowed_modes": (
                        None if config.allowed_modes is None else list(config.allowed_modes)
                    ),
                    "source": self.get_source(config.name),
                    "config": config.to_dict(),
                }
                for config in available
            ],
        }

    def delete_config(self, name: str) -> None:
        """Delete a local declaration, validating its effective replacement.

        Built-ins remain immutable: deleting an override restores defaults,
        while deleting a pure built-in is rejected without a tombstone.
        """

        normalized = normalize_name(name)
        source = self.get_source(normalized)
        if source == "builtin":
            raise ValueError(f"内置 Agent 只读，不能删除: {normalized}")
        if source is None:
            raise FileNotFoundError(f"Agent 配置不存在: {normalized}")
        references = self._referencing_teams(normalized)
        if references:
            fallback = self._builtin_configs.get(normalized)
            if fallback is None:
                raise ValueError(
                    f"Agent {normalized!r} 被 Team 引用，不能删除: {', '.join(references)}"
                )
            self._ensure_team_references_remain_valid(self.validate(fallback))
        self._store.delete(normalized)
        logger.info(
            "已删除 Agent 本地声明: name=%s fallback=%s", normalized,
            "builtin" if normalized in self._builtin_configs else "none",
        )

    def _referencing_teams(self, name: str) -> tuple[str, ...]:
        # Private declarations cannot invalidate a same-name global reference.
        if self.config_dir.resolve() != self.config_context.agents_dir.resolve():
            return ()
        from juice_agents.core.registry.teams.registry import TeamRegistry

        return TeamRegistry(config_context=self.config_context).referencing_teams(name)

    def _ensure_team_references_remain_valid(self, candidate: AgentConfig) -> None:
        """Keep referenced shared Agents eligible and persistent after edits."""

        if (
            (candidate.allowed_modes is None or "team" in candidate.allowed_modes)
            and candidate.lifecycle == "persistent"
        ):
            return
        references = self._referencing_teams(candidate.name)
        if references:
            raise ValueError(
                f"Agent {candidate.name!r} 被 Team 引用，必须保留 team mode 和 persistent lifecycle: "
                f"{', '.join(references)}"
            )

    def bind_tool_ref(self, agent_name: str, tool_name: str) -> AgentConfig:
        """持久化 Agent -> Tool 名称引用；重复绑定保持幂等。"""
        config = self.load_config(agent_name)
        refs = list(config.tools)
        normalized = str(tool_name or "").strip()
        if not any(ref.name == normalized for ref in refs):
            refs.append(ToolRef(name=normalized))
        return self.save_config(
            config.copy_with(tools=[ref.to_dict() for ref in refs]),
            name=config.name,
        )

    def unload_tool_ref(self, agent_name: str, tool_name: str) -> AgentConfig:
        """仅解除 Agent -> Tool 引用，不删除 Tool YAML。"""
        config = self.load_config(agent_name)
        normalized = str(tool_name or "").strip()
        return self.save_config(
            config.copy_with(
                tools=[ref.to_dict() for ref in config.tools if ref.name != normalized]
            ),
            name=config.name,
        )

    def bind_skill_ref(self, agent_name: str, skill_name: str) -> AgentConfig:
        """启用 Agent 的 Skill 引用，并从 per-agent deny-list 移除。"""
        config = self.load_config(agent_name)
        normalized = str(skill_name or "").strip().lower().split("/")[-1]
        allow = list(config.skill_names)
        if allow and normalized not in allow:
            allow.append(normalized)
        deny = [item for item in config.disabled_skill_names if item.lower() != normalized]
        return self.save_config(
            config.copy_with(
                skill_names=allow,
                disabled_skill_names=deny,
                enable_skill_tools=True,
            ),
            name=config.name,
        )

    def unload_skill_ref(self, agent_name: str, skill_name: str) -> AgentConfig:
        """写入 per-agent Skill deny-list，不删除 Skill 目录。"""
        config = self.load_config(agent_name)
        normalized = str(skill_name or "").strip().lower().split("/")[-1]
        deny = list(config.disabled_skill_names)
        if normalized not in {item.lower() for item in deny}:
            deny.append(normalized)
        return self.save_config(
            config.copy_with(disabled_skill_names=deny),
            name=config.name,
        )

    def bind_managed_agent_ref(self, owner_name: str, target_name: str) -> AgentConfig:
        """持久化 owner -> managed agent 引用；重复绑定保持幂等。"""
        config = self.load_config(owner_name)
        names = list(config.managed_agent_names)
        normalized = str(target_name or "").strip()
        if normalized not in names:
            names.append(normalized)
        return self.save_config(
            config.copy_with(managed_agent_names=names),
            name=config.name,
        )

    def unload_managed_agent_ref(self, owner_name: str, target_name: str) -> AgentConfig:
        """仅解除 owner -> managed agent 引用，不删除目标 Agent YAML。"""
        config = self.load_config(owner_name)
        normalized = str(target_name or "").strip()
        return self.save_config(
            config.copy_with(
                managed_agent_names=[
                    item for item in config.managed_agent_names if item != normalized
                ]
            ),
            name=config.name,
        )

    def resolve(self, raw: AgentRef | AgentConfig | Mapping[str, Any] | str) -> AgentConfig:
        """Resolve a declaration without accepting or inspecting live Agents.

        Keeping this boundary free of ``MultiStepAgent`` imports is deliberate:
        accepting an object here lets a Registry become a second owner of a
        running Agent and makes restore/release order impossible to reason
        about.  ``AgentManager`` owns the resulting fresh instance instead.
        """

        if isinstance(raw, AgentRef):
            if raw.declaration_override is not None:
                return AgentConfig.from_dict(raw.declaration_override)
            return self.load_config(raw.name)
        if isinstance(raw, AgentConfig):
            # The effective config must never be the caller's declaration
            # object, even though AgentConfig is frozen. Nested payloads such
            # as output_schema intentionally receive a new deep copy here.
            return AgentConfig.from_dict(raw)
        if isinstance(raw, Mapping):
            return AgentConfig.from_dict(dict(raw))
        if isinstance(raw, str):
            normalized_name = str(raw or "").strip()
            if not normalized_name:
                raise ValueError("agent name 不能为空")
            return self.load_config(normalized_name)
        raise TypeError("agent 只支持通过 AgentRef / name / config object 解析")

    def validate(self, raw: AgentRef | AgentConfig | Mapping[str, Any] | str) -> AgentConfig:
        """Return a canonical declaration after validating static references.

        Validation deliberately stops at declaration boundaries.  It validates
        Tool names and dynamic Tool schemas, but does not construct sessions,
        bind permissions, or inspect a live Agent.
        """

        config = self.resolve(raw)
        canonical = AgentConfig.from_dict(config.to_dict())
        for tool_ref in canonical.tools:
            self._tool_registry.validate(tool_ref)
        return canonical

    def instantiate(
        self,
        raw: AgentRef | AgentConfig | Mapping[str, Any] | str,
        *,
        model: Any | None = None,
        runtime_config_path: str | Path | None = None,
        model_name: str | None = None,
        model_effort: str | None = None,
        juice_root: str | Path | None = None,
        tool_factories: dict[str, Callable] | None = None,
        **runtime_kwargs: Any,
    ) -> Any:
        """Construct one fresh Agent from a validated static declaration."""

        if runtime_kwargs:
            unexpected = ", ".join(sorted(runtime_kwargs))
            raise TypeError(f"AgentRegistry.instantiate 不接受未知运行时参数: {unexpected}")
        config = self.validate(raw)
        logger.info("实例化 fresh agent: name=%s lifecycle=%s", config.name, config.lifecycle)
        return self._instantiate_agent(
            config,
            model=model,
            runtime_config_path=runtime_config_path,
            model_name=model_name,
            model_effort=model_effort,
            juice_root=juice_root,
            tool_factories=tool_factories,
        )

    def _skills_enabled(self, runtime_config_path: str | Path | None = None) -> bool:
        try:
            context = self._config_context_for_runtime(runtime_config_path)
            raw_skills = context.read_merged_config().get("skills", {})
        except Exception:
            return True
        if not isinstance(raw_skills, dict):
            return True
        return bool(raw_skills.get("enabled", True))

    def _automatic_capability_enabled(
        self,
        section: str,
        runtime_config_path: str | Path | None = None,
    ) -> bool:
        """Read an opt-out capability switch with backward-compatible defaults."""

        try:
            raw = self._config_context_for_runtime(runtime_config_path).read_merged_config().get(section, {})
        except Exception:
            return True
        if not isinstance(raw, dict):
            return True
        enabled = raw.get("enabled", True)
        return enabled if isinstance(enabled, bool) else True

    def _config_context_for_runtime(self, runtime_config_path: str | Path | None) -> ConfigurationContext:
        if runtime_config_path is None:
            return self.config_context
        return replace(self.config_context, project_config_path=Path(runtime_config_path).expanduser().resolve())

    def _workspace_runtime_defaults(self, runtime_config_path: str | Path | None) -> dict[str, str]:
        try:
            runtime = self._config_context_for_runtime(runtime_config_path).read_merged_config().get("runtime", {})
        except Exception:
            runtime = {}
        if not isinstance(runtime, dict):
            runtime = {}
        return {
            "agent_type": normalize_agent_type(runtime.get("agent_type"), default="react"),
            "model_name": str(runtime.get("model_name") or DEFAULT_RUNTIME_MODEL_NAME).strip() or DEFAULT_RUNTIME_MODEL_NAME,
            "model_effort": normalize_runtime_model_effort(
                str(runtime.get("model_effort") or "disabled").strip() or "disabled"
            ),
        }

    def resolve_effective_agent_type(
        self,
        config: AgentConfig | Mapping[str, Any] | str,
        *,
        runtime_config_path: str | Path | None = None,
    ) -> str:
        """Resolve one declaration to the concrete protocol used by a fresh instance.

        The Registry intentionally reads the workspace default only for
        ``agent_type: default``.  Explicit ReAct/CodeAct declarations remain
        portable and are never overwritten by the global selector.
        """

        resolved = self.validate(config)
        if resolved.agent_type != "default":
            return normalize_agent_type(resolved.agent_type)
        return self._workspace_runtime_defaults(runtime_config_path)["agent_type"]

    def _resolve_agent_model_settings(
        self,
        config: AgentConfig,
        *,
        runtime_config_path: str | Path | None,
        model_name: str | None,
        model_effort: str | None,
    ) -> tuple[str, str]:
        runtime_defaults = self._workspace_runtime_defaults(runtime_config_path)
        configured_model = str(config.model_config_name or DEFAULT_MODEL_CONFIG_NAME).strip()
        explicit_model_name = str(model_name or "").strip()
        if _is_default_model_value(explicit_model_name):
            explicit_model_name = ""
        resolved_model_name = (
            explicit_model_name
            or ("" if _is_default_model_value(configured_model) else configured_model)
            or runtime_defaults["model_name"]
            or DEFAULT_RUNTIME_MODEL_NAME
        )
        configured_effort = str(config.model_effort or DEFAULT_MODEL_EFFORT).strip().lower()
        explicit_model_effort = str(model_effort or "").strip().lower()
        if explicit_model_effort == DEFAULT_MODEL_EFFORT:
            explicit_model_effort = ""
        resolved_model_effort = (
            explicit_model_effort
            or ("" if configured_effort in {"", DEFAULT_MODEL_EFFORT} else configured_effort)
            or runtime_defaults["model_effort"]
            or "disabled"
        )
        return resolved_model_name, normalize_runtime_model_effort(resolved_model_effort)

    def _instantiate_agent(
        self,
        config: AgentConfig,
        *,
        model: Any | None = None,
        runtime_config_path: str | Path | None = None,
        model_name: str | None = None,
        model_effort: str | None = None,
        juice_root: str | Path | None = None,
        tool_factories: dict[str, Callable] | None = None,
    ) -> Any:
        from juice_agents.core.agent.agents import CodeActAgent, ReActAgent

        resolved_agent_type = self.resolve_effective_agent_type(
            config,
            runtime_config_path=runtime_config_path,
        )

        resolved_model_name, resolved_model_effort = self._resolve_agent_model_settings(
            config,
            runtime_config_path=runtime_config_path,
            model_name=model_name,
            model_effort=model_effort,
        )
        if model is None:
            model = resolve_runtime_model(
                model_name=resolved_model_name,
                runtime_config_path=runtime_config_path or self.config_context.project_config_path,
                workspace_dir=self.config_context.workspace_dir,
                dotenv_path=self.config_context.dotenv_path,
                runtime_overrides=self.config_context.overrides,
                model_effort=resolved_model_effort,
            )
        compression_model = model
        compression_model_name = (
            config.session_compression.compact.compression_model_config_name
        )
        if compression_model_name is not None:
            compression_model = resolve_runtime_model(
                model_name=compression_model_name,
                runtime_config_path=runtime_config_path or self.config_context.project_config_path,
                workspace_dir=self.config_context.workspace_dir,
                dotenv_path=self.config_context.dotenv_path,
                runtime_overrides=self.config_context.overrides,
                model_effort=resolved_model_effort,
            )

        resolved_juice_root = Path(juice_root) if juice_root is not None else self.config_context.juice_root
        # Visibility is decided before factories run, so hidden capabilities do
        # not leak into prompt schemas, callable maps, or CodeAct executors.
        from juice_agents.core.agent.tools.builtin.evolution.constants import SELF_EVOLUTION_TOOL_NAMES
        from juice_agents.core.agent.tools.builtin.workflows.constants import WORKFLOW_TOOL_NAMES

        workflow_tools_enabled = bool(config.enable_workflow_tools) and self._automatic_capability_enabled(
            "workflows", runtime_config_path
        )
        evolution_enabled = self._automatic_capability_enabled("self_evolution", runtime_config_path)
        tools_config = [
            tool.to_dict()
            for tool in config.tools
            if (workflow_tools_enabled or tool.name not in WORKFLOW_TOOL_NAMES)
            and (evolution_enabled or tool.name not in SELF_EVOLUTION_TOOL_NAMES)
        ]
        tools = self._tool_registry.instantiate_batch(
            tools_config=tools_config,
            juice_root=resolved_juice_root,
            agent_name=config.name,
            runtime_config_path=runtime_config_path,
            model_config_name=resolved_model_name,
            extra_factories=tool_factories,
        )

        common_kwargs: dict[str, Any] = {
            "tools": tools,
            "config_context": self._config_context_for_runtime(runtime_config_path),
            "model": model,
            "system_prompt": config.system_prompt,
            "max_steps": config.max_steps,
            "context_window_tokens": config.context_window_tokens,
            "max_tool_calls_per_step": config.max_tool_calls_per_step,
            "session_compression": config.session_compression.to_dict(),
            "compression_model": compression_model,
            "name": config.name,
            "description": config.description,
            "skill_names": list(config.skill_names),
            "disabled_skill_names": list(config.disabled_skill_names),
            "local_skills_path": resolved_juice_root / "skills",
            "skills_config_path": Path(runtime_config_path or self.config_context.project_config_path),
            "instructions": config.instructions,
            "prompt_language": config.prompt_language,
            "output_schema": deepcopy(config.output_schema),
            "log_file_path": config.log_file_path,
            "enable_skill_tools": bool(config.enable_skill_tools) and self._skills_enabled(runtime_config_path),
            "enable_workflow_tools": workflow_tools_enabled,
            "enable_self_evolution": evolution_enabled,
        }

        if resolved_agent_type == "codeact":
            agent = CodeActAgent(
                additional_authorized_imports=list(config.additional_authorized_imports),
                **common_kwargs,
            )
            return _attach_declared_agent_metadata(
                agent,
                config,
                config_context=self._config_context_for_runtime(runtime_config_path),
                agent_config_dir=self.config_dir,
                resolved_agent_type=resolved_agent_type,
            )
        agent = ReActAgent(**common_kwargs)
        return _attach_declared_agent_metadata(
            agent,
            config,
            config_context=self._config_context_for_runtime(runtime_config_path),
            agent_config_dir=self.config_dir,
            resolved_agent_type=resolved_agent_type,
        )

    def list(self) -> list[str]:
        try:
            return sorted(self.list_configs())
        except FileNotFoundError:
            return []


__all__ = [
    "AgentRegistry",
    "DEFAULT_RUNTIME_CONFIG_PATH",
    "_attach_declared_agent_metadata",
]
