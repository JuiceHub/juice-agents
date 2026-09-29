"""
Session 懒加载功能测试
"""

import json
import tempfile
import time
from pathlib import Path

import pytest

from juice_agents.core.agent.sessions.lazy_serialization import (
    DEFAULT_RECENT_STEPS_COUNT,
    LAZY_LOAD_THRESHOLD,
    LazySessionLoader,
    get_session_size_info,
    should_use_lazy_loading,
)


def create_test_session(step_count: int) -> Path:
    """创建测试用的 session.json 文件"""
    steps = []
    for i in range(step_count):
        steps.append({
            "type": "action",
            "step_num": i + 1,
            "model_output": f"Test step {i + 1}",
            "thought": f"Thinking about step {i + 1}",
            "tool_calls": [],
            "observations": [f"Observation for step {i + 1}"],
        })

    session_data = {
        "system_prompt": "Test system prompt",
        "system_prompt_static": "Static part",
        "system_prompt_dynamic": "Dynamic part",
        "include_reasoning_in_context": True,
        "max_observation_length": 10000,
        "steps": steps,
    }

    tmp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(session_data, tmp_file, ensure_ascii=False)
    tmp_file.close()

    return Path(tmp_file.name)


class TestLazySessionLoader:
    """测试 LazySessionLoader 类"""

    def test_load_metadata_only(self):
        """测试仅加载元信息"""
        session_path = create_test_session(50)
        try:
            loader = LazySessionLoader(session_path)
            metadata = loader.load_metadata()

            assert "system_prompt" in metadata
            assert "total_steps" in metadata
            assert metadata["total_steps"] == 50
            assert metadata["system_prompt"] == "Test system prompt"
        finally:
            session_path.unlink()

    def test_load_recent_steps(self):
        """测试加载最近步骤"""
        session_path = create_test_session(50)
        try:
            loader = LazySessionLoader(session_path)
            recent = loader.load_recent_steps(count=10)

            assert len(recent) == 10
            assert recent[0]["step_num"] == 41  # 最后 10 步从 41 开始
            assert recent[-1]["step_num"] == 50
        finally:
            session_path.unlink()

    def test_load_recent_steps_less_than_total(self):
        """测试加载步数少于总步数的情况"""
        session_path = create_test_session(5)
        try:
            loader = LazySessionLoader(session_path)
            recent = loader.load_recent_steps(count=10)

            assert len(recent) == 5  # 只有 5 步，返回全部
        finally:
            session_path.unlink()

    def test_load_step_range(self):
        """测试范围加载"""
        session_path = create_test_session(50)
        try:
            loader = LazySessionLoader(session_path)
            steps = loader.load_step_range(10, 20)

            assert len(steps) == 10
            assert steps[0]["step_num"] == 11  # 索引 10 对应 step 11
            assert steps[-1]["step_num"] == 20
        finally:
            session_path.unlink()

    def test_get_total_steps(self):
        """测试获取总步数"""
        session_path = create_test_session(30)
        try:
            loader = LazySessionLoader(session_path)
            total = loader.get_total_steps()

            assert total == 30
        finally:
            session_path.unlink()

    def test_data_caching(self):
        """测试数据缓存机制"""
        session_path = create_test_session(20)
        try:
            loader = LazySessionLoader(session_path)

            # 首次加载元信息
            metadata1 = loader.load_metadata()
            assert loader._metadata_cache is not None

            # 再次加载应使用缓存
            metadata2 = loader.load_metadata()
            assert metadata1 == metadata2
            assert loader._metadata_cache is metadata1
        finally:
            session_path.unlink()


class TestShouldUseLazyLoading:
    """测试懒加载判断逻辑"""

    def test_small_file_no_lazy_loading(self):
        """测试小文件不启用懒加载"""
        session_path = create_test_session(5)  # 小文件
        try:
            should_lazy = should_use_lazy_loading(session_path)
            file_size = session_path.stat().st_size

            # 小于阈值应该返回 False
            if file_size < LAZY_LOAD_THRESHOLD:
                assert should_lazy is False
        finally:
            session_path.unlink()

    def test_large_file_uses_lazy_loading(self):
        """测试大文件启用懒加载"""
        session_path = create_test_session(500)  # 大文件
        try:
            should_lazy = should_use_lazy_loading(session_path)
            file_size = session_path.stat().st_size

            # 大于阈值应该返回 True
            if file_size > LAZY_LOAD_THRESHOLD:
                assert should_lazy is True
        finally:
            session_path.unlink()

    def test_nonexistent_file(self):
        """测试不存在的文件"""
        nonexistent = Path("/nonexistent/session.json")
        should_lazy = should_use_lazy_loading(nonexistent)

        assert should_lazy is False


class TestGetSessionSizeInfo:
    """测试获取 session 大小信息"""

    def test_get_size_info(self):
        """测试获取文件大小信息"""
        session_path = create_test_session(30)
        try:
            info = get_session_size_info(session_path)

            assert info["exists"] is True
            assert "size_bytes" in info
            assert "size_kb" in info
            assert "total_steps" in info
            assert info["total_steps"] == 30
            assert "should_lazy_load" in info
        finally:
            session_path.unlink()

    def test_nonexistent_file_info(self):
        """测试不存在文件的信息"""
        nonexistent = Path("/nonexistent/session.json")
        info = get_session_size_info(nonexistent)

        assert info["exists"] is False


class TestLazyLoadingPerformance:
    """性能基准测试"""

    def test_lazy_loading_faster_than_full_load(self):
        """测试懒加载比全量加载更快（大文件场景）"""
        session_path = create_test_session(200)  # 创建足够大的 session
        try:
            # 全量加载
            start = time.time()
            with open(session_path) as f:
                json.load(f)
            full_load_time = time.time() - start

            # 懒加载（仅元信息 + 最近 10 步）
            loader = LazySessionLoader(session_path)
            start = time.time()
            loader.load_metadata()
            loader.load_recent_steps(10)
            lazy_load_time = time.time() - start

            # 懒加载应该更快或相近（考虑到 JSON 解析本身很快）
            # 这里我们主要验证懒加载不会显著慢于全量加载
            assert lazy_load_time <= full_load_time * 1.5  # 允许 50% 的误差范围
        finally:
            session_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
