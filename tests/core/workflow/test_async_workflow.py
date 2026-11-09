"""原生异步脚本经过真实 Registry、WorkflowManager 和临时 Agent。"""

import asyncio
import json
import threading

import pytest

from juice_agents import Runner
from juice_agents.core.registry.agents import registry as agent_registry_module
from juice_agents.core.workflow.types import WorkflowCancelledError, WorkflowTimeoutError


def test_async_workflow_parallel_agents_and_artifact(tmp_path, monkeypatch):
    class Model:
        barrier = None

        async def generate(self, messages, **_kwargs):
            if self.barrier is None:
                self.barrier = asyncio.Barrier(2)
            await self.barrier.wait()
            return {"content": '<actions>[{"name":"submit_output","args":{"output":"done"}}]</actions>',
                    "usage_calls": []}

    model = Model()
    monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: model)
    folder = tmp_path / ".juice" / "workflows"
    folder.mkdir(parents=True)
    (folder / "parallel_native.py").write_text(
        'WORKFLOW_METADATA = {"name": "parallel_native", "tools": [], "read_only": True}\n'
        'async def run(ctx, args):\n'
        '    results = await ctx.aparallel([lambda: ctx.aagent("work", instructions="Answer.", tools=[]) for _ in range(2)])\n'
        '    await ctx.aartifact("results.json", results)\n'
        '    return results\n', encoding="utf-8")

    async def execute():
        runner = await Runner.acreate(base_dir=tmp_path)
        try:
            await asyncio.to_thread(runner._root)
            result = await asyncio.wait_for(runner.workflow_manager.acall("parallel_native", {}), 5)
            assert result["result"] == ["done", "done"]
            assert result["stats"]["agent_calls"] == 2
            from pathlib import Path
            assert json.loads((Path(result["artifacts_dir"]) / "results.json").read_text()) == ["done", "done"]
            assert [agent.agent_name for agent in runner.agent_manager.live_agents.values()] == ["root"]
        finally:
            await runner.aclose()

    asyncio.run(execute())


@pytest.mark.parametrize("control", ["cancel", "timeout"])
def test_waiting_native_workflow_drains_inner_agent_on_control(tmp_path, monkeypatch, control):
    started, cleaned, cancelled = threading.Event(), threading.Event(), threading.Event()

    class Model:
        async def generate(self, *_args, **_kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0.02)
                cleaned.set()

    monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: Model())
    folder = tmp_path / ".juice" / "workflows"
    folder.mkdir(parents=True)
    (folder / "waiting.py").write_text(
        'WORKFLOW_METADATA = {"name": "waiting", "tools": [], "read_only": True}\n'
        'async def run(ctx, args):\n'
        '    return await ctx.aagent("wait", instructions="Answer.", tools=[])\n', encoding="utf-8")

    async def execute():
        async with await Runner.acreate(base_dir=tmp_path) as runner:
            await asyncio.to_thread(runner._root)
            task = asyncio.create_task(runner.workflow_manager.acall(
                "waiting", {}, {"timeout_seconds": 0.5} if control == "timeout" else None,
                cancel_event=cancelled))
            assert await asyncio.to_thread(started.wait, 3)
            if control == "cancel":
                cancelled.set()
            with pytest.raises(WorkflowTimeoutError if control == "timeout" else WorkflowCancelledError):
                await asyncio.wait_for(task, 3)
            assert cleaned.is_set()
            assert [agent.agent_name for agent in runner.agent_manager.live_agents.values()] == ["root"]
            assert not runner.agent_manager.get("root").cancel_event.is_set()

    asyncio.run(execute())
