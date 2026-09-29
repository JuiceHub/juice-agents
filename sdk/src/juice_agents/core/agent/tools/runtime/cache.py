"""
工具结果 Turn 级运行时缓存

功能：
- 在单个 turn 内缓存只读工具的执行结果
- 避免重复执行相同的文件读取、搜索操作
- Turn 结束后自动清空缓存

使用场景：
- Deep Research 场景：多次读取相同文件
- Code Review：重复执行 grep 搜索
- Memory Search：反复访问相同 memory 文件

性能目标：
- Deep Research 场景提升 20-40%
- 文件 IO 次数减少 50%+
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

# Turn 级缓存：{turn_id: {cache_key: result}}
_turn_cache: dict[int, dict[str, Any]] = {}
_current_turn_id: int = 0
_cache_lock = threading.Lock()

ResultType = TypeVar("ResultType")


class ToolResultCache:
    """工具结果缓存管理器"""

    def __init__(self):
        self._turn_id = 0
        self._cache: dict[str, Any] = {}

    def get_or_compute(
        self,
        cache_key: str,
        compute_fn: Callable[[], ResultType],
        *,
        enable_cache: bool = True,
    ) -> ResultType:
        """
        获取缓存结果或执行计算

        参数：
            cache_key: 缓存键（应包含所有影响结果的参数）
            compute_fn: 计算函数（无参数）
            enable_cache: 是否启用缓存（默认 True）

        返回：
            计算结果或缓存结果
        """
        if not enable_cache:
            return compute_fn()

        full_key = f"{self._turn_id}:{cache_key}"

        if full_key in self._cache:
            logger.debug(f"工具缓存命中: {cache_key}")
            return self._cache[full_key]

        logger.debug(f"工具缓存未命中，执行计算: {cache_key}")
        result = compute_fn()
        self._cache[full_key] = result

        return result

    def new_turn(self):
        """开始新的 turn，清空缓存"""
        old_size = len(self._cache)
        self._turn_id += 1
        self._cache.clear()

        if old_size > 0:
            logger.info(f"Turn {self._turn_id - 1} 缓存已清空（共 {old_size} 项）")

    def clear(self):
        """手动清空缓存"""
        self._cache.clear()
        logger.debug("工具缓存已手动清空")

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计信息"""
        return {
            "turn_id": self._turn_id,
            "cached_items": len(self._cache),
            "cache_keys": list(self._cache.keys()),
        }


# 全局缓存实例
_global_tool_cache = ToolResultCache()


def get_tool_cache() -> ToolResultCache:
    """获取全局工具缓存实例"""
    return _global_tool_cache


def new_turn():
    """
    开始新的 turn（应在 agent.step() 开始时调用）

    这个函数应该被集成到 Runner 的 turn 生命周期中。
    """
    _global_tool_cache.new_turn()


def cached_tool_call(cache_key: str, *, enable_cache: bool = True):
    """
    工具调用缓存装饰器

    使用示例：
        @cached_tool_call("read_file:{file_path}:{offset}:{limit}")
        def read_file(file_path: str, offset: int = 0, limit: int = 2000):
            # 实际读取逻辑
            ...

    参数：
        cache_key: 缓存键模板（支持格式化）
        enable_cache: 是否启用缓存

    返回：
        装饰后的函数
    """

    def decorator(func: Callable[..., ResultType]) -> Callable[..., ResultType]:
        def wrapper(*args, **kwargs) -> ResultType:
            # 生成具体的缓存键（将函数参数填充到模板中）
            try:
                # 尝试使用参数格式化 cache_key
                if "{" in cache_key:
                    # 从 args 和 kwargs 构建格式化字典
                    import inspect

                    sig = inspect.signature(func)
                    bound = sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    actual_key = cache_key.format(**bound.arguments)
                else:
                    actual_key = cache_key
            except Exception:
                # 格式化失败，使用原始 key + 参数哈希
                actual_key = f"{cache_key}:{hash((args, tuple(sorted(kwargs.items()))))}"

            return _global_tool_cache.get_or_compute(
                actual_key, lambda: func(*args, **kwargs), enable_cache=enable_cache
            )

        return wrapper

    return decorator


__all__ = [
    "ToolResultCache",
    "get_tool_cache",
    "new_turn",
    "cached_tool_call",
]
