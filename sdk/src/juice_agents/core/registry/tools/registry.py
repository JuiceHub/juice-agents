"""
Tool registry：统一管理工具配置与运行时 Tool 实例创建。
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any, Callable

from juice_agents.core.agent.tools.runtime.base_tools import Tool
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.config.model_catalog import DEFAULT_RUNTIME_MODEL_NAME, load_runtime_model_config
from juice_agents.core.config.runtime_config import DEFAULT_RUNTIME_CONFIG_PATH
from juice_agents.core.registry.tools.defaults import (
    get_builtin_tool_context_defaults,
    get_builtin_tool_context_requirements,
    get_default_tool_factories,
)
from juice_agents.core.registry.tools.store import ToolConfigStore
from juice_agents.core.registry.tools.types import ToolConfig, ToolRef

logger = logging.getLogger(__name__)


def _safe_identifier(raw: str, *, fallback: str = "generated_tool") -> str:
    base = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(raw or ""))
    base = base.strip("_")
    if not base:
        base = fallback
    if base[0].isdigit():
        base = f"tool_{base}"
    return base


def _tool_class_name(tool_name: str) -> str:
    chunks = [chunk for chunk in _safe_identifier(tool_name).split("_") if chunk]
    if not chunks:
        return "GeneratedTool"
    return "".join(item[:1].upper() + item[1:] for item in chunks) + "Tool"


def _create_forward_callable(*, forward_source: str, imports: list[str]) -> Callable[..., Any]:
    namespace: dict[str, Any] = {}
    for item in imports:
        exec(f"import {item}", namespace, namespace)
    exec(forward_source, namespace, namespace)
    forward_callable = namespace.get("forward")
    if not callable(forward_callable):
        raise ValueError("forward 源码中未定义可调用对象: forward")
    signature = inspect.signature(forward_callable)
    params = list(signature.parameters.values())
    if not params:
        raise ValueError("forward 必须包含 self 参数")
    return forward_callable


def create_dynamic_tool_class(config: dict[str, Any] | ToolConfig) -> type[Tool]:
    """从 ToolConfig 生成动态 Tool 子类。"""
    tool_config = ToolConfig.from_dict(config) if not isinstance(config, ToolConfig) else config
    forward_callable = _create_forward_callable(
        forward_source=tool_config.forward,
        imports=list(tool_config.imports),
    )
    tool_name = tool_config.name
    class_name = _tool_class_name(tool_name)
    description = tool_config.description
    inputs = dict(tool_config.inputs)
    outputs = dict(tool_config.outputs)
    init_defaults = dict(tool_config.init_params)
    runtime = tool_config.runtime
    source = tool_config.forward
    imports_list = list(tool_config.imports)
    max_observation_chars = tool_config.max_observation_chars

    def __init__(self, **kwargs: Any) -> None:
        super(dynamic_cls, self).__init__()
        resolved = dict(init_defaults)
        resolved.update(kwargs)
        self.runtime = runtime
        self.forward_source = source
        self.imports = imports_list
        for key, value in resolved.items():
            setattr(self, key, value)

    def execution_policy(self, args: dict[str, Any], context: Any) -> Any:
        """Dynamic YAML tools are conservative until explicitly classified."""

        del args, context
        from juice_agents.core.agent.tools.runtime import ToolExecutionMode, ToolExecutionPolicy

        return ToolExecutionPolicy(mode=ToolExecutionMode.SERIAL)

    attrs = {
        "name": tool_name,
        "description": description,
        "inputs": inputs,
        "outputs": outputs,
        "max_observation_chars": max_observation_chars,
        "__init__": __init__,
        "execution_policy": execution_policy,
        "forward": forward_callable,
    }
    dynamic_cls = type(class_name, (Tool,), attrs)
    return dynamic_cls


def _normalize_tool_spec(item: Any) -> tuple[str, dict[str, Any]]:
    ref = ToolRef.from_raw(item, field_name="tools 配置项")
    return ref.name, dict(ref.params)


class ToolRegistry:
    """声明式 Tool 的静态解析、校验与 fresh construction 入口。

    该类不缓存 Tool 实例，也不绑定 owner、Runner、权限或并发控制。那些
    运行态职责属于 ``ToolManager``；每一次 ``instantiate`` 都返回一个新对象。
    """

    def __init__(
        self,
        config_dir: str | Path | None = None,
        *,
        config_context: ConfigurationContext | None = None,
    ) -> None:
        self.config_context = config_context or ConfigurationContext.from_workspace(None)
        if config_dir is not None:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = self.config_context.tools_dir
        self._store = ToolConfigStore(self.config_dir)

    @classmethod
    def default(cls, config_dir: str | Path | None = None) -> "ToolRegistry":
        return cls(config_dir=config_dir)

    def save_config(self, config: dict[str, Any] | ToolConfig, *, name: str | None = None) -> ToolConfig:
        """Validate and write one workspace Tool YAML directly."""
        return self._store.save(config, name=name)

    def load_config(self, name: str) -> ToolConfig:
        normalized = str(name or "").strip()
        return self._store.load(normalized)

    def list_configs(self) -> list[str]:
        return self._store.list()

    def delete_config(self, name: str) -> None:
        """Delete one workspace Tool YAML; built-in Python tools are unaffected."""
        self._store.delete(name)

    def _resolve_named_factory(
        self,
        tool_name: str,
        *,
        extra_factories: dict[str, Callable[..., Tool]] | None = None,
    ) -> Callable[..., Tool] | None:
        factories = get_default_tool_factories()
        if extra_factories:
            factories.update(extra_factories)
        factory = factories.get(tool_name)
        if factory is not None:
            return factory
        try:
            cfg = self.load_config(tool_name)
        except FileNotFoundError:
            return None
        return create_dynamic_tool_class(cfg)

    def _default_agents_dir(self) -> Path:
        return self.config_context.agents_dir

    def _context_defaults_for(self, tool_name: str) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for param_name in get_builtin_tool_context_defaults().get(tool_name, tuple()):
            if param_name == "agent_config_dir":
                defaults[param_name] = str(self._default_agents_dir())
            if param_name == "tool_config_dir":
                defaults[param_name] = str(self.config_dir)
        return defaults

    def _prepare_builtin_params(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        为已注册内置工具补齐 registry 能推导的上下文，并提前校验运行时上下文。

        Registry 仅补齐可从静态 workspace 配置推导的参数；请求身份、权限、
        并发与取消上下文由 ToolManager 在运行时绑定，不能在这里伪造。
        """
        prepared = self._context_defaults_for(tool_name)
        prepared.update(params)
        missing = [
            name
            for name in get_builtin_tool_context_requirements().get(tool_name, tuple())
            if prepared.get(name) is None
        ]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"工具 {tool_name} 已注册，但需要运行时参数: {joined}")
        return prepared

    def resolve(
        self,
        raw: str | dict[str, Any] | ToolRef | ToolConfig,
        *,
        extra_factories: dict[str, Callable[..., Tool]] | None = None,
    ) -> ToolConfig | str:
        """Resolve a static Tool declaration without accepting a live Tool."""

        if isinstance(raw, ToolConfig):
            return ToolConfig.from_dict(raw)
        if isinstance(raw, ToolRef):
            return self.resolve(raw.name, extra_factories=extra_factories)
        if isinstance(raw, dict):
            return ToolConfig.from_dict(raw)
        if isinstance(raw, str):
            normalized_name = str(raw or "").strip()
            if not normalized_name:
                raise ValueError("tool name 不能为空")
            if normalized_name in get_default_tool_factories() or normalized_name in dict(extra_factories or {}):
                return normalized_name
            try:
                return self.load_config(normalized_name)
            except FileNotFoundError:
                available_names = self.list()
                raise ValueError(f"未知工具: {normalized_name}，可选工具: {available_names}") from None
        raise TypeError("tool 只支持通过 ToolRef / name / config object 解析")

    def validate(
        self,
        raw: str | dict[str, Any] | ToolRef | ToolConfig,
        *,
        extra_factories: dict[str, Callable[..., Tool]] | None = None,
    ) -> ToolConfig | str:
        """Validate and canonicalize a static Tool declaration.

        Dynamic Tool source is syntactically checked by ``ToolConfig`` here.
        Importing its requested modules and allocating the Tool are deliberately
        deferred to ``instantiate`` so validation stays side-effect free.
        """

        resolved = self.resolve(raw, extra_factories=extra_factories)
        if isinstance(resolved, str):
            return resolved
        return ToolConfig.from_dict(resolved)

    def instantiate(
        self,
        raw: str | dict[str, Any] | ToolRef | ToolConfig,
        *,
        params: dict[str, Any] | None = None,
        extra_factories: dict[str, Callable[..., Tool]] | None = None,
        **runtime_kwargs: Any,
    ) -> Tool:
        """Build one fresh Tool from a validated static declaration."""

        # ``extra_factories`` is a construction-time class mapping only.  It
        # must not carry a live Tool, permission context, or Runner state.
        resolved = self.validate(raw, extra_factories=extra_factories)
        final_params = dict(params or {})
        final_params.update(runtime_kwargs)
        if isinstance(resolved, str):
            tool_name = resolved.strip()
            factory = self._resolve_named_factory(tool_name, extra_factories=extra_factories)
            if factory is None:
                default_names = sorted(get_default_tool_factories().keys())
                raise ValueError(f"未知工具: {tool_name}，可选内置工具: {default_names}")
            logger.info("实例化 fresh tool: name=%s source=builtin", tool_name)
            return factory(**self._prepare_builtin_params(tool_name, final_params))

        defaults = dict(resolved.init_params)
        defaults.update(final_params)
        dynamic_cls = create_dynamic_tool_class(resolved)
        logger.info("实例化 fresh tool: name=%s source=config", resolved.name)
        return dynamic_cls(**defaults)

    def instantiate_batch(
        self,
        tools_config: list[dict[str, Any] | str],
        *,
        project_root: Path | None = None,
        juice_root: Path | None = None,
        agent_name: str,
        runtime_config_path: str | Path | None = None,
        model_config_name: str,
        extra_factories: dict[str, Callable[..., Tool]] | None = None,
    ) -> list[Tool]:
        """批量实例化工具，并为需要工作区/runtime 配置的工具补齐参数。"""
        tools: list[Tool] = []
        model_cfg: Any = None
        resolved_juice_root = (
            Path(juice_root).resolve()
            if juice_root is not None
            else self.config_context.juice_root
        )
        resolved_config_path = (
            Path(runtime_config_path).resolve()
            if runtime_config_path is not None
            else self.config_context.project_config_path
        )

        for item in tools_config:
            tool_name, params = _normalize_tool_spec(item)
            if tool_name == "todos" and "file_path" not in params:
                params["file_path"] = str(resolved_juice_root / "todos" / f"{agent_name}.md")
            if tool_name == "plan" and "file_path" not in params:
                params["file_path"] = str(resolved_juice_root / "plans" / f"{agent_name}.md")
            if tool_name == "api_web_search" and "config_path" not in params:
                params["config_path"] = str(self.config_context.project_config_path)
            if tool_name in {"skills_list", "skill_view", "skill_manage"}:
                params.setdefault("local_dir", str(resolved_juice_root / "skills"))
                params.setdefault("config_path", str(resolved_config_path))
            if tool_name == "generate_edit_image" and not str(params.get("api_key", "")).strip():
                if model_cfg is None:
                    try:
                        model_cfg = load_runtime_model_config(
                            model_name=model_config_name or DEFAULT_RUNTIME_MODEL_NAME,
                            runtime_config_path=resolved_config_path,
                            workspace_dir=self.config_context.workspace_dir,
                        )
                    except ValueError:
                        model_cfg = None
                if model_cfg is not None:
                    if getattr(model_cfg, "api_key", None):
                        params.setdefault("api_key", model_cfg.api_key)
                    if getattr(model_cfg, "api_base", None):
                        params.setdefault("api_base", model_cfg.api_base)
            tools.append(self.instantiate(tool_name, params=params, extra_factories=extra_factories))
        return tools

    def list(self) -> list[str]:
        builtin_names = set(get_default_tool_factories().keys())
        configured_names = set(self.list_configs())
        overlap = sorted(builtin_names & configured_names)
        if overlap:
            raise ValueError(f"workspace tool 不得覆盖 builtin tool: {overlap}")
        names = builtin_names | configured_names
        return sorted(names)


__all__ = [
    "ToolRegistry",
    "create_dynamic_tool_class",
]
