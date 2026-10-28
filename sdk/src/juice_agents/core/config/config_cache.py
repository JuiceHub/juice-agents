"""
配置文件智能缓存模块（参考 Claude Code 实现）

核心特性：
- 基于每个来源文件 stat 指纹的自动失效机制（包含新增、删除与原子替换）
- 支持 watchdog 文件监听（可选依赖）
- 重入保护防止递归加载
- 内存缓存配合逐来源文件指纹检查

使用方式：
    @cached_config_loader(
        cache_key_fn=lambda config_path, workspace_dir: f"{config_path}:{workspace_dir}",
        file_paths_fn=lambda config_path, workspace_dir: [Path(config_path)],
    )
    def read_runtime_config(config_path, workspace_dir=None):
        # 原有加载逻辑
        ...
"""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from functools import wraps
from pathlib import Path
from threading import Lock, local
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

# 重入保护：防止 config → logger → config 死循环
_loading = local()

# 每个来源各自保留指纹。取所有文件 mtime 的最大值会漏掉较旧文件的
# 修改，以及删除/带旧时间戳的原子替换，不能表达分层配置是否发生变化。
FileFingerprint = tuple[str, tuple[int, ...] | None]
_config_cache: dict[str, tuple[tuple[FileFingerprint, ...], dict[str, Any]]] = {}
_cache_lock = Lock()

# 可选的文件监听（需要 watchdog 库）
_file_watchers: dict[str, Any] = {}
_watchdog_available = False

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler as _FileSystemEventHandler

    _watchdog_available = True

    class ConfigFileChangeHandler(_FileSystemEventHandler):
        """配置文件变化监听器"""

        def __init__(self, cache_key: str):
            self.cache_key = cache_key
            super().__init__()

        def on_any_event(self, event):
            if not event.is_directory and event.event_type in {"modified", "created", "deleted", "moved"}:
                logger.debug(f"配置文件已变化，失效缓存: {event.src_path}")
                invalidate_cache(self.cache_key)

    logger.debug("watchdog 已加载，启用文件监听模式")
except ImportError:
    Observer = None  # type: ignore
    ConfigFileChangeHandler = None  # type: ignore
    logger.debug("watchdog 未安装，配置缓存将使用文件指纹检查模式")


def _file_fingerprints(file_paths: list[Path]) -> tuple[FileFingerprint, ...]:
    """Keep absence as a real state; inode/ctime detect atomic replacement."""
    return tuple(_file_fingerprint(path) for path in file_paths)


def _file_fingerprint(path: Path) -> FileFingerprint:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return str(path), None
    return str(path), (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def invalidate_cache(cache_key: str | None = None):
    """失效缓存（特定 key 或全部）"""
    with _cache_lock:
        if cache_key is None:
            count = len(_config_cache)
            _config_cache.clear()
            logger.info(f"已清空所有配置缓存（{count} 项）")
        elif cache_key in _config_cache:
            del _config_cache[cache_key]
            logger.debug(f"已失效缓存: {cache_key}")


def setup_file_watcher(cache_key: str, file_path: Path):
    """
    设置文件监听（可选，需要 watchdog）

    watchdog 仅提前失效缓存；读取时仍检查完整文件指纹。
    """
    if not _watchdog_available or cache_key in _file_watchers:
        return

    try:
        handler = ConfigFileChangeHandler(cache_key)
        observer = Observer()
        observer.schedule(handler, str(file_path.parent), recursive=False)
        observer.start()

        _file_watchers[cache_key] = observer
        logger.debug(f"已启动配置文件监听: {file_path}")
    except Exception as e:
        logger.warning(f"无法启动文件监听，继续使用文件指纹检查: {e}")


FuncType = TypeVar("FuncType", bound=Callable[..., dict[str, Any]])


def cached_config_loader(
    cache_key_fn: Callable[..., str],
    file_paths_fn: Callable[..., list[Path]],
    enable_file_watch: bool = False,
) -> Callable[[FuncType], FuncType]:
    """
    配置加载器装饰器

    参数：
        cache_key_fn: 从函数参数生成缓存 key 的函数
        file_paths_fn: 从函数参数获取需要监听的文件路径列表
        enable_file_watch: 是否额外启用文件监听（始终检查文件指纹）

    示例：
        @cached_config_loader(
            cache_key_fn=lambda config_path, workspace_dir: f"{config_path}:{workspace_dir}",
            file_paths_fn=lambda config_path, workspace_dir: [Path(config_path)]
        )
        def read_runtime_config(config_path, workspace_dir=None):
            ...

    返回：
        装饰后的函数，自动处理缓存逻辑
    """

    def decorator(func: FuncType) -> FuncType:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 日志初始化可能间接读配置。保护仅限当前线程，不把其他
            # workspace 的并行读取误判成递归，也不在持锁时调用加载器。
            if getattr(_loading, "active", False):
                return func(*args, **kwargs)
            _loading.active = True
            try:
                # 生成缓存 key 和文件路径列表
                cache_key = cache_key_fn(*args, **kwargs)
                file_paths = file_paths_fn(*args, **kwargs)
                fingerprints = _file_fingerprints(file_paths)

                # 检查缓存是否新鲜
                with _cache_lock:
                    cached = _config_cache.get(cache_key)
                    if cached is not None and cached[0] == fingerprints:
                        _, cached_data = cached
                        logger.debug(f"配置缓存命中: {cache_key}")
                        return deepcopy(cached_data)

                # 缓存未命中或已过期，重新加载
                logger.debug(f"配置缓存未命中，加载: {cache_key}")
                start_time = time.time()
                data = func(*args, **kwargs)
                load_time = (time.time() - start_time) * 1000

                # 更新缓存
                # A file edited during parsing must be re-read next time; never
                # attach a new fingerprint to data read from an older file.
                if _file_fingerprints(file_paths) == fingerprints:
                    with _cache_lock:
                        _config_cache[cache_key] = (fingerprints, deepcopy(data))

                logger.debug(f"配置加载完成: {cache_key} ({load_time:.1f}ms)")

                # 可选：设置文件监听
                if enable_file_watch and file_paths:
                    setup_file_watcher(cache_key, file_paths[0])

                return data

            finally:
                _loading.active = False

        return wrapper  # type: ignore[return-value]

    return decorator


def get_cache_stats() -> dict[str, Any]:
    """获取缓存统计信息（用于监控和调试）"""
    with _cache_lock:
        return {
            "cached_configs": len(_config_cache),
            "cache_keys": list(_config_cache.keys()),
            "file_watchers": len(_file_watchers),
            "watchdog_enabled": _watchdog_available,
        }


def clear_all_caches():
    """清空所有缓存（用于测试或手动刷新）"""
    invalidate_cache(None)


__all__ = [
    "cached_config_loader",
    "invalidate_cache",
    "clear_all_caches",
    "get_cache_stats",
]
