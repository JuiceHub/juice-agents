"""
Registry store 共享工具：YAML 读写、名称校验、模型解析。

统一 YAML 配置持久化的读写与路径解析，减少registry 持久化层的重复实现。
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - 运行时依赖缺失时抛错
    yaml = None

logger = logging.getLogger(__name__)

_CONFIG_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def require_yaml() -> None:
    if yaml is None:
        raise ImportError("未安装 pyyaml，请先执行: pip install pyyaml")


def normalize_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("config_name 必须为非空字符串")
    normalized = name.strip()
    if not _CONFIG_NAME_RE.fullmatch(normalized):
        raise ValueError("config_name 仅允许字母、数字、点、下划线、中划线")
    return normalized


def as_config_path(
    config_name: str,
    config_dir: str | Path,
) -> Path:
    normalized = normalize_name(config_name)
    return Path(config_dir) / f"{normalized}.yaml"


def read_yaml_config(path: Path) -> dict[str, Any]:
    require_yaml()
    if not path.exists():
        raise FileNotFoundError(f"配置不存在: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"配置必须为 YAML object: {path}")
    return dict(raw)


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """将文本原子写入目标文件。

    临时文件必须创建在目标目录内，才能保证 ``os.replace`` 不跨文件系统，
    从而维持原子替换语义。写入成功后先刷新 Python 缓冲区并同步文件描述符，
    再替换旧文件；任一步骤失败都会清理尚未替换的临时文件，并保留原文件。
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)

        # fdopen 接管文件描述符的生命周期；即使 write/flush/fsync 失败，
        # with 退出时也会关闭描述符，finally 再负责删除临时文件。
        with os.fdopen(file_descriptor, "w", encoding=encoding) as stream:
            file_descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                logger.debug("原子写入临时文件描述符已关闭: %s", target)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "清理原子写入临时文件失败: %s",
                    temporary_path,
                    exc_info=True,
                )


def write_yaml_config(path: Path, payload: dict[str, Any]) -> None:
    require_yaml()
    # 在落盘前完成 YAML 序列化；序列化失败时不会创建目录或临时文件，
    # 更不会影响目标位置上最后一次成功写入的配置。
    serialized = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    atomic_write_text(
        path,
        serialized,
        encoding="utf-8",
    )


def list_config_names(config_dir: str | Path) -> list[str]:
    base = Path(config_dir)
    if not base.exists():
        return []
    names: list[str] = []
    for path in sorted(base.glob("*.yaml")):
        names.append(path.stem)
    return names


def resolve_runtime_model_from_catalog(
    *,
    runtime_config_path: str | Path,
    model_name: str,
    model_effort: str | None = None,
) -> Any:
    """
    从 YAML 模型目录解析并实例化运行时模型。
    """
    from juice_agents.core.config.model_catalog import resolve_runtime_model

    return resolve_runtime_model(
        model_name=model_name,
        runtime_config_path=runtime_config_path,
        model_effort=model_effort,
    )


__all__ = [
    "normalize_name",
    "as_config_path",
    "read_yaml_config",
    "atomic_write_text",
    "write_yaml_config",
    "list_config_names",
    "resolve_runtime_model_from_catalog",
]
