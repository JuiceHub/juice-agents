"""原生模型管理：并发身份、用量、取消收尾及连接所有权。"""

import asyncio
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from juice_agents.core.managers import ModelManager
from juice_agents.core.models.chat_models import ChatModel


def test_shared_model_keeps_request_identity_and_usage(tmp_path):
    async def execute():
        barrier = asyncio.Barrier(2)

        async def respond(**payload):
            await barrier.wait()
            text = payload["messages"][0]["content"]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                                   usage={"prompt_tokens": len(text), "completion_tokens": 1})

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=respond))),
                                 close=AsyncMock())
        model = ChatModel("shared", client=client)
        manager = ModelManager(usage_path=tmp_path / "usage.jsonl")
        events = []

        async def call(agent):
            return await manager.acall(model, "generate", purpose="generation",
                args=([{"role": "user", "content": agent}],),
                on_event=lambda kind, data: events.append((agent, kind, data)),
                on_model_usage=partial(manager.record, agent_id=agent, agent_name=agent))

        try:
            results = await asyncio.wait_for(asyncio.gather(call("alice"), call("bob")), 3)
            assert [value["content"] for value in results] == ["alice", "bob"]
            assert {record["agent_id"] for record in manager.model_usage_records()} == {"alice", "bob"}
            assert manager.token_usage()["total"]["input_tokens"] == 8
            starts = [data["model_call_id"] for _, kind, data in events if kind == "model_start"]
            ends = [data["model_call_id"] for _, kind, data in events if kind == "model_end"]
            assert len(set(starts)) == 2 and set(starts) == set(ends)
        finally:
            await manager.aclose()
            # 运行时不能夺走调用方注入的连接所有权。
            client.close.assert_not_awaited()
            await model.aclose()

    asyncio.run(execute())


def test_cancel_waits_for_cleanup_and_records_partial_usage(tmp_path):
    async def execute():
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Model:
            model_name, backend = "slow", "test"

            async def generate(self, messages):
                started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError as exc:
                    exc.usage_calls = [{"backend": "test", "model_name": "slow",
                                        "usage": {"input_tokens": 7}}]
                    raise
                finally:
                    cleaning.set()
                    await release.wait()

        manager = ModelManager(usage_path=tmp_path / "usage.jsonl")
        events = []
        task = asyncio.create_task(manager.acall(Model(), "generate", purpose="generation", args=([],),
            on_event=lambda kind, data: events.append((kind, data)),
            on_model_usage=partial(manager.record, agent_id="a", agent_name="alice")))
        await started.wait()
        task.cancel()
        await cleaning.wait()
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and [kind for kind, _ in events] == ["model_start"]
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events[-1][1]["status"] == "cancelled"
        assert manager.token_usage()["total"]["input_tokens"] == 7
        await manager.aclose()

    asyncio.run(execute())


def test_owned_client_is_closed_once_at_manager_shutdown(monkeypatch):
    import openai

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])))),
        close=AsyncMock())
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **_kwargs: client)

    async def execute():
        manager = ModelManager()
        model = ChatModel("owned")
        await manager.acall(model, "generate", purpose="generation", args=([{"role": "user", "content": "hi"}],))
        await manager.aclose()
        await manager.aclose()
        client.close.assert_awaited_once()
        with pytest.raises(RuntimeError, match="已关闭"):
            await manager.acall(model, "generate", purpose="generation", args=([],))

    asyncio.run(execute())
