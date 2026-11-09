"""Python 工具共享执行状态的互斥与并发边界。"""

from __future__ import annotations

import threading

from juice_agents.core.agent.tools.builtin.code_execution.code_tools import PythonTool
from juice_agents.core.managers.tools import ToolManager


def test_python_instance_lock_preserves_independent_tool_concurrency(monkeypatch):
    """同一执行器互斥；另一个 Python 执行器仍能与它同时运行。"""

    shared = PythonTool()
    independent = PythonTool()
    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    # 屏障只有在两个独立实例同时进入 forward 时才能通过，避免用耗时推断并发。
    independent_running = threading.Barrier(2, timeout=3)

    def shared_forward(code: str):
        if code == "first":
            first_entered.set()
            independent_running.wait()
            assert release_first.wait(3), "first Python call was not released"
        else:
            second_entered.set()
            assert release_first.is_set(), "shared Python executor was entered concurrently"
        return {"result": code}

    def independent_forward(code: str):
        independent_running.wait()
        return {"result": code}

    # 只替换执行体，保留真实 PythonTool 的 policy、ToolManager 调度和资源锁。
    monkeypatch.setattr(shared, "forward", shared_forward)
    monkeypatch.setattr(independent, "forward", independent_forward)
    manager = ToolManager({"shared": shared, "independent": independent}, max_workers=3)
    stream = manager.execute_streaming([
        {"name": "shared", "args": {"code": "first"}},
        {"name": "shared", "args": {"code": "second"}},
        {"name": "independent", "args": {"code": "independent"}},
    ])
    results = []
    try:
        assert next(stream).result is None
        assert next(stream).result is None
        # start 在提交 worker 之前发出；先让首个调用拿到锁，再提交第二个。
        # 这不依赖同一资源上线程获得锁的先后顺序。
        assert first_entered.wait(3), "first Python call did not start"
        for event in stream:
            if event.result is None:
                continue
            results.append(event.result)
            if event.result.order == 2:
                assert event.result.status == "completed", event.result.error
                # 独立工具已经完成，首个共享调用仍被 Event 阻塞。第二个共享调用
                # 此时只能在资源锁外等待，不能提前进入持久执行器。
                assert not second_entered.is_set()
                release_first.set()
        assert second_entered.is_set()
        assert all(result.status == "completed" for result in results)
        assert [result.observation["result"] for result in sorted(results, key=lambda r: r.order)] == [
            "first", "second", "independent",
        ]
    finally:
        release_first.set()
        independent_running.abort()
        stream.close()
        manager.release()
