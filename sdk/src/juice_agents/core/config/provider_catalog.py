"""Shared provider references for language and image model catalogs.

Catalog inspection validates public metadata only. Credentials are resolved
separately when a consumer actually creates a client, preserving lazy clients
and allowing model selectors to work before a key is configured.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .runtime_config import (
    credential_paths,
    read_credential_values,
    read_runtime_config,
    resolve_env_value,
)

logger = logging.getLogger(__name__)

_CONNECTION_FIELDS = {"backend", "api_base", "api_base_env", "api_key_env", "api_key"}
_BACKENDS = {"openai", "doubao", "anthropic"}


def redact_provider_api_base(api_base: str) -> str:
    """Keep endpoint metadata useful without returning URL credentials.

    Query values and fragments may carry provider-specific secrets, so redact
    all of them rather than maintaining an incomplete list of parameter names.
    The effective URL passed to SDK clients is never modified.
    """

    try:
        parts = urlsplit(api_base)
        netloc = parts.netloc.rsplit("@", 1)[-1]
        query = urlencode([(name, "***") for name, _ in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, netloc, parts.path, query, ""))
    except ValueError:
        # Invalid URLs are unsuitable public metadata and may still embed keys.
        return "<invalid api_base>"


def load_model_catalog(
    catalog_name: str,
    *,
    runtime_config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read the normal configuration layers once for a catalog resolution."""

    payload = read_runtime_config(
        runtime_config_path, workspace_dir=workspace_dir, overrides=runtime_overrides,
    )
    catalog = payload.get(catalog_name)
    if not isinstance(catalog, dict):
        raise ValueError(f"运行时配置缺少合法的 {catalog_name} 顶层字段")
    return payload, catalog


def resolve_catalog_entry(
    payload: dict[str, Any],
    catalog_name: str,
    model_name: str,
    *,
    allowed_backends: set[str] | None = None,
) -> dict[str, Any]:
    """Join a model with its provider, without reading any credentials.

    Connection fields are owned exclusively by providers. Rejecting mixed or
    legacy entries prevents stale inline overrides from silently taking priority
    after users migrate only one layer of their configuration.
    """

    catalog = payload.get(catalog_name)
    raw = catalog.get(model_name) if isinstance(catalog, dict) else None
    if not isinstance(raw, dict):
        raise ValueError(f"{catalog_name} 模型目录中不存在模型: {model_name}")
    if _CONNECTION_FIELDS.intersection(raw):
        raise ValueError(
            f"{catalog_name}.{model_name} 使用旧内联连接配置，请迁移："
            "将 backend、api_base、api_base_env、api_key_env 移至 providers.<名称>，"
            f"在 {catalog_name}.{model_name} 设置 provider: <名称>；"
            "保留 provider_model_name。配置文件不会自动改写。"
        )
    provider_name = str(raw.get("provider") or "").strip()
    if not provider_name:
        raise ValueError(f"模型 {model_name} 缺少必填字段: provider")
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        raise ValueError("运行时配置缺少合法的 providers 顶层字段")
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        raise ValueError(f"模型 {model_name} 引用的 Provider 不存在: {provider_name}")
    if "api_key" in provider:
        raise ValueError(f"Provider {provider_name} 请使用 api_key_env 引用凭据，不支持内联 api_key")
    for field in ("backend", "api_base", "api_key_env"):
        if not str(provider.get(field) or "").strip():
            raise ValueError(f"Provider {provider_name} 缺少必填字段: {field}")
    if not str(raw.get("provider_model_name") or "").strip():
        raise ValueError(f"模型 {model_name} 缺少必填字段: provider_model_name")
    backend = str(provider["backend"]).strip().lower()
    if backend not in _BACKENDS:
        raise ValueError(f"Provider {provider_name} 的 backend 不合法（支持 openai、doubao、anthropic）")
    if allowed_backends is not None and backend not in allowed_backends:
        raise ValueError(f"{catalog_name} 模型 {model_name} 暂不支持 backend={backend}")
    # Keep model-specific capabilities while resolving all connection properties
    # through a single provider. Neither return value contains an actual key.
    return {
        **raw,
        "model_name": model_name,
        "provider": provider_name,
        "backend": backend,
        "api_base": str(provider["api_base"]).strip(),
        "api_base_env": str(provider.get("api_base_env") or "").strip(),
        "api_key_env": str(provider["api_key_env"]).strip(),
        "provider_model_name": str(raw["provider_model_name"]).strip(),
    }


def resolve_provider_credentials(
    entry: dict[str, Any],
    *,
    dotenv_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
) -> tuple[str, str]:
    """Return effective base and key using the existing environment precedence."""

    values = read_credential_values(workspace_dir=workspace_dir, dotenv_path=dotenv_path)
    api_key_env = entry["api_key_env"]
    api_key = resolve_env_value(api_key_env, dotenv_values=values)
    if not api_key:
        locations = ", ".join(str(path) for path in credential_paths(
            workspace_dir=workspace_dir, dotenv_path=dotenv_path,
        ))
        raise ValueError(f"模型密钥环境变量未配置: {api_key_env}；请设置进程环境变量，或写入 {locations}")
    api_base = entry["api_base"]
    api_base_env = entry["api_base_env"]
    if api_base_env:
        override = resolve_env_value(api_base_env, dotenv_values=values)
        if override:
            # URLs can embed credentials. Log the variable name, never its value.
            logger.info("api_base 由环境变量 %s 覆盖", api_base_env)
            api_base = override
    return api_base, api_key
