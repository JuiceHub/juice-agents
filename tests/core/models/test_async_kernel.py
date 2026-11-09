"""模型本身提供原生协程；执行器只在调用边界适配同步运行时。"""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from juice_agents.core.models.chat_models import BaseChatModel, ChatModel
from juice_agents.core.models.reward_models import BaseRewardModel, GenerativeRewardModel


@pytest.mark.parametrize("method", [
    BaseChatModel.generate, ChatModel.generate,
    BaseRewardModel.evaluate_batch, GenerativeRewardModel.evaluate_batch,
])
def test_model_methods_are_native_coroutines(method):
    assert inspect.iscoroutinefunction(method)


@pytest.mark.parametrize("model_type", [ChatModel, GenerativeRewardModel])
def test_models_reject_synchronous_sdk_clients(model_type):
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock())))
    with pytest.raises(TypeError, match="异步"):
        model_type("test-model", client=client)


def test_native_model_concurrent_calls_keep_independent_usage():
    async def run():
        barrier = asyncio.Barrier(2)

        async def respond(**payload):
            text = payload["messages"][0]["content"]
            await barrier.wait()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                usage={"prompt_tokens": len(text), "completion_tokens": 1},
            )

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=respond))),
            close=AsyncMock(),
        )
        async with ChatModel("test-model", client=client) as model:
            results = await asyncio.gather(*(
                model.generate([{"role": "user", "content": text}])
                for text in ("first", "second request")
            ))
        assert results[0]["usage_calls"] is not results[1]["usage_calls"]
        for result in results:
            assert result["usage"]["input_tokens"] == len(result["content"])
        client.close.assert_not_awaited()  # 注入的客户端仍归调用方所有。

    asyncio.run(run())


def test_owned_client_context_closes_once_and_rejects_reuse(monkeypatch):
    """显式关闭和上下文退出共享生命周期，不关闭两次或重建已关闭的模型。"""
    import openai

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
        close=AsyncMock(),
    )
    monkeypatch.setattr(openai, "AsyncOpenAI", Mock(return_value=client))

    async def run():
        async with ChatModel("test-model") as model:
            await model.aclose()
        client.close.assert_awaited_once()
        with pytest.raises(RuntimeError, match="已经关闭"):
            await model.generate([{"role": "user", "content": "test"}])

    asyncio.run(run())


@pytest.mark.parametrize("model_type", [ChatModel, GenerativeRewardModel])
def test_model_pool_cannot_be_reused_across_event_loops(model_type):
    """拒绝跨循环使用连接池，调用方仍可回到原循环继续使用并关闭模型。"""
    create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"scores": [8]}'))],
    ))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    model = model_type("test-model", client=client)

    async def invoke():
        if isinstance(model, ChatModel):
            return await model.generate([{"role": "user", "content": "test"}])
        return await model.evaluate_batch(context=[], actions=[{"content": "test"}])

    with asyncio.Runner() as original, asyncio.Runner() as other:
        original.run(invoke())
        with pytest.raises(RuntimeError, match="跨事件循环"):
            other.run(invoke())
        original.run(invoke())
        original.run(model.aclose())
    assert create.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["openai", "doubao", "anthropic"])
async def test_native_sdk_retains_network_retry_policy(backend):
    """真实 SDK 遇到 429 后自行重试，模型只记录一条可观察调用。"""
    import httpx
    from openai import AsyncOpenAI
    from anthropic import AsyncAnthropic
    from volcenginesdkarkruntime import AsyncArk

    requests = []

    async def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={
                "type": "error", "error": {"type": "rate_limit_error", "message": "retry"},
            })
        if backend == "anthropic":
            return httpx.Response(200, json={
                "id": "message_test", "type": "message", "role": "assistant", "model": "test-model",
                "content": [{"type": "text", "text": "answer"}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 7, "output_tokens": 3},
            })
        return httpx.Response(200, json={
            "id": "completion_test", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "answer"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        })

    client_type = {"openai": AsyncOpenAI, "doubao": AsyncArk, "anthropic": AsyncAnthropic}[backend]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with client_type(api_key="test-key", base_url="https://example.test", max_retries=1, http_client=http) as client:
            async with ChatModel("test-model", backend=backend, client=client) as model:
                result = await model.generate([{"role": "user", "content": "test"}])
    assert result["content"] == "answer"
    assert len(requests) == 2
    assert len(result["usage_calls"]) == 1


def test_cancel_during_empty_response_retry_preserves_usage():
    async def run():
        create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" "))],
            usage={"prompt_tokens": 7, "completion_tokens": 3},
        ))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        async with ChatModel("test-model", client=client, wait_time=60) as model:
            task = asyncio.create_task(model.generate([{"role": "user", "content": "test"}]))
            async with asyncio.timeout(2):
                while not create.await_count:
                    await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as raised:
                await task
            assert raised.value.usage_calls[0]["usage"]["total_tokens"] == 10
            create.assert_awaited_once()

    asyncio.run(run())
