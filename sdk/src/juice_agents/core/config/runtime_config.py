"""
统一的运行时 YAML 配置读取。

该模块负责把 runner / TUI / 工具层共用的非敏感默认配置作为 SDK
只读资源加载，依次叠加用户级和 workspace 配置；凭据使用相同的
workspace 上下文读取，不修改进程环境，避免多个项目互相污染。
"""

from __future__ import annotations

import os
import tempfile
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable

from juice_agents.core.utils import load_env_file

from .config_cache import cached_config_loader

try:
    import yaml
except Exception:  # pragma: no cover - 运行时缺依赖时给出清晰错误
    yaml = None

# The default model/tool catalog is immutable SDK data.  Wheels are installed
# outside the user's workspace, so no runtime state may be inferred from the
# package's filesystem parents or written next to this module.
DEFAULT_RUNTIME_CONFIG_PATH = Path(
    str(files("juice_agents.core.config").joinpath("defaults.yaml"))
).resolve()
DEFAULT_DOTENV_PATH = Path.cwd().resolve() / ".env"
WORKSPACE_CONFIG_RELATIVE_PATH = Path(".juice") / "config.yaml"


def user_juice_root() -> Path:
    """Resolve HOME when called, so independent homes never share cached config."""

    return Path.home().expanduser().resolve() / ".juice"


def user_config_path() -> Path:
    return user_juice_root() / "config.yaml"


def user_dotenv_path() -> Path:
    return user_juice_root() / ".env"


def credential_paths(
    *, workspace_dir: str | Path | None = None, dotenv_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Return credentials in ascending precedence; an explicit .env replaces project .env."""

    project_env = (
        Path(dotenv_path).expanduser().resolve()
        if dotenv_path is not None
        else Path(workspace_dir or Path.cwd()).expanduser().resolve() / ".env"
    )
    return user_dotenv_path(), project_env


def read_credential_values(
    *, workspace_dir: str | Path | None = None, dotenv_path: str | Path | None = None,
) -> dict[str, str]:
    """Read file credentials without exporting them into ``os.environ``.

    Read on demand rather than cache secrets: edits, replacement, and deletion
    of either .env file must take effect on the next model/tool resolution.
    Process environment precedence is applied by :func:`resolve_env_value`.
    """

    values: dict[str, str] = {}
    for path in credential_paths(workspace_dir=workspace_dir, dotenv_path=dotenv_path):
        values.update(load_env_file(path))
    return values


def resolve_runtime_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve the explicit base file, or the SDK's immutable defaults.

    Missing files are reported by the loader; an explicit path must never
    silently fall back to a different configuration.
    """

    return (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else DEFAULT_RUNTIME_CONFIG_PATH.resolve()
    )


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
            merged[key] = deepcopy(value)
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


def runtime_config_paths(
    config_path: str | Path | None = None, *, workspace_dir: str | Path | None = None,
) -> list[Path]:
    """Single source list used by both loading and cache invalidation."""

    paths = [resolve_runtime_config_path(config_path), user_config_path()]
    if workspace_dir is not None:
        paths.append(workspace_config_path(workspace_dir))
    return paths


@cached_config_loader(
    cache_key_fn=lambda config_path=None, workspace_dir=None: repr(
        runtime_config_paths(config_path, workspace_dir=workspace_dir)
    ),
    file_paths_fn=runtime_config_paths,
)
def _read_layered_config(
    config_path: str | Path | None = None,
    *,
    workspace_dir: str | Path | None = None,
) -> dict[str, Any]:
    resolved_path, *overrides = runtime_config_paths(config_path, workspace_dir=workspace_dir)
    if not resolved_path.exists():
        raise FileNotFoundError(f"运行时配置文件不存在: {resolved_path}")
    payload = _read_yaml_object(resolved_path)
    for path in overrides:
        if path.exists():
            payload = _deep_merge(payload, _read_yaml_object(path))
    return payload


def read_runtime_config(
    config_path: str | Path | None = None,
    *,
    workspace_dir: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read base → user → project → transient overrides.

    Only file layers are cached. Startup options stay in memory and never get
    written into a project's YAML simply because a runner was created.
    """

    payload = _read_layered_config(config_path, workspace_dir=workspace_dir)
    return _deep_merge(payload, overrides) if overrides else payload


def get_runtime_mapping(
    mapping_name: str,
    *,
    config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = read_runtime_config(config_path, workspace_dir=workspace_dir, overrides=overrides)
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
    # An explicitly empty process variable also overrides file values; this
    # allows callers to disable a credential without deleting a local file.
    value = os.environ.get(normalized_name, dotenv_values.get(normalized_name, ""))
    return str(value or "").strip()


_DEFAULT_EVALUATOR_CONFIG: dict[str, str] = {
    "model": "doubao_lite",
    "model_effort": "disabled",
}


def get_evaluator_config(
    config_path: str | Path | None = None,
    *,
    workspace_dir: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Read the evaluator section from runtime config, with sensible defaults."""

    try:
        payload = read_runtime_config(config_path, workspace_dir=workspace_dir, overrides=overrides)
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
    "DEFAULT_RUNTIME_CONFIG_PATH",
    "WORKSPACE_CONFIG_RELATIVE_PATH",
    "get_evaluator_config",
    "get_runtime_mapping",
    "credential_paths",
    "normalize_runtime_key",
    "read_runtime_config",
    "read_credential_values",
    "read_workspace_config",
    "resolve_env_value",
    "resolve_runtime_config_path",
    "runtime_config_paths",
    "update_workspace_config",
    "workspace_config_path",
    "write_workspace_config",
    "user_config_path",
    "user_dotenv_path",
    "user_juice_root",
]
