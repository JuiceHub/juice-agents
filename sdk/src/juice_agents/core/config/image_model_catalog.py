"""Independent image model selection using the shared provider connections."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .provider_catalog import (
    load_model_catalog,
    redact_provider_api_base,
    resolve_catalog_entry,
    resolve_provider_credentials,
)

DEFAULT_RUNTIME_IMAGE_MODEL_NAME = "seedream_4"


@dataclass(frozen=True)
class RuntimeImageModelConfig:
    model_name: str
    provider: str
    backend: str
    api_base: str = field(repr=False)
    api_key_env: str
    provider_model_name: str
    api_key: str = field(default="", repr=False)


def load_runtime_image_model_config(
    *,
    model_name: str | None = None,
    runtime_config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    dotenv_path: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> RuntimeImageModelConfig:
    """Resolve ``image.model_name → image_models → providers``.

    ``model_name`` is optional so tool builders can follow the active image
    selection. An explicit name lets selectors validate a proposed change
    before persisting it; neither path depends on the current chat model.
    """

    payload, _ = load_model_catalog(
        "image_models", runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir, runtime_overrides=runtime_overrides,
    )
    if model_name is None:
        image = payload.get("image")
        model_name = image.get("model_name") if isinstance(image, dict) else None
    normalized_name = str(model_name or "").strip()
    if not normalized_name:
        raise ValueError("image.model_name 不能为空，请在 image_models 中配置并选择生图模型")
    entry = resolve_catalog_entry(payload, "image_models", normalized_name, allowed_backends={"doubao"})
    api_base, api_key = resolve_provider_credentials(
        entry, dotenv_path=dotenv_path, workspace_dir=workspace_dir,
    )
    return RuntimeImageModelConfig(
        model_name=normalized_name,
        provider=entry["provider"],
        backend=entry["backend"],
        api_base=api_base,
        api_key_env=entry["api_key_env"],
        provider_model_name=entry["provider_model_name"],
        api_key=api_key,
    )


def list_runtime_image_models(
    *,
    runtime_config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return sorted image model metadata without requiring or exposing keys."""

    payload, catalog = load_model_catalog(
        "image_models", runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir, runtime_overrides=runtime_overrides,
    )
    items = []
    for model_name in sorted(catalog):
        entry = resolve_catalog_entry(payload, "image_models", model_name, allowed_backends={"doubao"})
        item = {field: entry[field] for field in (
            "model_name", "provider", "backend", "api_base", "api_key_env", "provider_model_name",
        )}
        item["api_base"] = redact_provider_api_base(entry["api_base"])
        items.append(item)
    return items


__all__ = [
    "DEFAULT_RUNTIME_IMAGE_MODEL_NAME",
    "RuntimeImageModelConfig",
    "list_runtime_image_models",
    "load_runtime_image_model_config",
]
