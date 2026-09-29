"""
配置文件智能缓存模块（参考 Claude Code 实现）

核心特性：
- 基于文件 mtime 的自动失效机制
- 支持 watchdog 文件监听（可选依赖）
- 重入保护防止递归加载
- 多级缓存：内存 + mtime 检查

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
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

# 重入保护：防止 config → logger → config 死循环
_inside_config_load = False
_config_load_lock = Lock()

# 配置缓存结构：{cache_key: (mtime, data)}
_config_cache: dict[str, tuple[float, dict[str, Any]]] = {}
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

        def on_modified(self, event):
            if not event.is_directory:
                logger.debug(f"配置文件已变化，失效缓存: {event.src_path}")
                invalidate_cache(self.cache_key)

    logger.debug("watchdog 已加载，启用文件监听模式")
except ImportError:
    Observer = None  # type: ignore
    ConfigFileChangeHandler = None  # type: ignore
    logger.debug("watchdog 未安装，配置缓存将使用 mtime 检查模式")


def _get_file_mtime(path: Path) -> float:
    """获取文件修改时间，文件不存在返回 0"""
    try:
        return path.stat().st_mtime if path.exists() else 0.0
    except Exception:
        return 0.0


def _is_cache_fresh(cache_key: str, file_paths: list[Path]) -> bool:
    """检查缓存是否新鲜（所有文件 mtime 未变化）"""
    if cache_key not in _config_cache:
        return False

    cached_mtime, _ = _config_cache[cache_key]

    # 检查所有相关文件的 mtime
    for path in file_paths:
        current_mtime = _get_file_mtime(path)
        if current_mtime > cached_mtime:
            logger.debug(f"文件 {path} 已变化，缓存过期 (cached: {cached_mtime}, current: {current_mtime})")
            return False

    return True


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

    优先级：watchdog 监听 > mtime 轮询
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
        logger.warning(f"无法启动文件监听，回退到 mtime 模式: {e}")


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
        enable_file_watch: 是否启用文件监听（默认使用 mtime）

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
            global _inside_config_load

            # 重入保护：防止递归加载
            with _config_load_lock:
                if _inside_config_load:
                    logger.warning(f"检测到配置加载重入，跳过缓存: {func.__name__}")
                    return func(*args, **kwargs)

                _inside_config_load = True

            try:
                # 生成缓存 key 和文件路径列表
                cache_key = cache_key_fn(*args, **kwargs)
                file_paths = file_paths_fn(*args, **kwargs)

                # 检查缓存是否新鲜
                with _cache_lock:
                    if _is_cache_fresh(cache_key, file_paths):
                        _, cached_data = _config_cache[cache_key]
                        logger.debug(f"配置缓存命中: {cache_key}")
                        return cached_data

                # 缓存未命中或已过期，重新加载
                logger.debug(f"配置缓存未命中，加载: {cache_key}")
                start_time = time.time()
                data = func(*args, **kwargs)
                load_time = (time.time() - start_time) * 1000

                # 更新缓存
                current_mtime = max(_get_file_mtime(p) for p in file_paths) if file_paths else time.time()
                with _cache_lock:
                    _config_cache[cache_key] = (current_mtime, data)

                logger.debug(f"配置加载完成: {cache_key} ({load_time:.1f}ms)")

                # 可选：设置文件监听
                if enable_file_watch and file_paths:
                    setup_file_watcher(cache_key, file_paths[0])

                return data

            finally:
                with _config_load_lock:
                    _inside_config_load = False

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
