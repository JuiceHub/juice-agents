"""ConfigStore 基类：封装通用 YAML 配置 CRUD 逻辑。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from juice_agents.core.registry.common.store import (
    as_config_path,
    list_config_names,
    normalize_name,
    read_yaml_config,
    write_yaml_config,
)

logger = logging.getLogger(__name__)


class _BaseConfigStore:
    """
    ConfigStore 通用基类。

    配置目录必须由调用方显式传入。Registry 属于 workspace 级基础设施，
    让 Store 自行猜测当前目录会绕过 ``ConfigurationContext`` 的领域隔离。
    """

    def __init__(self, config_dir: str | Path) -> None:
        self._config_dir = Path(config_dir)

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    def _save(self, name: str, data: dict[str, Any]) -> Path:
        """将配置数据持久化为 YAML 文件。"""
        normalized = normalize_name(name)
        path = as_config_path(normalized, self._config_dir)
        write_yaml_config(path, data)
        logger.debug("配置已保存: %s -> %s", normalized, path)
        return path

    def _load(self, name: str) -> dict[str, Any]:
        """从 YAML 文件加载配置数据。"""
        normalized = normalize_name(name)
        path = as_config_path(normalized, self._config_dir)
        return read_yaml_config(path)

    def _list(self) -> list[str]:
        """列出当前目录下所有已保存的配置名。"""
        return list_config_names(self._config_dir)

    def _delete(self, name: str) -> None:
        """删除指定配置文件。"""
        normalized = normalize_name(name)
        path = as_config_path(normalized, self._config_dir)
        if not path.exists():
            raise FileNotFoundError(f"配置不存在: {path}")
        path.unlink()
        logger.debug("配置已删除: %s -> %s", normalized, path)


__all__ = ["_BaseConfigStore"]
