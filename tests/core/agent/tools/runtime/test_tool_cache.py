"""
Tool runtime 的 Turn 级结果缓存测试。
"""

import time
from unittest.mock import Mock

import pytest

from juice_agents.core.agent.tools.runtime.cache import (
    ToolResultCache,
    cached_tool_call,
    get_tool_cache,
    new_turn,
)


class TestToolResultCache:
    """测试 ToolResultCache 类"""

    def test_get_or_compute_cache_hit(self):
        """测试缓存命中"""
        cache = ToolResultCache()
        compute_fn = Mock(return_value="result")

        # 首次调用
        result1 = cache.get_or_compute("key1", compute_fn)
        assert result1 == "result"
        assert compute_fn.call_count == 1

        # 再次调用（缓存命中）
        result2 = cache.get_or_compute("key1", compute_fn)
        assert result2 == "result"
        assert compute_fn.call_count == 1  # 未再次调用

    def test_get_or_compute_different_keys(self):
        """测试不同 key 不共享缓存"""
        cache = ToolResultCache()
        compute_fn1 = Mock(return_value="result1")
        compute_fn2 = Mock(return_value="result2")

        result1 = cache.get_or_compute("key1", compute_fn1)
        result2 = cache.get_or_compute("key2", compute_fn2)

        assert result1 == "result1"
        assert result2 == "result2"
        assert compute_fn1.call_count == 1
        assert compute_fn2.call_count == 1

    def test_new_turn_clears_cache(self):
        """测试新 turn 清空缓存"""
        cache = ToolResultCache()
        compute_fn = Mock(return_value="result")

        # Turn 1
        cache.get_or_compute("key1", compute_fn)
        assert compute_fn.call_count == 1

        # 开始新 turn
        cache.new_turn()

        # Turn 2 - 应该重新计算
        cache.get_or_compute("key1", compute_fn)
        assert compute_fn.call_count == 2

    def test_cache_disabled(self):
        """测试禁用缓存"""
        cache = ToolResultCache()
        compute_fn = Mock(return_value="result")

        # 禁用缓存
        result1 = cache.get_or_compute("key1", compute_fn, enable_cache=False)
        result2 = cache.get_or_compute("key1", compute_fn, enable_cache=False)

        assert result1 == "result"
        assert result2 == "result"
        assert compute_fn.call_count == 2  # 每次都调用

    def test_get_stats(self):
        """测试获取统计信息"""
        cache = ToolResultCache()

        stats = cache.get_stats()
        assert stats["turn_id"] == 0
        assert stats["cached_items"] == 0

        cache.get_or_compute("key1", lambda: "result")

        stats = cache.get_stats()
        assert stats["cached_items"] == 1


class TestCachedToolCallDecorator:
    """测试 cached_tool_call 装饰器"""

    def test_decorator_with_simple_key(self):
        """测试简单缓存键"""
        call_count = {"count": 0}

        @cached_tool_call("simple_key")
        def expensive_function():
            call_count["count"] += 1
            return "result"

        result1 = expensive_function()
        result2 = expensive_function()

        assert result1 == "result"
        assert result2 == "result"
        assert call_count["count"] == 1  # 只调用一次

    def test_decorator_with_formatted_key(self):
        """测试格式化缓存键"""
        call_count = {"count": 0}

        @cached_tool_call("read_file:{file_path}:{offset}")
        def read_file(file_path: str, offset: int = 0):
            call_count["count"] += 1
            return f"Content of {file_path} at {offset}"

        # 相同参数
        result1 = read_file("/tmp/test.txt", offset=0)
        result2 = read_file("/tmp/test.txt", offset=0)
        assert result1 == result2
        assert call_count["count"] == 1

        # 不同参数
        result3 = read_file("/tmp/test.txt", offset=100)
        assert result3 != result1
        assert call_count["count"] == 2

    def test_decorator_respects_new_turn(self):
        """测试装饰器在新 turn 后重新计算"""
        call_count = {"count": 0}

        @cached_tool_call("test_key")
        def test_function():
            call_count["count"] += 1
            return "result"

        # Turn 1
        test_function()
        assert call_count["count"] == 1

        # 缓存命中
        test_function()
        assert call_count["count"] == 1

        # 新 turn
        new_turn()
        test_function()
        assert call_count["count"] == 2


class TestGlobalToolCache:
    """测试全局缓存实例"""

    def test_get_tool_cache_returns_same_instance(self):
        """测试 get_tool_cache 返回相同实例"""
        cache1 = get_tool_cache()
        cache2 = get_tool_cache()

        assert cache1 is cache2

    def test_new_turn_affects_global_cache(self):
        """测试 new_turn 影响全局缓存"""
        cache = get_tool_cache()
        initial_turn = cache._turn_id

        new_turn()

        assert cache._turn_id == initial_turn + 1


class TestPerformance:
    """性能测试"""

    def test_cache_improves_performance(self):
        """测试缓存确实提升性能"""

        @cached_tool_call("perf_test")
        def slow_function():
            time.sleep(0.01)  # 模拟慢速操作
            return "result"

        # 首次调用
        start = time.time()
        slow_function()
        first_call_time = time.time() - start

        # 缓存调用
        start = time.time()
        slow_function()
        cached_call_time = time.time() - start

        # 缓存调用应该明显更快
        assert cached_call_time < first_call_time * 0.1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
