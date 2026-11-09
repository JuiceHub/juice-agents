"""真实 Agent/Manager/工具执行链，模型替身只替换网络响应。"""

import asyncio
import threading
import json

import pytest

from juice_agents import Runner
from juice_agents.core.agent.agents import MultiStepAgent
from juice_agents.core.agent.tools.runtime.executor import ToolExecutionPolicy
from juice_agents.core.registry.agents import registry as agent_registry_module
from juice_agents.core.registry.agents.types import AgentConfig


def test_many_agents_await_models_on_one_loop_without_sync_bridge(tmp_path, monkeypatch):
    class Model:
        def __init__(self):
            self.barrier = None
            self.threads = set()

        async def generate(self, messages, **_kwargs):
            if self.barrier is None:
                self.barrier = asyncio.Barrier(40)
            self.threads.add(threading.get_ident())
            await self.barrier.wait()
            return {"content": '<actions>[{"name":"submit_output","args":{"output":"done"}}]</actions>',
                    "usage_calls": []}

    model = Model()
    monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: model)
    # 如果任一生成/评分/压缩路径退回逐请求同步桥接，测试立即失败。
    monkeypatch.setattr(MultiStepAgent, "_call_model", lambda *_args, **_kwargs: pytest.fail("sync model bridge used"))

    async def execute():
        runner = await Runner.acreate(base_dir=tmp_path, permission_mode="accept")
        try:
            agents = [await runner.agent_manager.aacquire(
                AgentConfig(name=f"worker_{i}", tools=["submit_output"], max_steps=1,
                            enable_skill_tools=False, lifecycle="functional"),
                model=model, lifecycle="functional") for i in range(40)]
            results = await asyncio.wait_for(asyncio.gather(*(
                runner.agent_manager.arun(agent, "work") for agent in agents)), 15)
            assert all(result.unwrap() == "done" for result in results)
            assert len(model.threads) == 1
            assert not any(thread.name.startswith(f"agent-{runner.runner_id}") for thread in threading.enumerate())
        finally:
            await runner.aclose()

    asyncio.run(execute())


def test_async_runner_cancel_returns_only_after_model_cleanup(tmp_path, monkeypatch):
    started, cleaned = threading.Event(), threading.Event()

    class Model:
        async def generate(self, messages, **_kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0.03)
                cleaned.set()

    monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: Model())

    async def execute():
        runner = await Runner.acreate(base_dir=tmp_path, permission_mode="accept")
        try:
            task = asyncio.create_task(runner.arun("wait"))
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert cleaned.is_set()
            root = runner.agent_manager.get("root")
            assert root.round_checkpoint["status"] == "cancelled"
            assert runner._active_requests == 0
        finally:
            await runner.aclose()

    asyncio.run(execute())


def test_nested_sync_tools_do_not_exhaust_shared_stream_workers(tmp_path, monkeypatch):
    """超过流适配池容量的父工具并发等待子 Agent，子工具仍能执行。"""
    class Model:
        barrier = None

        async def generate(self, messages, **_kwargs):
            text = json.dumps(messages)
            if "leaf-only-marker" in text:
                if self.barrier is None:
                    self.barrier = asyncio.Barrier(12)
                await self.barrier.wait()
                action = {"name": "submit_output", "args": {"output": "nested-complete"}}
            elif "nested-complete" in text:
                action = {"name": "submit_output", "args": {"output": "parent-complete"}}
            else:
                action = {"name": "write", "args": {"path": "unused", "content": "invoke"}}
            return {"content": f"<actions>{json.dumps([action])}</actions>", "usage_calls": []}

    model = Model()
    monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: model)

    async def execute():
        async with await Runner.acreate(base_dir=tmp_path, permission_mode="accept") as runner:
            await asyncio.to_thread(runner._root)
            parents = [await runner.agent_manager.aacquire(
                AgentConfig(name=f"parent_{i}", instructions="Parent worker.", tools=["write", "submit_output"],
                            max_steps=2, enable_skill_tools=False, lifecycle="functional"),
                model=model, lifecycle="functional", permission_mode="accept") for i in range(12)]
            for parent in parents:
                def forward(*, path, content, owner=parent):
                    return runner.invoke_agent({
                        "agent_ref": AgentConfig(name="leaf", instructions="leaf-only-marker", tools=["submit_output"],
                                                  enable_skill_tools=False, max_steps=1),
                        "task": "child work", "execution": "sync", "owner_agent_name": owner.agent_name,
                        "permission_mode": "accept", "ephemeral": True,
                    })["output"]
                monkeypatch.setattr(parent.instance.tools["write"], "forward", forward)
                # 每个父工具使用不同资源锁；本测试检验的是调度容量而非文件互斥。
                monkeypatch.setattr(parent.instance.tools["write"], "execution_policy",
                                    lambda args, ctx: ToolExecutionPolicy())
            results = await asyncio.wait_for(asyncio.gather(*(
                runner.agent_manager.arun(parent, "parent work") for parent in parents)), 15)
            assert [result.unwrap() for result in results] == ["parent-complete"] * 12
            assert all(not parent.is_live for parent in parents)

    asyncio.run(execute())


def test_cancel_during_durable_acceptance_does_not_orphan_round(tmp_path, monkeypatch):
    posting, release = threading.Event(), threading.Event()

    class Model:
        async def generate(self, *_args, **_kwargs):
            await asyncio.Future()

    monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: Model())

    async def execute():
        async with await Runner.acreate(base_dir=tmp_path, permission_mode="accept") as runner:
            post = runner.agent_manager.post

            def delayed_post(*args, **kwargs):
                receipt = post(*args, **kwargs)
                posting.set()
                assert release.wait(3)
                return receipt

            monkeypatch.setattr(runner.agent_manager, "post", delayed_post)
            task = asyncio.create_task(runner.arun("cancel while accepting"))
            try:
                assert await asyncio.to_thread(posting.wait, 3)
                task.cancel()
                await asyncio.sleep(0.06)
                task.cancel()  # 重复取消不能跳过持久化事务和执行收尾。
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
                root = runner.agent_manager.get("root")
                assert root.round_checkpoint["status"] == "cancelled"
                assert runner._active_requests == 0
            finally:
                release.set()

    asyncio.run(execute())
