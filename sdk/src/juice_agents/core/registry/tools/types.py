"""
ToolConfig 与 ToolRef 强类型定义。
"""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable

from juice_agents.core.registry.common import normalize_name

SUPPORTED_TOOL_RUNTIMES = {"safe", "trusted"}
DEFAULT_CONFIGURED_TOOL_OBSERVATION_CHARS = 12_000


def _normalize_inputs_or_outputs(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须为 object")
    return deepcopy(dict(value))


def _normalize_imports(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ValueError("imports 必须为 list[str]")
    return tuple(item.strip() for item in value if item.strip())


def _normalize_forward(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("tool 配置缺少 forward，且必须是非空函数源码字符串")
    try:
        module = ast.parse(text)
    except SyntaxError as exc:
        raise ValueError(f"forward 不是合法 Python 源码: {exc}") from exc
    has_forward = any(isinstance(node, ast.FunctionDef) and node.name == "forward" for node in module.body)
    if not has_forward:
        raise ValueError("forward 源码中必须定义 def forward(...):")
    return text


def _normalize_runtime(value: Any) -> str:
    runtime = str(value or "safe").strip().lower()
    if runtime not in SUPPORTED_TOOL_RUNTIMES:
        raise ValueError(f"runtime 必须为 {sorted(SUPPORTED_TOOL_RUNTIMES)}")
    return runtime


@dataclass(frozen=True, slots=True)
class ToolRef:
    """
    Agent 侧对工具的轻量引用，仅描述“使用哪个工具 + 传什么参数”。

    注意：
    - 这不是完整 ToolConfig，仅用于 AgentConfig.tools。
    - params 使用深拷贝，避免模板/调用方复用同一 dict 时发生串改。
    """

    name: str
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        tool_name = str(self.name or "").strip()
        if not tool_name:
            raise ValueError("tool 缺少 name")
        raw_params = self.params if self.params is not None else {}
        if not isinstance(raw_params, dict):
            raise ValueError("tool.params 必须为 object")
        object.__setattr__(self, "name", tool_name)
        object.__setattr__(self, "params", deepcopy(dict(raw_params)))

    @classmethod
    def from_raw(cls, value: Any, *, field_name: str = "tool") -> "ToolRef":
        if isinstance(value, cls):
            return cls(name=value.name, params=value.params)
        if isinstance(value, str):
            tool_name = value.strip()
            if not tool_name:
                raise ValueError(f"{field_name} 不能为空字符串")
            return cls(name=tool_name, params={})
        if not isinstance(value, dict):
            raise ValueError(f"{field_name} 必须为 string 或 object")

        tool_name = str(value.get("name", "")).strip()
        if not tool_name:
            raise ValueError(f"{field_name} 缺少 name")

        raw_params = value.get("params")
        if raw_params is None:
            raw_params = {
                key: item
                for key, item in value.items()
                if key not in {"name", "params"}
            }
        if not isinstance(raw_params, dict):
            raise ValueError(f"{field_name}.params 必须为 object")
        return cls(name=tool_name, params=dict(raw_params))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params": deepcopy(self.params),
        }

    def copy_with(self, **changes: Any) -> "ToolRef":
        payload = self.to_dict()
        payload.update(changes)
        return type(self).from_raw(payload)


@dataclass(frozen=True, slots=True)
class ToolConfig:
    """
    registry 层统一的 Tool 可持久化配置对象。

    和 `AgentConfig` 一样，ToolConfig 负责字段校验、规范化以及
    dict <-> 强类型对象的往返转换。
    """

    name: str
    description: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    imports: tuple[str, ...] = field(default_factory=tuple)
    runtime: str = "safe"
    init_params: dict[str, Any] = field(default_factory=dict)
    max_observation_chars: int = DEFAULT_CONFIGURED_TOOL_OBSERVATION_CHARS
    forward: str = ""

    def __post_init__(self) -> None:
        name = normalize_name(str(self.name or ""))
        if not name:
            raise ValueError("tool 配置缺少 name")
        inputs = _normalize_inputs_or_outputs(self.inputs, field_name="inputs")
        outputs = _normalize_inputs_or_outputs(self.outputs, field_name="outputs")
        imports = _normalize_imports(self.imports)
        runtime = _normalize_runtime(self.runtime)
        init_params = self.init_params if self.init_params is not None else {}
        if not isinstance(init_params, dict):
            raise ValueError("init_params 必须为 object")
        max_observation_chars = self.max_observation_chars
        if (
            isinstance(max_observation_chars, bool)
            or not isinstance(max_observation_chars, int)
            or max_observation_chars <= 0
        ):
            raise ValueError("max_observation_chars 必须为正整数")
        forward = _normalize_forward(self.forward)

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", str(self.description or ""))
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "outputs", outputs)
        object.__setattr__(self, "imports", imports)
        object.__setattr__(self, "runtime", runtime)
        object.__setattr__(self, "init_params", deepcopy(dict(init_params)))
        object.__setattr__(self, "max_observation_chars", max_observation_chars)
        object.__setattr__(self, "forward", forward)

    @classmethod
    def from_dict(cls, config: dict[str, Any] | "ToolConfig", *, config_name: str | None = None) -> "ToolConfig":
        if isinstance(config, cls):
            payload = config.to_dict()
        elif isinstance(config, dict):
            payload = dict(config)
        else:
            raise ValueError("tool 配置必须为 object")

        if config_name:
            normalized_config_name = normalize_name(config_name)
            raw_name = str(payload.get("name", "") or "").strip()
            if raw_name and normalize_name(raw_name) != normalized_config_name:
                raise ValueError("tool 配置中的 name 必须与 config_name 一致")
            payload["name"] = normalized_config_name
        if "name" not in payload or not str(payload.get("name", "")).strip():
            raise ValueError("tool 配置缺少 name")

        return cls(
            name=str(payload["name"]),
            description=str(payload.get("description", "") or ""),
            inputs=payload.get("inputs"),
            outputs=payload.get("outputs"),
            imports=payload.get("imports", ()),
            runtime=payload.get("runtime", "safe"),
            init_params=payload.get("init_params", {}),
            max_observation_chars=payload.get(
                "max_observation_chars",
                DEFAULT_CONFIGURED_TOOL_OBSERVATION_CHARS,
            ),
            forward=payload.get("forward"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputs": deepcopy(self.inputs),
            "outputs": deepcopy(self.outputs),
            "imports": list(self.imports),
            "runtime": self.runtime,
            "init_params": deepcopy(self.init_params),
            "max_observation_chars": self.max_observation_chars,
            "forward": self.forward,
        }

    def copy_with(self, **changes: Any) -> "ToolConfig":
        payload = self.to_dict()
        payload.update(changes)
        return type(self).from_dict(payload)


def normalize_tool_ref(value: Any) -> dict[str, Any]:
    return ToolRef.from_raw(value).to_dict()


def normalize_tool_refs(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("tools 必须为 list")
    return [ToolRef.from_raw(item, field_name=f"tools[{idx}]").to_dict() for idx, item in enumerate(values)]


__all__ = [
    "SUPPORTED_TOOL_RUNTIMES",
    "DEFAULT_CONFIGURED_TOOL_OBSERVATION_CHARS",
    "ToolRef",
    "ToolConfig",
    "normalize_tool_ref",
    "normalize_tool_refs",
]
