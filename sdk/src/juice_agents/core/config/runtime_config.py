"""
统一的运行时 YAML 配置读取。

该模块负责把 runner / TUI / 工具层共用的非敏感默认配置作为 SDK
只读资源加载，并叠加 workspace 的 `.juice/config.yaml`。

性能优化：使用配置缓存减少重复文件 IO 和 YAML 解析
"""

from __future__ import annotations

import os
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable

try:
    import yaml
except Exception:  # pragma: no cover - 运行时缺依赖时给出清晰错误
    yaml = None

# 导入配置缓存模块
try:
    from .config_cache import cached_config_loader
    _CACHE_AVAILABLE = True
except ImportError:  # pragma: no cover - 优雅降级
    _CACHE_AVAILABLE = False
    # 定义空装饰器作为回退
    def cached_config_loader(*args, **kwargs):
        def decorator(func):
            return func
        return decorator


# The default model/tool catalog is immutable SDK data.  Wheels are installed
# outside the user's workspace, so no runtime state may be inferred from the
# package's filesystem parents or written next to this module.
_ASSETS_ROOT = files("juice_agents._assets")
DEFAULT_RUNTIME_CONFIG_EXAMPLE_PATH = Path(
    str(_ASSETS_ROOT.joinpath("config.example.yaml"))
).resolve()
DEFAULT_RUNTIME_CONFIG_PATH = DEFAULT_RUNTIME_CONFIG_EXAMPLE_PATH
DEFAULT_DOTENV_PATH = Path.cwd().resolve() / ".env"
WORKSPACE_CONFIG_RELATIVE_PATH = Path(".juice") / "config.yaml"


def resolve_runtime_config_path(config_path: str | Path | None = None) -> Path:
    configured = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else DEFAULT_RUNTIME_CONFIG_PATH.resolve()
    )
    if configured.exists():
        return configured
    if configured == DEFAULT_RUNTIME_CONFIG_PATH.resolve() and DEFAULT_RUNTIME_CONFIG_EXAMPLE_PATH.exists():
        return DEFAULT_RUNTIME_CONFIG_EXAMPLE_PATH.resolve()
    return configured


def workspace_config_path(workspace_dir: str | Path) -> Path:
    """Return the workspace-local YAML override path."""

    return Path(workspace_dir).expanduser().resolve() / WORKSPACE_CONFIG_RELATIVE_PATH


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _read_yaml_object(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise ImportError("未安装 pyyaml，请先执行: pip install pyyaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"运行时配置必须为 YAML object: {path}")
    return dict(raw)


def read_workspace_config(workspace_dir: str | Path) -> dict[str, Any]:
    """Read workspace-local runtime overrides, returning empty config if absent."""

    path = workspace_config_path(workspace_dir)
    if not path.exists():
        return {}
    return _read_yaml_object(path)


def write_workspace_config(workspace_dir: str | Path, payload: dict[str, Any]) -> Path:
    """Atomically persist workspace-local runtime overrides as YAML.

    A crash or failed replace must never leave a partially written
    ``.juice/config.yaml`` behind: write and fsync a sibling temporary file,
    then replace the old file in one filesystem operation.
    """

    if yaml is None:
        raise ImportError("未安装 pyyaml，请先执行: pip install pyyaml")
    path = workspace_config_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(dict(payload), allow_unicode=True, sort_keys=False)
    fd, raw_temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(raw_temp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return path


def update_workspace_config(
    workspace_dir: str | Path,
    updater: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any]:
    """Load, mutate, and write the workspace-local runtime override YAML."""

    current = read_workspace_config(workspace_dir)
    updated = updater(dict(current))
    payload = current if updated is None else dict(updated)
    write_workspace_config(workspace_dir, payload)
    return payload


@cached_config_loader(
    cache_key_fn=lambda config_path=None, workspace_dir=None: f"{resolve_runtime_config_path(config_path)}:{workspace_dir or ''}",
    file_paths_fn=lambda config_path=None, workspace_dir=None: [
        resolve_runtime_config_path(config_path),
        workspace_config_path(workspace_dir) if workspace_dir else Path("/dev/null"),
    ],
    enable_file_watch=False,  # 使用 mtime 检查即可，避免增加依赖
)
def read_runtime_config(
    config_path: str | Path | None = None,
    *,
    workspace_dir: str | Path | None = None,
) -> dict[str, Any]:
    resolved_path = resolve_runtime_config_path(config_path)
    if not resolved_path.exists():
        raise FileNotFoundError(f"运行时配置文件不存在: {resolved_path}")
    payload = _read_yaml_object(resolved_path)
    if workspace_dir is None:
        return payload
    return _deep_merge(payload, read_workspace_config(workspace_dir))


def get_runtime_mapping(
    mapping_name: str,
    *,
    config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
) -> dict[str, Any]:
    payload = read_runtime_config(config_path, workspace_dir=workspace_dir)
    raw_mapping = payload.get(mapping_name)
    if not isinstance(raw_mapping, dict):
        resolved_path = resolve_runtime_config_path(config_path)
        raise ValueError(f"运行时配置缺少合法的 {mapping_name} 顶层字段: {resolved_path}")
    return dict(raw_mapping)


def normalize_runtime_key(name: str) -> str:
    normalized = str(name or "").strip()
    if normalized.startswith("tools."):
        normalized = normalized.split(".", 1)[1]
    return normalized.strip().lower()


def resolve_env_value(
    env_name: str,
    *,
    dotenv_values: dict[str, str] | None = None,
) -> str:
    normalized_name = str(env_name or "").strip()
    if not normalized_name:
        return ""
    dotenv_values = dotenv_values or {}
    return str(os.environ.get(normalized_name) or dotenv_values.get(normalized_name) or "").strip()


_DEFAULT_EVALUATOR_CONFIG: dict[str, str] = {
    "model": "doubao_lite",
    "model_effort": "disabled",
}


def get_evaluator_config(
    config_path: str | Path | None = None,
    *,
    workspace_dir: str | Path | None = None,
) -> dict[str, str]:
    """Read the evaluator section from runtime config, with sensible defaults."""

    try:
        payload = read_runtime_config(config_path, workspace_dir=workspace_dir)
    except (FileNotFoundError, ValueError):
        return dict(_DEFAULT_EVALUATOR_CONFIG)
    raw = payload.get("evaluator")
    if not isinstance(raw, dict):
        return dict(_DEFAULT_EVALUATOR_CONFIG)
    return {
        "model": str(raw.get("model") or _DEFAULT_EVALUATOR_CONFIG["model"]).strip(),
        "model_effort": str(raw.get("model_effort") or _DEFAULT_EVALUATOR_CONFIG["model_effort"]).strip(),
    }


__all__ = [
    "DEFAULT_DOTENV_PATH",
    "DEFAULT_RUNTIME_CONFIG_EXAMPLE_PATH",
    "DEFAULT_RUNTIME_CONFIG_PATH",
    "WORKSPACE_CONFIG_RELATIVE_PATH",
    "get_evaluator_config",
    "get_runtime_mapping",
    "normalize_runtime_key",
    "read_runtime_config",
    "read_workspace_config",
    "resolve_env_value",
    "resolve_runtime_config_path",
    "update_workspace_config",
    "workspace_config_path",
    "write_workspace_config",
]
