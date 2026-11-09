"""后台协程取消之后，等待 finally 才向调用方交回控制。"""

import asyncio
import threading

from juice_agents.core.managers import AsyncTaskManager


def test_native_background_callback_cancellation_drains_cleanup(tmp_path):
    started, cleaned = threading.Event(), threading.Event()

    async def callback(_task, _path):
        started.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0.03)
            cleaned.set()

    async def execute():
        manager = AsyncTaskManager(state_dir=tmp_path / "tasks", max_workers=2)
        try:
            receipt = await manager.alaunch(task_type="local_agent", owner_agent_id="root",
                owner_agent_name="root", description="native callback", runner=callback, summary_builder=str)
            assert await asyncio.to_thread(started.wait, 3)
            result = await asyncio.wait_for(manager.acancel(receipt["async_task_id"]), 3)
            assert cleaned.is_set() and result["status"] == "killed"
            assert result["notification"]["state"] == "pending"
        finally:
            assert await manager.aclose()

    asyncio.run(execute())


def test_cancelling_queued_task_does_not_wait_for_unrelated_running_work(tmp_path):
    started = threading.Event()
    queued_calls = []

    async def running(_task, _path):
        started.set()
        await asyncio.Future()

    async def queued(_task, _path):
        queued_calls.append(True)

    async def execute():
        manager = AsyncTaskManager(state_dir=tmp_path / "tasks", max_workers=1)
        try:
            active = await manager.alaunch(task_type="local_agent", owner_agent_id="root",
                owner_agent_name="root", description="running", runner=running, summary_builder=str)
            assert await asyncio.to_thread(started.wait, 3)
            pending = await manager.alaunch(task_type="local_agent", owner_agent_id="root",
                owner_agent_name="root", description="queued", runner=queued, summary_builder=str)
            result = await asyncio.wait_for(manager.acancel(pending["async_task_id"]), 1)
            assert result["status"] == "killed" and not queued_calls
            assert manager.get_task(active["async_task_id"])["status"] == "running"
        finally:
            assert await manager.aclose()

    asyncio.run(execute())
