"""
统一运行时配置中的模型目录加载与模型解析。

该模块服务于 runner / prebuilt / TUI：
- 从 `config/config.yaml` 的 `models` 顶层读取逻辑模型目录；
- 从 `.env` / 环境变量解析密钥；
- 根据 `backend=openai|doubao|anthropic|composite` 选择具体模型实现。

性能优化：
- 模型实例复用：使用 WeakValueDictionary 缓存已创建的模型实例
- SDK 客户端复用：避免重复初始化 OpenAI/Anthropic 等客户端
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from weakref import WeakValueDictionary

from juice_agents.core.models import AnthropicModel, CompositeModel, DoubaoModel, OpenAIModel
from juice_agents.core.utils import load_env_file

from .runtime_config import (
    DEFAULT_DOTENV_PATH,
    DEFAULT_RUNTIME_CONFIG_EXAMPLE_PATH,
    DEFAULT_RUNTIME_CONFIG_PATH,
    get_evaluator_config,
    get_runtime_mapping,
    resolve_runtime_config_path,
)

logger = logging.getLogger(__name__)

# 模型实例复用池：使用 WeakValueDictionary 自动回收未使用的实例
_MODEL_INSTANCE_POOL: WeakValueDictionary = WeakValueDictionary()
_pool_lock = __import__("threading").Lock()

DEFAULT_RUNTIME_MODEL_NAME = "doubao_lite"
RuntimeBackend = Literal["openai", "doubao", "anthropic", "composite"]
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
    api_key: str = ""


def _load_catalog_payload(
    runtime_config_path: str | Path | None,
    *,
    workspace_dir: str | Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    resolved_path = resolve_runtime_config_path(runtime_config_path)
    if not resolved_path.exists():
        raise FileNotFoundError(f"模型目录文件不存在: {resolved_path}")
    return resolved_path, get_runtime_mapping(
        "models",
        config_path=resolved_path,
        workspace_dir=workspace_dir,
    )


def _require_model_entry_fields(model_name: str, raw: dict[str, Any], required: list[str]) -> None:
    for field in required:
        if not str(raw.get(field) or "").strip():
            raise ValueError(f"模型 {model_name} 缺少必填字段: {field}")


def _normalize_backend(model_name: str, raw: dict[str, Any]) -> RuntimeBackend:
    backend = str(raw.get("backend") or "").strip().lower()
    if backend not in {"openai", "doubao", "anthropic", "composite"}:
        raise ValueError(f"模型 {model_name} 的 backend 不合法: {backend}")
    return backend  # type: ignore[return-value]


def _resolve_api_key(*, api_key_env: str, dotenv_path: str | Path | None) -> str:
    env_values = load_env_file(dotenv_path or DEFAULT_DOTENV_PATH)
    value = str(os.environ.get(api_key_env) or env_values.get(api_key_env) or "").strip()
    if not value:
        raise ValueError(f"模型密钥环境变量未配置: {api_key_env}")
    return value


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
    if "claude-opus-4-8" in model or "claude-opus-4-7" in model:
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
    if normalized_backend == "composite":
        values = RUNTIME_MODEL_EFFORTS_ORDER
    elif normalized_backend == "anthropic":
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
    return "claude-opus-4-7" not in model


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
) -> RuntimeModelEffort:
    normalized = normalize_runtime_model_effort(model_effort)
    _, models = _load_catalog_payload(runtime_config_path, workspace_dir=workspace_dir)
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
) -> RuntimeModelConfig:
    normalized_model_name = str(model_name or "").strip()
    if not normalized_model_name:
        raise ValueError("model_name 不能为空")

    _, models = _load_catalog_payload(runtime_config_path, workspace_dir=workspace_dir)
    raw_model = models.get(normalized_model_name)
    if not isinstance(raw_model, dict):
        raise ValueError(f"模型目录中不存在模型: {normalized_model_name}")

    _require_model_entry_fields(normalized_model_name, raw_model, ["backend"])
    backend = _normalize_backend(normalized_model_name, raw_model)
    if backend == "composite":
        return RuntimeModelConfig(model_name=normalized_model_name, backend=backend)

    _require_model_entry_fields(
        normalized_model_name,
        raw_model,
        ["api_base", "api_key_env", "provider_model_name"],
    )

    api_key_env = str(raw_model.get("api_key_env") or "").strip()
    provider_model_name = str(raw_model.get("provider_model_name") or "").strip()
    api_base = str(raw_model.get("api_base") or "").strip()
    api_key = _resolve_api_key(api_key_env=api_key_env, dotenv_path=dotenv_path)

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
) -> list[dict[str, Any]]:
    _, models = _load_catalog_payload(runtime_config_path, workspace_dir=workspace_dir)
    items: list[dict[str, Any]] = []
    for model_name in sorted(models.keys()):
        raw = dict(models.get(model_name) or {})
        _require_model_entry_fields(model_name, raw, ["backend"])
        backend = _normalize_backend(model_name, raw)
        if backend != "composite":
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
        if backend == "composite":
            item["strategy"] = str(raw.get("strategy") or "").strip().lower()
        items.append(item)
    return items


def _normalize_model_ref(raw: Any, *, field_name: str) -> dict[str, str]:
    """把 composite 子模型引用收敛为统一结构。"""

    if isinstance(raw, str):
        model_name = raw.strip()
        if not model_name:
            raise ValueError(f"{field_name} 不能为空")
        return {"model": model_name, "name": model_name, "model_effort": ""}
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} 必须为字符串或 object")
    model_name = str(raw.get("model") or "").strip()
    if not model_name:
        raise ValueError(f"{field_name}.model 不能为空")
    return {
        "model": model_name,
        "name": str(raw.get("name") or model_name).strip(),
        "model_effort": str(raw.get("model_effort") or "").strip(),
    }


def _resolve_child_model(
    *,
    raw_ref: Any,
    field_name: str,
    inherited_effort: str | None,
    runtime_config_path: str | Path | None,
    workspace_dir: str | Path | None,
    dotenv_path: str | Path | None,
    resolution_stack: tuple[str, ...],
) -> dict[str, Any]:
    ref = _normalize_model_ref(raw_ref, field_name=field_name)
    child_effort = ref["model_effort"] or inherited_effort
    model = resolve_runtime_model(
        model_name=ref["model"],
        runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir,
        dotenv_path=dotenv_path,
        model_effort=child_effort,
        _resolution_stack=resolution_stack,
    )
    return {"name": ref["name"], "model": model}


def _resolve_child_model_object(
    *,
    raw_ref: Any,
    field_name: str,
    inherited_effort: str | None,
    runtime_config_path: str | Path | None,
    workspace_dir: str | Path | None,
    dotenv_path: str | Path | None,
    resolution_stack: tuple[str, ...],
) -> Any:
    return _resolve_child_model(
        raw_ref=raw_ref,
        field_name=field_name,
        inherited_effort=inherited_effort,
        runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir,
        dotenv_path=dotenv_path,
        resolution_stack=resolution_stack,
    )["model"]


def _resolve_composite_model(
    *,
    model_name: str,
    raw: dict[str, Any],
    runtime_config_path: str | Path | None,
    workspace_dir: str | Path | None,
    dotenv_path: str | Path | None,
    model_effort: str | None,
    resolution_stack: tuple[str, ...],
) -> CompositeModel:
    strategy = str(raw.get("strategy") or "").strip().lower()
    if not strategy:
        raise ValueError(f"Composite 模型 {model_name} 缺少 strategy")
    fail_fast = bool(raw.get("fail_fast", False))
    max_refine_rounds = int(raw.get("max_refine_rounds", 2))
    child_stack = (*resolution_stack, model_name)

    if strategy in {"judge_select", "synthesize"}:
        raw_candidates = raw.get("candidates")
        if not isinstance(raw_candidates, list) or not raw_candidates:
            raise ValueError(f"Composite 模型 {model_name} 必须配置非空 candidates")
        candidates = [
            _resolve_child_model(
                raw_ref=item,
                field_name=f"{model_name}.candidates[{idx}]",
                inherited_effort=model_effort,
                runtime_config_path=runtime_config_path,
                workspace_dir=workspace_dir,
                dotenv_path=dotenv_path,
                resolution_stack=child_stack,
            )
            for idx, item in enumerate(raw_candidates)
        ]
        if strategy == "judge_select":
            judge_model = _resolve_child_model_object(
                raw_ref=raw.get("judge"),
                field_name=f"{model_name}.judge",
                inherited_effort=model_effort,
                runtime_config_path=runtime_config_path,
                workspace_dir=workspace_dir,
                dotenv_path=dotenv_path,
                resolution_stack=child_stack,
            )
            return CompositeModel(
                model_name=model_name,
                strategy=strategy,
                candidates=candidates,
                judge_model=judge_model,
                fail_fast=fail_fast,
                max_refine_rounds=max_refine_rounds,
            )
        synthesis_ref = raw.get("synthesis") or raw.get("synthesizer") or raw.get("final_model")
        synthesis_model = _resolve_child_model_object(
            raw_ref=synthesis_ref,
            field_name=f"{model_name}.synthesis",
            inherited_effort=model_effort,
            runtime_config_path=runtime_config_path,
            workspace_dir=workspace_dir,
            dotenv_path=dotenv_path,
            resolution_stack=child_stack,
        )
        return CompositeModel(
            model_name=model_name,
            strategy=strategy,
            candidates=candidates,
            synthesis_model=synthesis_model,
            fail_fast=fail_fast,
            max_refine_rounds=max_refine_rounds,
        )

    if strategy == "iterative_refine":
        raw_reviewers = raw.get("reviewers")
        if not isinstance(raw_reviewers, list) or not raw_reviewers:
            raise ValueError(f"Composite 模型 {model_name} 必须配置非空 reviewers")
        generator_model = _resolve_child_model_object(
            raw_ref=raw.get("generator"),
            field_name=f"{model_name}.generator",
            inherited_effort=model_effort,
            runtime_config_path=runtime_config_path,
            workspace_dir=workspace_dir,
            dotenv_path=dotenv_path,
            resolution_stack=child_stack,
        )
        reviewers = [
            _resolve_child_model(
                raw_ref=item,
                field_name=f"{model_name}.reviewers[{idx}]",
                inherited_effort=model_effort,
                runtime_config_path=runtime_config_path,
                workspace_dir=workspace_dir,
                dotenv_path=dotenv_path,
                resolution_stack=child_stack,
            )
            for idx, item in enumerate(raw_reviewers)
        ]
        return CompositeModel(
            model_name=model_name,
            strategy=strategy,
            generator_model=generator_model,
            reviewer_models=reviewers,
            fail_fast=fail_fast,
            max_refine_rounds=max_refine_rounds,
        )

    raise ValueError(f"Composite 模型 {model_name} 的 strategy 不合法: {strategy}")


def resolve_runtime_model(
    *,
    model_name: str,
    runtime_config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    dotenv_path: str | Path | None = None,
    model_effort: str | None = None,
    _resolution_stack: tuple[str, ...] = (),
    _enable_cache: bool = True,
) -> Any:
    """
    解析并创建运行时模型实例。

    性能优化：使用实例池复用已创建的模型对象，避免重复初始化 SDK 客户端。
    """
    normalized_model_name = str(model_name or "").strip()

    # 生成缓存 key
    cache_key = (
        f"{normalized_model_name}:"
        f"{model_effort or 'default'}:"
        f"{runtime_config_path or 'default'}:"
        f"{workspace_dir or 'default'}"
    )

    # 尝试从缓存获取
    if _enable_cache:
        with _pool_lock:
            if cache_key in _MODEL_INSTANCE_POOL:
                logger.debug(f"模型实例缓存命中: {normalized_model_name}")
                return _MODEL_INSTANCE_POOL[cache_key]

    # 循环引用检测
    if normalized_model_name in _resolution_stack:
        chain = " -> ".join((*_resolution_stack, normalized_model_name))
        raise ValueError(f"Composite 模型存在循环引用: {chain}")

    config = load_runtime_model_config(
        model_name=normalized_model_name,
        runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir,
        dotenv_path=dotenv_path,
    )
    normalized_effort = validate_runtime_model_effort(
        model_name=config.model_name,
        model_effort=model_effort,
        runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir,
    )

    if config.backend == "composite":
        _, models = _load_catalog_payload(runtime_config_path, workspace_dir=workspace_dir)
        raw_model = dict(models.get(config.model_name) or {})
        model = _resolve_composite_model(
            model_name=config.model_name,
            raw=raw_model,
            runtime_config_path=runtime_config_path,
            workspace_dir=workspace_dir,
            dotenv_path=dotenv_path,
            model_effort=normalized_effort,
            resolution_stack=_resolution_stack,
        )
    else:
        logger.info(
            "解析运行时模型: model_name=%s backend=%s provider_model_name=%s",
            config.model_name,
            config.backend,
            config.provider_model_name,
        )
        # Anthropic 使用 output_config.effort + adaptive thinking（官方推荐）
        if config.backend == "anthropic":
            model = AnthropicModel(
                api_base=config.api_base,
                api_key=config.api_key,
                model_name=config.provider_model_name,
                thinking=build_anthropic_thinking_config(normalized_effort),
                effort_config=build_anthropic_effort_config(normalized_effort),
            )
        else:
            # Doubao/OpenAI 继续使用 thinking.effort 方式
            kwargs = {
                "api_base": config.api_base,
                "api_key": config.api_key,
                "model_name": config.provider_model_name,
                "thinking": build_thinking_override(normalized_effort),
            }
            if config.backend == "doubao":
                model = DoubaoModel(**kwargs)
            else:
                model = OpenAIModel(**kwargs)

    # 加入缓存
    if _enable_cache:
        with _pool_lock:
            _MODEL_INSTANCE_POOL[cache_key] = model
            logger.debug(f"模型实例已缓存: {cache_key}")

    return model


def resolve_evaluator_model(
    *,
    runtime_config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    dotenv_path: str | Path | None = None,
) -> Any:
    """Resolve an independent lightweight model for goal evaluation."""

    evaluator_config = get_evaluator_config(runtime_config_path, workspace_dir=workspace_dir)
    return resolve_runtime_model(
        model_name=evaluator_config["model"],
        runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir,
        dotenv_path=dotenv_path,
        model_effort=evaluator_config["model_effort"],
    )


__all__ = [
    "DEFAULT_DOTENV_PATH",
    "DEFAULT_RUNTIME_CONFIG_EXAMPLE_PATH",
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
