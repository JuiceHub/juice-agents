"""
AgentConfig 强类型定义。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from juice_agents.core.registry.common import normalize_name
from juice_agents.core.registry.tools.types import ToolRef

SUPPORTED_AGENT_TYPES = {"react", "codeact"}
SUPPORTED_AGENT_TYPE_POLICIES = {"default", *SUPPORTED_AGENT_TYPES}
SUPPORTED_PROMPT_LANGUAGES = {"en", "zh"}
SUPPORTED_OUTPUT_SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
SUPPORTED_AGENT_LIFECYCLES = {"functional", "persistent"}
SUPPORTED_AGENT_ISOLATIONS = {"none", "worktree"}
DEFAULT_MODEL_CONFIG_NAME = "default"
DEFAULT_MODEL_EFFORT = "default"
SUPPORTED_AGENT_MODEL_EFFORTS = {"default", "disabled", "low", "medium", "high", "xhigh", "max", "auto"}
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_MAX_TOOL_CALLS_PER_STEP = 8


def normalize_agent_type(value: Any, *, default: str = "react") -> str:
    """Normalize public agent_type inputs to the supported protocol names."""

    normalized = str(value or default).strip().lower() or default
    if normalized == "n/a":
        normalized = default
    if normalized not in SUPPORTED_AGENT_TYPES:
        raise ValueError(f"不支持的 agent_type: {normalized}，可选值: {sorted(SUPPORTED_AGENT_TYPES)}")
    return normalized


def normalize_agent_type_policy(value: Any, *, default: str = "default") -> str:
    """Normalize the AgentConfig declaration without resolving workspace defaults.

    ``default`` is deliberately kept in the persisted declaration.  The
    concrete ReAct/CodeAct choice belongs to :class:`AgentRegistry`, which
    resolves it when a fresh runtime instance is created.
    """

    normalized = str(value or default).strip().lower() or default
    if normalized not in SUPPORTED_AGENT_TYPE_POLICIES:
        raise ValueError(
            "不支持的 agent_type 策略: "
            f"{normalized}，可选值: {sorted(SUPPORTED_AGENT_TYPE_POLICIES)}"
        )
    return normalized


def _validate_output_schema_contract(output_schema: dict[str, Any]) -> None:
    schema_type = output_schema.get("type")
    allowed_type_repr = sorted(SUPPORTED_OUTPUT_SCHEMA_TYPES)
    if isinstance(schema_type, str):
        schema_types = [schema_type]
    elif isinstance(schema_type, list) and schema_type and all(isinstance(item, str) for item in schema_type):
        schema_types = list(schema_type)
    else:
        raise ValueError(
            "output_schema.type 必须为 string 或 list[string]，例如 'object' 或 ['object','null']"
        )

    for item in schema_types:
        if item not in SUPPORTED_OUTPUT_SCHEMA_TYPES:
            raise ValueError(f"output_schema.type 不支持 {item!r}，可选值: {allowed_type_repr}")

    is_object_schema = "object" in schema_types
    properties = output_schema.get("properties")
    if is_object_schema:
        if properties is not None:
            if not isinstance(properties, dict):
                raise ValueError("output_schema.properties 必须为 object")
            for field_name, field_schema in properties.items():
                if not isinstance(field_schema, dict):
                    raise ValueError(f"output_schema.properties.{field_name} 必须为 object")
                field_type = field_schema.get("type")
                if not field_type:
                    raise ValueError(f"output_schema.properties.{field_name} 缺少 type")
        elif output_schema.get("additionalProperties") is False or output_schema.get("required"):
            raise ValueError("output_schema 当限制 object 字段时，必须提供 properties 对象")

    required = output_schema.get("required")
    if required is not None:
        if not isinstance(required, list) or any(not isinstance(item, str) or not item.strip() for item in required):
            raise ValueError("output_schema.required 必须为非空字符串数组")
        if isinstance(properties, dict):
            missing_keys = [key for key in required if key not in properties]
            if missing_keys:
                raise ValueError(f"output_schema.required 包含未定义字段: {missing_keys}")


def _normalize_string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} 必须为 list[str]")
    return tuple(item.strip() for item in value if item.strip())


def _normalize_allowed_modes(value: Any) -> tuple[str, ...] | None:
    """Normalize the optional mode applicability declaration.

    ``None`` deliberately means "all modes" rather than only the built-in
    modes.  A Registry cannot know every custom ``RunnerConfig`` that a host
    might register, so rejecting an unknown mode here would make otherwise
    portable declarations impossible.  An explicitly empty list is almost
    certainly a configuration error and would create an invisible agent, so
    it is rejected at the declaration boundary.
    """

    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ValueError("allowed_modes 必须为 list[str] 或 null")
    normalized = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
    if not normalized:
        raise ValueError("allowed_modes 不能为空；请使用 null 表示所有 mode")
    return normalized


def _normalize_lifecycle(value: Any) -> str:
    lifecycle = str(value or "functional").strip() or "functional"
    if lifecycle not in SUPPORTED_AGENT_LIFECYCLES:
        raise ValueError(
            f"lifecycle 不支持 {lifecycle!r}，可选值: {sorted(SUPPORTED_AGENT_LIFECYCLES)}"
        )
    return lifecycle


def _normalize_isolation(value: Any) -> str:
    isolation = str(value or "none").strip() or "none"
    if isolation not in SUPPORTED_AGENT_ISOLATIONS:
        raise ValueError(
            f"isolation 不支持 {isolation!r}，可选值: {sorted(SUPPORTED_AGENT_ISOLATIONS)}"
        )
    return isolation


def _normalize_model_config_name(value: Any) -> str:
    model_config_name = str(value or DEFAULT_MODEL_CONFIG_NAME).strip()
    if not model_config_name:
        raise ValueError("agent 配置缺少 model_config_name")
    # Historical configs used runtime.shared_model for the same "follow workspace
    # runtime" behavior. Normalize at the boundary so compact writes stay stable.
    if model_config_name == "runtime.shared_model":
        return DEFAULT_MODEL_CONFIG_NAME
    return model_config_name


def _normalize_tool_refs(value: Any) -> tuple[ToolRef, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("tools 必须为 list")
    return tuple(ToolRef.from_raw(item, field_name=f"tools[{idx}]") for idx, item in enumerate(value))


@dataclass(frozen=True, slots=True)
class RemainCompressionConfig:
    """非破坏性 remain 投影：仅保留最近若干 ActionStep 的完整 observation。"""

    keep_recent_actions: int = 5

    def __post_init__(self) -> None:
        value = self.keep_recent_actions
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("session_compression.remain.keep_recent_actions 必须为非负整数")

    @classmethod
    def from_raw(cls, value: Any) -> "RemainCompressionConfig":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("session_compression.remain 必须为 object")
        return cls(keep_recent_actions=value.get("keep_recent_actions", 5))

    def to_dict(self) -> dict[str, Any]:
        return {"keep_recent_actions": self.keep_recent_actions}


@dataclass(frozen=True, slots=True)
class CompactCompressionConfig:
    """按 context window 比例触发的持久化 compact 配置。"""

    trigger_ratio: float = 0.8
    preserve_recent_actions: int = 5
    compression_model_config_name: str | None = None

    def __post_init__(self) -> None:
        ratio = self.trigger_ratio
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
            raise ValueError("session_compression.compact.trigger_ratio 必须为 (0, 1] 数字")
        ratio = float(ratio)
        if ratio <= 0 or ratio > 1:
            raise ValueError("session_compression.compact.trigger_ratio 必须位于 (0, 1]")
        recent = self.preserve_recent_actions
        if isinstance(recent, bool) or not isinstance(recent, int) or recent < 0:
            raise ValueError("session_compression.compact.preserve_recent_actions 必须为非负整数")
        model_name = self.compression_model_config_name
        if model_name is not None:
            model_name = str(model_name).strip()
            if not model_name:
                raise ValueError(
                    "session_compression.compact.compression_model_config_name 不能为空字符串"
                )
        object.__setattr__(self, "trigger_ratio", ratio)
        object.__setattr__(self, "compression_model_config_name", model_name)

    @classmethod
    def from_raw(cls, value: Any) -> "CompactCompressionConfig":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("session_compression.compact 必须为 object")
        return cls(
            trigger_ratio=value.get("trigger_ratio", 0.8),
            preserve_recent_actions=value.get("preserve_recent_actions", 5),
            compression_model_config_name=value.get("compression_model_config_name"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "trigger_ratio": self.trigger_ratio,
            "preserve_recent_actions": self.preserve_recent_actions,
        }
        if self.compression_model_config_name is not None:
            payload["compression_model_config_name"] = self.compression_model_config_name
        return payload


@dataclass(frozen=True, slots=True)
class SessionCompressionConfig:
    """remain + compact 的统一组合配置。"""

    remain: RemainCompressionConfig = field(default_factory=RemainCompressionConfig)
    compact: CompactCompressionConfig = field(default_factory=CompactCompressionConfig)

    def __post_init__(self) -> None:
        object.__setattr__(self, "remain", RemainCompressionConfig.from_raw(self.remain))
        object.__setattr__(self, "compact", CompactCompressionConfig.from_raw(self.compact))

    @classmethod
    def from_raw(cls, value: Any) -> "SessionCompressionConfig":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("session_compression 必须为 object")
        return cls(
            remain=RemainCompressionConfig.from_raw(value.get("remain")),
            compact=CompactCompressionConfig.from_raw(value.get("compact")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "remain": self.remain.to_dict(),
            "compact": self.compact.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AgentRef:
    """声明式 Agent 引用，供 RunnerConfig 与 AgentManager 绑定角色。

    这个值绝不能携带运行时 Agent 实例。实例、session 与取消状态完全由
    ``AgentManager`` 持有；Registry 只会把该引用解析成 YAML 声明。
    """

    name: str
    mode_id: str = "agent"
    role: str = "subagents"
    declaration_override: AgentConfig | dict[str, Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        mode_id = str(self.mode_id or "").strip()
        role = str(self.role or "").strip()
        if not name:
            raise ValueError("AgentRef.name 不能为空")
        if not mode_id:
            raise ValueError("AgentRef.mode_id 不能为空")
        if not role:
            raise ValueError("AgentRef.role 不能为空")
        if self.declaration_override is not None and not isinstance(
            self.declaration_override,
            (AgentConfig, dict),
        ):
            raise TypeError("AgentRef.declaration_override 必须是 AgentConfig 或 object")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "mode_id", mode_id)
        object.__setattr__(self, "role", role)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """
    registry 层统一的 Agent 可持久化配置对象。

    当前 deliberately 只承接 config_store 已稳定支持的字段，
    不追求完整镜像 agents.* 构造器的全部参数。
    """

    name: str
    agent_type: str = "default"
    model_config_name: str = DEFAULT_MODEL_CONFIG_NAME
    model_effort: str = DEFAULT_MODEL_EFFORT
    description: str = ""
    # ``None`` is intentionally different from an empty tuple: the former is
    # portable across all built-in and custom modes, while an empty sequence
    # is rejected because it can never be selected.
    allowed_modes: tuple[str, ...] | None = None
    instructions: str | None = None
    prompt_language: str = "en"
    max_steps: int = 5
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    max_tool_calls_per_step: int = DEFAULT_MAX_TOOL_CALLS_PER_STEP
    session_compression: SessionCompressionConfig = field(default_factory=SessionCompressionConfig)
    tools: tuple[ToolRef, ...] = field(default_factory=tuple)
    skill_names: tuple[str, ...] = field(default_factory=tuple)
    disabled_skill_names: tuple[str, ...] = field(default_factory=tuple)
    enable_skill_tools: bool = True
    # Graph capabilities follow the same per-agent declaration rule as Skills.
    # Global ``graphs.enabled`` is applied later by AgentRegistry, so this field
    # remains portable when the same AgentConfig is used in another workspace.
    enable_graph_tools: bool = True
    output_schema: dict[str, Any] | None = None
    log_file_path: str | None = None
    additional_authorized_imports: tuple[str, ...] = field(default_factory=tuple)
    system_prompt: str | None = None
    managed_agent_names: tuple[str, ...] = field(default_factory=tuple)
    lifecycle: str = "functional"
    isolation: str = "none"

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name:
            raise ValueError("agent 配置缺少 name")

        agent_type = normalize_agent_type_policy(self.agent_type)

        model_config_name = _normalize_model_config_name(self.model_config_name)
        model_effort = str(self.model_effort or DEFAULT_MODEL_EFFORT).strip().lower()
        if model_effort not in SUPPORTED_AGENT_MODEL_EFFORTS:
            raise ValueError(
                "model_effort 不合法: "
                f"{self.model_effort}. 可选值: {', '.join(sorted(SUPPORTED_AGENT_MODEL_EFFORTS))}"
            )

        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int) or self.max_steps <= 0:
            raise ValueError("max_steps 必须为正整数")
        if (
            isinstance(self.context_window_tokens, bool)
            or not isinstance(self.context_window_tokens, int)
            or self.context_window_tokens <= 0
        ):
            raise ValueError("context_window_tokens 必须为正整数")
        if (
            isinstance(self.max_tool_calls_per_step, bool)
            or not isinstance(self.max_tool_calls_per_step, int)
            or self.max_tool_calls_per_step <= 0
        ):
            raise ValueError("max_tool_calls_per_step 必须为正整数")
        session_compression = SessionCompressionConfig.from_raw(self.session_compression)

        prompt_language = str(self.prompt_language or "en").strip().lower() or "en"
        if prompt_language in {"cn", "zh-cn", "chinese"}:
            prompt_language = "zh"
        elif prompt_language in {"en-us", "english"}:
            prompt_language = "en"
        if prompt_language not in SUPPORTED_PROMPT_LANGUAGES:
            raise ValueError(
                f"不支持的 prompt_language: {prompt_language}，可选值: {sorted(SUPPORTED_PROMPT_LANGUAGES)}"
            )

        normalized_tools = tuple(
            tool if isinstance(tool, ToolRef) else ToolRef.from_raw(tool)
            for tool in tuple(self.tools or ())
        )
        normalized_skill_names = tuple(str(item).strip() for item in tuple(self.skill_names or ()) if str(item).strip())
        normalized_disabled_skill_names = tuple(
            str(item).strip()
            for item in tuple(self.disabled_skill_names or ())
            if str(item).strip()
        )
        normalized_imports = tuple(
            str(item).strip()
            for item in tuple(self.additional_authorized_imports or ())
            if str(item).strip()
        )
        normalized_managed_agent_names = tuple(
            str(item).strip()
            for item in tuple(self.managed_agent_names or ())
            if str(item).strip()
        )
        allowed_modes = _normalize_allowed_modes(self.allowed_modes)
        lifecycle = _normalize_lifecycle(self.lifecycle)
        isolation = _normalize_isolation(self.isolation)

        output_schema = self.output_schema
        if output_schema is not None:
            if not isinstance(output_schema, dict):
                raise ValueError("output_schema 必须为 object 或 null")
            output_schema = deepcopy(output_schema)
            _validate_output_schema_contract(output_schema)

        log_file_path = self.log_file_path
        if log_file_path is not None:
            log_file_path = str(log_file_path)

        system_prompt = self.system_prompt
        if system_prompt is not None:
            system_prompt = str(system_prompt)

        instructions = self.instructions
        if instructions is not None:
            instructions = str(instructions)

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "agent_type", agent_type)
        object.__setattr__(self, "model_config_name", model_config_name)
        object.__setattr__(self, "model_effort", model_effort)
        object.__setattr__(self, "description", str(self.description or ""))
        object.__setattr__(self, "allowed_modes", allowed_modes)
        object.__setattr__(self, "instructions", instructions)
        object.__setattr__(self, "prompt_language", prompt_language)
        object.__setattr__(self, "max_steps", self.max_steps)
        object.__setattr__(self, "context_window_tokens", self.context_window_tokens)
        object.__setattr__(self, "max_tool_calls_per_step", self.max_tool_calls_per_step)
        object.__setattr__(self, "session_compression", session_compression)
        object.__setattr__(self, "tools", normalized_tools)
        object.__setattr__(self, "skill_names", normalized_skill_names)
        object.__setattr__(self, "disabled_skill_names", normalized_disabled_skill_names)
        object.__setattr__(self, "output_schema", output_schema)
        object.__setattr__(self, "log_file_path", log_file_path)
        object.__setattr__(self, "additional_authorized_imports", normalized_imports)
        object.__setattr__(self, "system_prompt", system_prompt)
        object.__setattr__(self, "managed_agent_names", normalized_managed_agent_names)
        object.__setattr__(self, "lifecycle", lifecycle)
        object.__setattr__(self, "isolation", isolation)

    @classmethod
    def from_dict(cls, config: dict[str, Any] | "AgentConfig", *, config_name: str | None = None) -> "AgentConfig":
        if isinstance(config, cls):
            payload = config.to_dict()
        elif isinstance(config, dict):
            payload = dict(config)
        else:
            raise ValueError("agent 配置必须为 object")

        if config_name is not None:
            normalized_config_name = normalize_name(config_name)
            raw_name = str(payload.get("name", "") or "").strip()
            if raw_name and raw_name != normalized_config_name:
                raise ValueError("agent 配置中的 name 必须与 config_name 一致")
            payload["name"] = normalized_config_name

        if "name" not in payload or not str(payload.get("name", "")).strip():
            raise ValueError("agent 配置缺少 name")

        return cls(
            name=str(payload["name"]).strip(),
            agent_type=str(payload.get("agent_type", "default") or "default"),
            model_config_name=str(payload.get("model_config_name", DEFAULT_MODEL_CONFIG_NAME) or DEFAULT_MODEL_CONFIG_NAME),
            model_effort=str(payload.get("model_effort", DEFAULT_MODEL_EFFORT) or DEFAULT_MODEL_EFFORT),
            description=str(payload.get("description", "") or ""),
            allowed_modes=_normalize_allowed_modes(payload.get("allowed_modes")),
            instructions=None if payload.get("instructions") is None else str(payload.get("instructions")),
            prompt_language=str(payload.get("prompt_language", "en") or "en"),
            max_steps=payload.get("max_steps", 5),
            context_window_tokens=payload.get(
                "context_window_tokens",
                DEFAULT_CONTEXT_WINDOW_TOKENS,
            ),
            max_tool_calls_per_step=payload.get(
                "max_tool_calls_per_step",
                DEFAULT_MAX_TOOL_CALLS_PER_STEP,
            ),
            session_compression=SessionCompressionConfig.from_raw(
                payload.get("session_compression")
            ),
            tools=_normalize_tool_refs(payload.get("tools")),
            skill_names=_normalize_string_list(payload.get("skill_names", []), field_name="skill_names"),
            disabled_skill_names=_normalize_string_list(
                payload.get("disabled_skill_names", []),
                field_name="disabled_skill_names",
            ),
            enable_skill_tools=bool(payload.get("enable_skill_tools", True)),
            enable_graph_tools=bool(payload.get("enable_graph_tools", True)),
            output_schema=deepcopy(payload.get("output_schema")),
            log_file_path=None if payload.get("log_file_path") is None else str(payload.get("log_file_path")),
            additional_authorized_imports=_normalize_string_list(
                payload.get("additional_authorized_imports", []),
                field_name="additional_authorized_imports",
            ),
            system_prompt=None if payload.get("system_prompt") is None else str(payload.get("system_prompt")),
            managed_agent_names=_normalize_string_list(
                payload.get("managed_agent_names", []),
                field_name="managed_agent_names",
            ),
            lifecycle=str(payload.get("lifecycle") or "functional"),
            isolation=str(payload.get("isolation") or "none"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "agent_type": self.agent_type,
            "model_config_name": self.model_config_name,
            "model_effort": self.model_effort,
            "description": self.description,
            "allowed_modes": None if self.allowed_modes is None else list(self.allowed_modes),
            "instructions": self.instructions,
            "prompt_language": self.prompt_language,
            "max_steps": self.max_steps,
            "context_window_tokens": self.context_window_tokens,
            "max_tool_calls_per_step": self.max_tool_calls_per_step,
            "session_compression": self.session_compression.to_dict(),
            "system_prompt": self.system_prompt,
            "skill_names": list(self.skill_names),
            "enable_skill_tools": self.enable_skill_tools,
            "enable_graph_tools": self.enable_graph_tools,
            "output_schema": deepcopy(self.output_schema),
            "log_file_path": self.log_file_path,
            "additional_authorized_imports": list(self.additional_authorized_imports),
            "tools": [tool.to_dict() for tool in self.tools],
            "managed_agent_names": list(self.managed_agent_names),
            "lifecycle": self.lifecycle,
        }
        if self.disabled_skill_names:
            payload["disabled_skill_names"] = list(self.disabled_skill_names)
        if self.isolation != "none":
            payload["isolation"] = self.isolation
        return payload

    def to_persisted_dict(self) -> dict[str, Any]:
        """Return compact workspace registry YAML without inert or runner-owned fields."""

        payload = self.to_dict()
        # Runner owns concrete log paths. Persisting them in shared registry YAML
        # makes later sessions write into stale runner directories.
        payload.pop("log_file_path", None)
        for key in ("system_prompt", "output_schema"):
            if payload.get(key) is None:
                payload.pop(key, None)
        for key in (
            "allowed_modes",
            "skill_names",
            "disabled_skill_names",
            "additional_authorized_imports",
            "managed_agent_names",
        ):
            if not payload.get(key):
                payload.pop(key, None)
        if payload.get("isolation") == "none":
            payload.pop("isolation", None)
        return payload

    def copy_with(self, **changes: Any) -> "AgentConfig":
        payload = self.to_dict()
        payload.update(changes)
        return type(self).from_dict(payload)


__all__ = [
    "AgentConfig",
    "AgentRef",
    "RemainCompressionConfig",
    "CompactCompressionConfig",
    "SessionCompressionConfig",
    "DEFAULT_CONTEXT_WINDOW_TOKENS",
    "DEFAULT_MAX_TOOL_CALLS_PER_STEP",
    "DEFAULT_MODEL_CONFIG_NAME",
    "DEFAULT_MODEL_EFFORT",
    "SUPPORTED_AGENT_MODEL_EFFORTS",
    "SUPPORTED_AGENT_TYPES",
    "SUPPORTED_AGENT_TYPE_POLICIES",
    "SUPPORTED_AGENT_LIFECYCLES",
    "SUPPORTED_AGENT_ISOLATIONS",
    "SUPPORTED_OUTPUT_SCHEMA_TYPES",
    "SUPPORTED_PROMPT_LANGUAGES",
    "normalize_agent_type",
    "normalize_agent_type_policy",
]
