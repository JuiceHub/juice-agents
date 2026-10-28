"""
统一运行时配置中的模型目录加载与模型解析。

该模块服务于 runner / prebuilt / TUI：
- 从合并运行时配置的 `models` 顶层读取逻辑模型目录；
- 从 `.env` / 环境变量解析密钥；
- 根据 `backend=openai|doubao|anthropic|composite` 选择具体模型实现。

性能优化：
- 模型实例复用：使用 WeakValueDictionary 缓存已创建的模型实例
- SDK 客户端复用：避免重复初始化 OpenAI/Anthropic 等客户端
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from weakref import WeakValueDictionary

from juice_agents.core.models.chat_models import ChatModel

from .runtime_config import (
    DEFAULT_DOTENV_PATH,
    DEFAULT_RUNTIME_CONFIG_PATH,
    get_evaluator_config,
    get_runtime_mapping,
    credential_paths,
    read_credential_values,
    resolve_env_value,
    resolve_runtime_config_path,
)

logger = logging.getLogger(__name__)

# 模型实例复用池：使用 WeakValueDictionary 自动回收未使用的实例
_MODEL_INSTANCE_POOL: WeakValueDictionary = WeakValueDictionary()
_pool_lock = __import__("threading").Lock()

DEFAULT_RUNTIME_MODEL_NAME = "doubao_lite"
RuntimeBackend = Literal["openai", "doubao", "anthropic"]
RuntimeModelEffort = Literal["disabled", "low", "medium", "high", "xhigh", "max", "auto"]
RuntimeThinkingMode = Literal["adaptive", "manual", "none"]
RUNTIME_MODEL_EFFORTS_ORDER: tuple[str, ...] = (
    "disabled",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "auto",
)
RUNTIME_MODEL_EFFORTS: set[str] = set(RUNTIME_MODEL_EFFORTS_ORDER)
DEFAULT_PROVIDER_EFFORTS: dict[str, tuple[str, ...]] = {
    "doubao": ("disabled", "low", "medium", "high", "auto"),
    "openai": ("disabled", "low", "medium", "high", "auto"),
}
# Opus 系列（4-7 / 4-8）支持完整的 effort 档位，包含 xhigh / max。
CLAUDE_OPUS_EFFORTS: tuple[str, ...] = ("disabled", "low", "medium", "high", "xhigh", "max")
CLAUDE_SONNET_46_EFFORTS: tuple[str, ...] = ("disabled", "low", "medium", "high", "max")
CLAUDE_DISABLED_ONLY_EFFORTS: tuple[str, ...] = ("disabled",)


@dataclass(frozen=True)
class RuntimeModelConfig:
    model_name: str
    backend: RuntimeBackend
    api_base: str = ""
    api_key_env: str = ""
    provider_model_name: str = ""
    api_key: str = field(default="", repr=False)


def _load_catalog_payload(
    runtime_config_path: str | Path | None,
    *,
    workspace_dir: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    resolved_path = resolve_runtime_config_path(runtime_config_path)
    if not resolved_path.exists():
        raise FileNotFoundError(f"模型目录文件不存在: {resolved_path}")
    return resolved_path, get_runtime_mapping(
        "models",
        config_path=resolved_path,
        workspace_dir=workspace_dir,
        overrides=runtime_overrides,
    )


def _require_model_entry_fields(model_name: str, raw: dict[str, Any], required: list[str]) -> None:
    for field in required:
        if not str(raw.get(field) or "").strip():
            raise ValueError(f"模型 {model_name} 缺少必填字段: {field}")


def _normalize_backend(model_name: str, raw: dict[str, Any]) -> RuntimeBackend:
    backend = str(raw.get("backend") or "").strip().lower()
    if backend not in {"openai", "doubao", "anthropic"}:
        raise ValueError(f"模型 {model_name} 的 backend 不合法: {backend}")
    return backend  # type: ignore[return-value]


def _read_env_value(
    name: str, *, dotenv_path: str | Path | None, workspace_dir: str | Path | None = None,
) -> str:
    """所有模型入口使用统一凭据来源，显式 .env 仍作为项目级来源。"""
    return resolve_env_value(
        name, dotenv_values=read_credential_values(workspace_dir=workspace_dir, dotenv_path=dotenv_path),
    )


def _resolve_api_key(
    *, api_key_env: str, dotenv_path: str | Path | None, workspace_dir: str | Path | None = None,
) -> str:
    value = _read_env_value(api_key_env, dotenv_path=dotenv_path, workspace_dir=workspace_dir)
    if not value:
        locations = ", ".join(str(path) for path in credential_paths(
            workspace_dir=workspace_dir, dotenv_path=dotenv_path,
        ))
        raise ValueError(f"模型密钥环境变量未配置: {api_key_env}；请设置进程环境变量，或写入 {locations}")
    return value


def _resolve_api_base(
    *, api_base: str, api_base_env: str, dotenv_path: str | Path | None,
    workspace_dir: str | Path | None = None,
) -> str:
    """
    解析 API base URL，优先级：api_base_env 指向的变量 > YAML 中的 api_base。

    便于在 .env 中切换官方地址与代理/中转地址，而无需修改模型目录 YAML。
    """
    if api_base_env:
        env_value = _read_env_value(api_base_env, dotenv_path=dotenv_path, workspace_dir=workspace_dir)
        if env_value:
            # URLs may embed credentials; only log the source variable name.
            logger.info("api_base 由环境变量 %s 覆盖", api_base_env)
            return env_value
    return api_base


def normalize_runtime_model_effort(model_effort: str | None) -> RuntimeModelEffort:
    normalized = str(model_effort or "disabled").strip().lower() or "disabled"
    if normalized not in RUNTIME_MODEL_EFFORTS:
        raise ValueError(
            "model_effort 不合法: "
            f"{model_effort}. 可选值: {', '.join(RUNTIME_MODEL_EFFORTS_ORDER)}"
        )
    return normalized  # type: ignore[return-value]


def _normalize_effort_list(raw: Any, *, model_name: str) -> list[RuntimeModelEffort]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"模型 {model_name} 的 supported_efforts 必须为 list")
    efforts: list[str] = []
    for item in raw:
        normalized = normalize_runtime_model_effort(str(item))
        if normalized not in efforts:
            efforts.append(normalized)
    if not efforts:
        raise ValueError(f"模型 {model_name} 的 supported_efforts 不能为空")
    # disabled 是 Juice 的统一关闭开关；即使模型配置漏写，也保留为首选回退。
    if "disabled" not in efforts:
        efforts.insert(0, "disabled")
    return efforts  # type: ignore[return-value]


def _infer_anthropic_supported_efforts(provider_model_name: str) -> tuple[str, ...]:
    model = str(provider_model_name or "").strip().lower()
    # Opus 5.5 / Sonnet 5.5 / Opus 5 / 4.8/4.7 支持完整 effort 档位（含 xhigh / max）
    if any(x in model for x in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7")):
        return CLAUDE_OPUS_EFFORTS
    if "claude-sonnet-4-6" in model or "claude-opus-4-6" in model:
        return CLAUDE_SONNET_46_EFFORTS
    if "claude-haiku-4-5" in model:
        return CLAUDE_DISABLED_ONLY_EFFORTS
    # Unknown Anthropic models default to no output_config.effort to avoid surfacing
    # an option that Anthropic may reject. Users can opt in per model via YAML.
    return CLAUDE_DISABLED_ONLY_EFFORTS


def infer_supported_efforts(*, backend: str, provider_model_name: str) -> list[RuntimeModelEffort]:
    normalized_backend = str(backend or "").strip().lower()
    if normalized_backend == "anthropic":
        values = _infer_anthropic_supported_efforts(provider_model_name)
    else:
        values = DEFAULT_PROVIDER_EFFORTS.get(normalized_backend, ("disabled",))
    return list(values)  # type: ignore[return-value]


def resolve_supported_efforts(
    model_name: str,
    raw: dict[str, Any],
) -> list[RuntimeModelEffort]:
    explicit = _normalize_effort_list(raw.get("supported_efforts"), model_name=model_name)
    if explicit:
        return explicit
    return infer_supported_efforts(
        backend=str(raw.get("backend") or ""),
        provider_model_name=str(raw.get("provider_model_name") or ""),
    )


def infer_thinking_mode(*, backend: str, provider_model_name: str) -> RuntimeThinkingMode:
    if str(backend or "").strip().lower() != "anthropic":
        return "none"
    model = str(provider_model_name or "").strip().lower()
    if "claude-haiku-4-5" in model:
        return "manual"
    return "adaptive"


def resolve_thinking_mode(raw: dict[str, Any]) -> RuntimeThinkingMode:
    raw_mode = str(raw.get("thinking_mode") or "").strip().lower()
    if not raw_mode:
        return infer_thinking_mode(
            backend=str(raw.get("backend") or ""),
            provider_model_name=str(raw.get("provider_model_name") or ""),
        )
    if raw_mode not in {"adaptive", "manual", "none"}:
        raise ValueError(f"thinking_mode 不合法: {raw_mode}")
    return raw_mode  # type: ignore[return-value]


def infer_supports_budget_tokens(*, backend: str, provider_model_name: str) -> bool:
    if str(backend or "").strip().lower() != "anthropic":
        return False
    model = str(provider_model_name or "").strip().lower()
    # Opus 5.5 / Sonnet 5.5 / Opus 5 / Opus 4.8 / Opus 4.7 不支持 budget_tokens（仅通过 effort 控制）
    excluded = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7")
    return not any(x in model for x in excluded)


def resolve_supports_budget_tokens(raw: dict[str, Any]) -> bool:
    raw_value = raw.get("supports_budget_tokens")
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is not None:
        raise ValueError("supports_budget_tokens 必须为 bool")
    return infer_supports_budget_tokens(
        backend=str(raw.get("backend") or ""),
        provider_model_name=str(raw.get("provider_model_name") or ""),
    )


def validate_runtime_model_effort(
    *,
    model_name: str,
    model_effort: str | None,
    runtime_config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> RuntimeModelEffort:
    normalized = normalize_runtime_model_effort(model_effort)
    _, models = _load_catalog_payload(
        runtime_config_path, workspace_dir=workspace_dir, runtime_overrides=runtime_overrides,
    )
    raw_model = models.get(str(model_name or "").strip())
    if not isinstance(raw_model, dict):
        raise ValueError(f"模型目录中不存在模型: {model_name}")
    supported = resolve_supported_efforts(str(model_name or "").strip(), raw_model)
    if normalized not in supported:
        raise ValueError(
            f"模型 {model_name} 不支持 model_effort={normalized}. "
            f"可选值: {', '.join(supported)}"
        )
    return normalized


def build_thinking_override(model_effort: str | None) -> dict[str, Any]:
    """构建 Doubao/OpenAI 兼容的 thinking 参数。"""
    normalized = normalize_runtime_model_effort(model_effort)
    if normalized == "disabled":
        return {"type": "disabled"}
    return {"type": "enabled", "effort": normalized}


def build_anthropic_thinking_config(model_effort: str | None) -> dict[str, Any]:
    """构建 Anthropic thinking 参数：disabled 或 adaptive。"""
    normalized = normalize_runtime_model_effort(model_effort)
    if normalized == "disabled":
        return {"type": "disabled"}
    return {"type": "adaptive"}


def build_anthropic_effort_config(model_effort: str | None) -> dict[str, Any] | None:
    """构建 Anthropic output_config，仅在 effort 非 disabled 时返回。"""
    normalized = normalize_runtime_model_effort(model_effort)
    if normalized == "disabled":
        return None
    # auto 不是 Anthropic 官方值，映射为 high
    effort = "high" if normalized == "auto" else normalized
    return {"effort": effort}


def load_runtime_model_config(
    *,
    model_name: str,
    runtime_config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    dotenv_path: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> RuntimeModelConfig:
    normalized_model_name = str(model_name or "").strip()
    if not normalized_model_name:
        raise ValueError("model_name 不能为空")

    _, models = _load_catalog_payload(
        runtime_config_path, workspace_dir=workspace_dir, runtime_overrides=runtime_overrides,
    )
    raw_model = models.get(normalized_model_name)
    if not isinstance(raw_model, dict):
        raise ValueError(f"模型目录中不存在模型: {normalized_model_name}")

    _require_model_entry_fields(normalized_model_name, raw_model, ["backend"])
    backend = _normalize_backend(normalized_model_name, raw_model)

    _require_model_entry_fields(
        normalized_model_name,
        raw_model,
        ["api_base", "api_key_env", "provider_model_name"],
    )

    api_key_env = str(raw_model.get("api_key_env") or "").strip()
    provider_model_name = str(raw_model.get("provider_model_name") or "").strip()
    # api_base_env 可选：配置后其指向的变量（如 ANTHROPIC_BASE_URL）优先于 YAML 的 api_base
    api_base = _resolve_api_base(
        api_base=str(raw_model.get("api_base") or "").strip(),
        api_base_env=str(raw_model.get("api_base_env") or "").strip(),
        dotenv_path=dotenv_path,
        workspace_dir=workspace_dir,
    )
    api_key = _resolve_api_key(
        api_key_env=api_key_env, dotenv_path=dotenv_path, workspace_dir=workspace_dir,
    )

    return RuntimeModelConfig(
        model_name=normalized_model_name,
        backend=backend,
        api_base=api_base,
        api_key_env=api_key_env,
        provider_model_name=provider_model_name,
        api_key=api_key,
    )


def list_runtime_models(
    *,
    runtime_config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    _, models = _load_catalog_payload(
        runtime_config_path, workspace_dir=workspace_dir, runtime_overrides=runtime_overrides,
    )
    items: list[dict[str, Any]] = []
    for model_name in sorted(models.keys()):
        raw = dict(models.get(model_name) or {})
        _require_model_entry_fields(model_name, raw, ["backend"])
        backend = _normalize_backend(model_name, raw)
        _require_model_entry_fields(
            model_name,
            raw,
            ["api_base", "api_key_env", "provider_model_name"],
        )
        item = {
            "model_name": model_name,
            "backend": backend,
            "api_base": str(raw.get("api_base") or "").strip(),
            "api_key_env": str(raw.get("api_key_env") or "").strip(),
            "provider_model_name": str(raw.get("provider_model_name") or "").strip(),
            "supported_efforts": resolve_supported_efforts(model_name, raw),
            "thinking_mode": resolve_thinking_mode(raw),
            "supports_budget_tokens": resolve_supports_budget_tokens(raw),
        }
        items.append(item)
    return items


def resolve_runtime_model(
    *,
    model_name: str,
    runtime_config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    dotenv_path: str | Path | None = None,
    model_effort: str | None = None,
    runtime_overrides: dict[str, Any] | None = None,
    _resolution_stack: tuple[str, ...] = (),
    _enable_cache: bool = True,
) -> Any:
    """
    解析并创建运行时模型实例。

    性能优化：使用实例池复用已创建的模型对象，避免重复初始化 SDK 客户端。
    """
    normalized_model_name = str(model_name or "").strip()

    # 循环引用检测（移除 composite 后不再需要，但保留以备未来使用）
    if normalized_model_name in _resolution_stack:
        chain = " -> ".join((*_resolution_stack, normalized_model_name))
        raise ValueError(f"模型存在循环引用: {chain}")

    config = load_runtime_model_config(
        model_name=normalized_model_name,
        runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir,
        dotenv_path=dotenv_path,
        runtime_overrides=runtime_overrides,
    )
    normalized_effort = validate_runtime_model_effort(
        model_name=config.model_name,
        model_effort=model_effort,
        runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir,
        runtime_overrides=runtime_overrides,
    )

    # Resolve files and credentials before consulting the pool. A cached client
    # must never hide a changed/deleted config or reuse another project's key.
    # RuntimeModelConfig masks api_key in repr; cache diagnostics log names only.
    cache_key = (config, normalized_effort, str(Path(workspace_dir or Path.cwd()).resolve()))
    if _enable_cache:
        with _pool_lock:
            cached = _MODEL_INSTANCE_POOL.get(cache_key)
            if cached is not None:
                logger.debug("模型实例缓存命中: %s", normalized_model_name)
                return cached

    logger.info(
        "解析运行时模型: model_name=%s backend=%s provider_model_name=%s",
        config.model_name,
        config.backend,
        config.provider_model_name,
    )
    # 模型类型保持统一；仅在此将目录中的 effort 转为各 provider 的原生配置。
    kwargs = {
        "backend": config.backend,
        "api_base": config.api_base,
        "api_key": config.api_key,
        "model_name": config.provider_model_name,
    }
    if config.backend == "anthropic":
        kwargs["thinking"] = build_anthropic_thinking_config(normalized_effort)
        kwargs["effort_config"] = build_anthropic_effort_config(normalized_effort)
    else:
        kwargs["thinking"] = build_thinking_override(normalized_effort)
    model = ChatModel(**kwargs)

    # 加入缓存
    if _enable_cache:
        with _pool_lock:
            _MODEL_INSTANCE_POOL[cache_key] = model
            logger.debug("模型实例已缓存: %s", normalized_model_name)

    return model


def resolve_evaluator_model(
    *,
    runtime_config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    dotenv_path: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> Any:
    """Resolve an independent lightweight model for goal evaluation."""

    evaluator_config = get_evaluator_config(
        runtime_config_path, workspace_dir=workspace_dir, overrides=runtime_overrides,
    )
    return resolve_runtime_model(
        model_name=evaluator_config["model"],
        runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir,
        dotenv_path=dotenv_path,
        model_effort=evaluator_config["model_effort"],
        runtime_overrides=runtime_overrides,
    )


__all__ = [
    "DEFAULT_DOTENV_PATH",
    "DEFAULT_RUNTIME_CONFIG_PATH",
    "DEFAULT_RUNTIME_MODEL_NAME",
    "RuntimeModelConfig",
    "RuntimeModelEffort",
    "RuntimeThinkingMode",
    "build_anthropic_effort_config",
    "build_anthropic_thinking_config",
    "build_thinking_override",
    "infer_supported_efforts",
    "list_runtime_models",
    "load_runtime_model_config",
    "normalize_runtime_model_effort",
    "resolve_supported_efforts",
    "validate_runtime_model_effort",
    "resolve_evaluator_model",
    "resolve_runtime_model",
]
