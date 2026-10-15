"""测试探索策略切换功能。"""

import pytest


def test_switch_strategy_greedy():
    """测试切换到贪心策略。"""
    from adapters.stdio_gateway.runtime import DirectRunnerRuntime

    runtime = DirectRunnerRuntime(
        base_dir=".",
        permission_mode="default",
        agent_mode="agent",
    )

    # 切换到贪心策略
    result = runtime.switch_strategy({"reset": True})

    assert result["status"] in ("pending", "applied")
    assert result["strategy"]["type"] == "greedy"
    assert result["strategy"]["params"] == {}


def test_switch_strategy_exploration():
    """测试切换到探索策略。"""
    from adapters.stdio_gateway.runtime import DirectRunnerRuntime

    runtime = DirectRunnerRuntime(
        base_dir=".",
        permission_mode="default",
        agent_mode="agent",
    )

    # 切换到探索策略
    result = runtime.switch_strategy({
        "breadth": 3,
        "depth": 5,
        "beam_width": 2
    })

    assert result["status"] in ("pending", "applied")
    assert result["strategy"]["type"] == "exploration"
    assert result["strategy"]["params"]["breadth"] == 3
    assert result["strategy"]["params"]["depth"] == 5
    assert result["strategy"]["params"]["beam_width"] == 2


def test_switch_strategy_best_of_n():
    """测试 Best-of-N 配置（breadth=N, depth=1, beam_width=1）。"""
    from adapters.stdio_gateway.runtime import DirectRunnerRuntime

    runtime = DirectRunnerRuntime(
        base_dir=".",
        permission_mode="default",
        agent_mode="agent",
    )

    # Best-of-N: breadth=5, depth=1, beam_width=1
    result = runtime.switch_strategy({
        "breadth": 5,
        "depth": 1,
        "beam_width": 1
    })

    assert result["status"] in ("pending", "applied")
    assert result["strategy"]["type"] == "exploration"
    assert result["strategy"]["params"]["breadth"] == 5
    assert result["strategy"]["params"]["depth"] == 1
    assert result["strategy"]["params"]["beam_width"] == 1


def test_switch_strategy_beam_search():
    """测试 Beam Search 配置（breadth=W, depth=D, beam_width=W）。"""
    from adapters.stdio_gateway.runtime import DirectRunnerRuntime

    runtime = DirectRunnerRuntime(
        base_dir=".",
        permission_mode="default",
        agent_mode="agent",
    )

    # Beam Search: breadth=3, depth=5, beam_width=3
    result = runtime.switch_strategy({
        "breadth": 3,
        "depth": 5,
        "beam_width": 3
    })

    assert result["status"] in ("pending", "applied")
    assert result["strategy"]["type"] == "exploration"
    assert result["strategy"]["params"]["breadth"] == 3
    assert result["strategy"]["params"]["depth"] == 5
    assert result["strategy"]["params"]["beam_width"] == 3


def test_switch_strategy_list_breadth():
    """测试自适应宽度（breadth 为列表）。"""
    from adapters.stdio_gateway.runtime import DirectRunnerRuntime

    runtime = DirectRunnerRuntime(
        base_dir=".",
        permission_mode="default",
        agent_mode="agent",
    )

    # 自适应宽度
    result = runtime.switch_strategy({
        "breadth": [5, 3, 2, 1],
        "depth": 4,
        "beam_width": 2
    })

    assert result["status"] in ("pending", "applied")
    assert result["strategy"]["type"] == "exploration"
    assert result["strategy"]["params"]["breadth"] == [5, 3, 2, 1]
    assert result["strategy"]["params"]["depth"] == 4
    assert result["strategy"]["params"]["beam_width"] == 2


def test_switch_strategy_default_values():
    """测试默认值（1,1,1）自动使用贪心策略。"""
    from adapters.stdio_gateway.runtime import DirectRunnerRuntime

    runtime = DirectRunnerRuntime(
        base_dir=".",
        permission_mode="default",
        agent_mode="agent",
    )

    # 默认值应该使用贪心策略
    result = runtime.switch_strategy({
        "breadth": 1,
        "depth": 1,
        "beam_width": 1
    })

    assert result["status"] in ("pending", "applied")
    assert result["strategy"]["type"] == "greedy"


def test_rpc_handler_switch_strategy():
    """测试 RPC handler。"""
    from adapters.stdio_gateway.handlers import RpcHandlers

    handlers = RpcHandlers()

    # 测试切换策略
    result = handlers.handle_switch_strategy({
        "base_dir": ".",
        "breadth": 3,
        "depth": 5,
        "beam_width": 2
    })

    assert "status" in result
    assert "strategy" in result
    assert "message" in result
    assert result["strategy"]["type"] == "exploration"
