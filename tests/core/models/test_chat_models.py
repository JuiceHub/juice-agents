"""聊天模型拆分后的功能回归；只替换外部 SDK client，不访问网络。"""

from __future__ import annotations

import copy
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import anthropic
import httpx
import openai
import pytest
from volcenginesdkarkruntime import _exceptions as ark_exceptions

from juice_agents.core.agent.context.steps import ActionStep
from juice_agents.core.agent.context.content import CACHE_BOUNDARY_KEY
from juice_agents.core.agent.context.strategies import TreeOfThoughtsStrategy
from juice_agents.core.models.chat_models import BaseChatModel, ChatModel
from juice_agents.core.models.reward_models import BaseRewardModel, GenerativeRewardModel
from juice_agents.core.runner.execution.cancellation import StreamCancelled


_BACKEND_SDK = {
    "openai": ("openai", "OpenAI"),
    "doubao": ("volcenginesdkarkruntime", "Ark"),
    "anthropic": ("anthropic", "Anthropic"),
}


def _model_with_response(
    backend: str, content: str, **options,
) -> tuple[BaseChatModel, Mock]:
    """构造各 SDK 的真实响应形状，让生产适配器负责请求和响应转换。"""
    if backend == "anthropic":
        response = SimpleNamespace(
            role="assistant",
            content=[
                SimpleNamespace(type="thinking", thinking="analysis"),
                SimpleNamespace(type="text", text=content),
            ],
            usage={"input_tokens": 7, "output_tokens": 3},
        )
        create = Mock(return_value=response)
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
    else:
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                role="assistant", content=content, reasoning_content="analysis",
            ))],
            usage={"prompt_tokens": 7, "completion_tokens": 3},
        )
        create = Mock(return_value=response)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return ChatModel(
        api_base="https://example.test/v1", api_key="test-key", model_name="provider-judge",
        backend=backend, client=client, max_retry_times=options.pop("max_retry_times", 1),
        wait_time=0, **options,
    ), create


@pytest.mark.parametrize("backend", _BACKEND_SDK)
def test_chat_providers_generate_normalized_response_and_usage(
    backend: str,
) -> None:
    """统一聊天入口保留各 provider 请求字段和规范化的响应与 usage。"""
    model, create = _model_with_response(backend, "answer")
    messages = [{"role": "system", "content": "policy"}, {"role": "user", "content": "task"}]

    response = model.generate(messages, stop_sequence=["END"])

    raw_usage = (
        {"input_tokens": 7, "output_tokens": 3}
        if backend == "anthropic" else {"prompt_tokens": 7, "completion_tokens": 3}
    )
    assert response == {
        "role": "assistant", "content": "answer", "reasoning_content": "analysis",
        "usage": {
            "input_tokens": 7, "output_tokens": 3, "total_tokens": 10,
            "provider_usage": raw_usage,
        },
        "usage_calls": [{"backend": backend, "model_name": "provider-judge", "usage": {
            "input_tokens": 7, "output_tokens": 3, "total_tokens": 10,
            "provider_usage": raw_usage,
        }}],
    }
    create.assert_called_once()
    request = create.call_args.kwargs
    assert request["model"] == "provider-judge"
    if backend == "anthropic":
        assert request["system"] == "policy"
        assert request["messages"] == messages[1:]
        assert request["stop_sequences"] == ["END"]
    else:
        assert request["messages"] == messages
        assert request["stop"] == ["END"]


@pytest.mark.parametrize("backend", _BACKEND_SDK)
def test_chat_thinking_and_cache_boundaries_keep_provider_semantics(backend: str) -> None:
    """缓存标记仅转为 Anthropic cache_control，且请求转换不修改输入。"""
    thinking = {"type": "adaptive"} if backend == "anthropic" else {"type": "enabled", "effort": "high"}
    options = {"thinking": thinking}
    if backend == "anthropic":
        options["effort_config"] = {"effort": "high"}
    model, create = _model_with_response(backend, "answer", **options)
    messages = [
        {"role": "system", "content": [{"type": "text", "text": "policy", CACHE_BOUNDARY_KEY: True}]},
        {"role": "system", "content": "additional policy"},
        {"role": "user", "content": [{"type": "text", "text": "task"}]},
    ]
    original = copy.deepcopy(messages)

    model.generate(messages)

    request = create.call_args.kwargs
    if backend == "anthropic":
        assert request["thinking"] == thinking
        assert request["output_config"] == {"effort": "high"}
        assert request["max_tokens"] >= 16000
        assert request["system"] == [
            {"type": "text", "text": "policy", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "additional policy"},
        ]
        assert request["messages"] == messages[2:]
    else:
        expected = copy.deepcopy(messages)
        expected[0]["content"][0].pop(CACHE_BOUNDARY_KEY)
        assert request["messages"] == expected
        if backend == "openai":
            assert request["extra_body"]["thinking"] == thinking
            assert "thinking" not in request
        else:
            assert request["thinking"] == thinking
    assert messages == original


@pytest.mark.parametrize("backend", _BACKEND_SDK)
def test_chat_empty_response_retries_and_reports_exhaustion(backend: str) -> None:
    """统一入口保留空响应重试，达到上限后明确失败。"""
    model, create = _model_with_response(backend, "answer", max_retry_times=2)
    _, empty_create = _model_with_response(backend, " ")
    valid, empty = create.return_value, empty_create.return_value
    create.side_effect = [empty, valid]
    result = model.generate([{"role": "user", "content": "task"}])
    assert result["content"] == "answer"
    assert len(result["usage_calls"]) == 2
    assert all(call["usage"]["total_tokens"] == 10 for call in result["usage_calls"])
    assert create.call_count == 2

    create.side_effect = [empty, empty]
    with pytest.raises(RuntimeError, match="空内容") as error:
        model.generate([{"role": "user", "content": "task"}])
    assert create.call_count == 4
    assert len(error.value.usage_calls) == 2


@pytest.mark.parametrize("backend", ["doubao", "anthropic"])
def test_chat_remembers_sdk_thinking_fallback(backend: str) -> None:
    """SDK 拒绝 thinking 参数时只降级一次，后续请求沿用已确认的能力。"""
    model, create = _model_with_response(backend, "answer", thinking={"type": "enabled"})
    response = create.return_value
    create.side_effect = [TypeError("unexpected keyword argument 'thinking'"), response, response]

    for _ in range(2):
        result = model.generate([{"role": "user", "content": "task"}])
        assert result["content"] == "answer"
        assert len(result["usage_calls"]) == 1

    assert create.call_count == 3
    assert "thinking" in create.call_args_list[0].kwargs
    assert all("thinking" not in request.kwargs for request in create.call_args_list[1:])


class _ProviderStream:
    """可关闭的原生流；可在迭代开始时触发取消，无需定时或后台线程协调。"""

    def __init__(self, events: list, cancel_event: threading.Event | None = None) -> None:
        self.events = events
        self.cancel_event = cancel_event
        self.closed = False

    def __iter__(self):
        for event in self.events:
            if self.cancel_event is not None:
                self.cancel_event.set()
            yield event

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("backend", _BACKEND_SDK)
@pytest.mark.parametrize("cancelled", [False, True])
def test_chat_streaming_aggregates_or_closes_on_cancellation(backend: str, cancelled: bool) -> None:
    """保留各 SDK 的流式协议；用户取消时关闭流，不重试或回退阻塞调用。"""
    model, create = _model_with_response(backend, "unused", max_retry_times=3)
    cancel_event = threading.Event()
    if backend == "anthropic":
        events = [SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type=kind, **{field: value}))
                  for kind, field, value in [("thinking_delta", "thinking", "analysis"), ("text_delta", "text", "answer")]]
    else:
        events = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="answer", reasoning_content="analysis"))],
                                  usage={"prompt_tokens": 7, "completion_tokens": 3})]
    stream = _ProviderStream(events, cancel_event if cancelled else None)
    if backend == "anthropic":
        stream_method = Mock(return_value=stream)
        model.client.messages.stream = stream_method
    else:
        create.return_value = stream
        stream_method = create

    if cancelled:
        with pytest.raises(StreamCancelled) as error:
            model.generate([{"role": "user", "content": "task"}], cancel_event=cancel_event)
        assert stream.closed
        assert len(error.value.usage_calls) == 1
        if backend != "anthropic":
            assert error.value.usage_calls[0]["usage"]["total_tokens"] == 10
    else:
        response = model.generate([{"role": "user", "content": "task"}], cancel_event=cancel_event)
        assert response["content"] == "answer"
        assert response["reasoning_content"] == "analysis"
        assert len(response["usage_calls"]) == 1
        if backend != "anthropic":
            assert response["usage"]["total_tokens"] == 10
    stream_method.assert_called_once()
    if backend == "anthropic":
        create.assert_not_called()
    else:
        assert create.call_args.kwargs["stream"] is True


@pytest.mark.parametrize("backend", _BACKEND_SDK)
@pytest.mark.parametrize("custom_base", [False, True])
def test_chat_constructs_native_sdk_with_optional_base_url(
    monkeypatch: pytest.MonkeyPatch, backend: str, custom_base: bool,
) -> None:
    """省略 base URL 使用 SDK 默认地址，Anthropic 保持 bearer token 认证约定。"""
    reference, _create = _model_with_response(backend, "answer")
    constructor = Mock(return_value=reference.client)
    module_name, constructor_name = _BACKEND_SDK[backend]
    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(**{constructor_name: constructor}))
    options = {"api_base": "https://example.test/v1"} if custom_base else {}
    model = ChatModel("provider-judge", backend=backend, api_key="test-key", **options)

    assert isinstance(model, BaseChatModel)
    assert model.generate([{"role": "user", "content": "task"}])["content"] == "answer"
    expected = {"auth_token" if backend == "anthropic" else "api_key": "test-key"}
    if custom_base:
        expected["base_url"] = "https://example.test/v1"
    constructor.assert_called_once_with(**expected)


@pytest.mark.parametrize("kwargs", [
    {"model_name": " "}, {"model_name": None}, {"model_name": 1},
    {"backend": "unknown"}, {"backend": []},
    {"max_tokens": 0}, {"max_tokens": True}, {"max_tokens": 1.5},
    {"max_retry_times": 0}, {"max_retry_times": True}, {"max_retry_times": 1.5},
    {"wait_time": -1}, {"wait_time": float("inf")}, {"wait_time": float("nan")},
])
def test_chat_rejects_invalid_constructor_options(kwargs: dict) -> None:
    options = {"model_name": "provider-judge", **kwargs}
    with pytest.raises(ValueError):
        ChatModel(**options)


@pytest.mark.parametrize("thinking, error_type", [("enabled", TypeError), ({"type": "unknown"}, ValueError)])
def test_chat_rejects_invalid_thinking_before_call(thinking: object, error_type: type[Exception]) -> None:
    with pytest.raises(error_type):
        _model_with_response("openai", "answer", thinking=thinking)


def test_chat_rejects_non_mapping_effort_config() -> None:
    with pytest.raises(TypeError):
        _model_with_response("anthropic", "answer", effort_config="high")


def test_native_reward_sdk_and_strategy_select_using_provider_scores() -> None:
    """无需聊天模型或配置，贯通原生 SDK 响应、奖励评分和策略选择。"""
    create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content='{"scores": [1, 8.5]}'),
    )]))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    scorer: BaseRewardModel = GenerativeRewardModel("judge", client=client)
    assert isinstance(scorer, BaseRewardModel)
    strategy = TreeOfThoughtsStrategy(breadth=2, reward_model=scorer)
    context = [{"role": "user", "content": "Choose the accurate action."}]
    candidates = [ActionStep(step_num=1, model_output="guess"), ActionStep(step_num=1, model_output="verify")]
    create.assert_not_called()

    selected, scores = strategy.select(context=context, candidates=candidates)

    assert selected == 1
    assert scores == [1.0, 8.5]
    create.assert_called_once()
    messages = create.call_args.kwargs["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert json.loads(messages[1]["content"]) == {
        "context": context,
        "candidates": [
            {"role": "assistant", "content": "guess"},
            {"role": "assistant", "content": "verify"},
        ],
    }


@pytest.mark.parametrize("backend", _BACKEND_SDK)
def test_chat_network_failure_returns_unknown_usage_and_preserves_exception(backend: str) -> None:
    """失败的 SDK 请求也显式返回明细；成功重试不覆盖前一次失败。"""
    model, create = _model_with_response(backend, "answer", max_retry_times=2)
    response = create.return_value
    create.side_effect = [ConnectionError("unavailable"), response]
    result = model.generate([{"role": "user", "content": "task"}])
    assert len(result["usage_calls"]) == 2
    assert result["usage_calls"][0]["usage"] is None
    assert result["usage_calls"][1]["usage"]["total_tokens"] == 10

    failure = ConnectionError("still unavailable")
    create.side_effect = [failure, failure]
    with pytest.raises(ConnectionError) as error:
        model.generate([{"role": "user", "content": "task"}])
    assert error.value is failure
    assert [call["usage"] for call in error.value.usage_calls] == [None, None]


def test_chat_stream_options_fallback_returns_one_actual_request() -> None:
    """SDK 本地拒绝 stream_options 时，降级后的一个请求只返回一份明细。"""
    model, create = _model_with_response("openai", "unused")
    stream = _ProviderStream([SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="answer"))],
        usage={"prompt_tokens": 3, "completion_tokens": 2},
    )])
    create.side_effect = [TypeError("unexpected keyword argument 'stream_options'"), stream]
    result = model.generate([{"role": "user", "content": "task"}], cancel_event=threading.Event())
    assert result["content"] == "answer"
    assert len(result["usage_calls"]) == 1
    assert result["usage"]["total_tokens"] == 5
    assert create.call_count == 2


@pytest.mark.parametrize("final_message", [False, True])
def test_anthropic_stream_usage_merges_snapshots_and_sdk_final_message(final_message: bool) -> None:
    """原生 delta 的累计数覆盖旧值，官方 final message 也可补齐最终用量。"""
    model, create = _model_with_response("anthropic", "unused")
    initial_usage = {"input_tokens": 8, "output_tokens": 0, "cache_read_input_tokens": 3}
    stream = _ProviderStream([
        SimpleNamespace(type="message_start", message=SimpleNamespace(usage=initial_usage)),
        SimpleNamespace(type="message_delta", usage={"output_tokens": 2}),
        SimpleNamespace(type="message_delta", usage={"output_tokens": 5}),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="answer")),
    ])
    if final_message:
        stream.get_final_message = Mock(return_value=SimpleNamespace(usage=SimpleNamespace(
            input_tokens=8, output_tokens=5, cache_read_input_tokens=3,
        )))
    model.client.messages.stream = Mock(return_value=stream)

    result = model.generate([{"role": "user", "content": "task"}], cancel_event=threading.Event())

    assert result["usage"]["input_tokens"] == 8
    assert result["usage"]["output_tokens"] == 5
    assert result["usage"]["total_tokens"] == 13
    assert result["usage"]["provider_usage"]["cache_read_input_tokens"] == 3
    assert result["usage_calls"] == [{
        "backend": "anthropic", "model_name": "provider-judge", "usage": result["usage"],
    }]
    if final_message:
        stream.get_final_message.assert_called_once()
    create.assert_not_called()


def test_anthropic_cancelled_stream_exposes_received_usage_without_final_message() -> None:
    """取消保留已收到的快照，既不读取最终消息阻塞退出，也不吞掉取消异常。"""
    model, create = _model_with_response("anthropic", "unused")
    cancel_event = threading.Event()

    class CancellingStream(_ProviderStream):
        def __iter__(self):
            yield SimpleNamespace(type="message_start", message=SimpleNamespace(usage={"input_tokens": 8}))
            cancel_event.set()
            yield SimpleNamespace(type="message_delta", usage={"output_tokens": 4})

    stream = CancellingStream([])
    stream.get_final_message = Mock(side_effect=AssertionError("must not wait for final message"))
    model.client.messages.stream = Mock(return_value=stream)
    with pytest.raises(StreamCancelled) as error:
        model.generate([{"role": "user", "content": "task"}], cancel_event=cancel_event)

    assert stream.closed
    assert error.value.usage_calls[0]["usage"]["total_tokens"] == 12
    stream.get_final_message.assert_not_called()
    create.assert_not_called()


def test_shared_chat_model_returns_independent_usage_for_concurrent_calls() -> None:
    """同一模型被多个调用方并发复用时，每个结果只携带自己的请求明细。"""
    model, create = _model_with_response("openai", "unused")
    barrier = threading.Barrier(2)

    def respond(**request):
        content = request["messages"][0]["content"]
        barrier.wait(timeout=5)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage={"prompt_tokens": len(content), "completion_tokens": 1},
        )

    create.side_effect = respond
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(model.generate, [{"role": "user", "content": text}])
                 for text in ("first", "second request")]
        results = [task.result(timeout=10) for task in tasks]
    assert results[0]["usage_calls"] is not results[1]["usage_calls"]
    for result in results:
        assert len(result["usage_calls"]) == 1
        assert result["usage_calls"][0]["usage"]["input_tokens"] == len(result["content"])


def _anthropic_sse(text: str) -> bytes:
    """Messages 流的真实事件序列，交给 SDK 自己解析，不替换流式实现。"""
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-test",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 7, "output_tokens": 0},
        }}),
        ("content_block_start", {
            "type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text},
        }),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {
            "type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 3},
        }),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


@pytest.mark.parametrize("options", [
    {"effort_config": {"effort": "xhigh"}},
    {"effort_config": {"effort": "max"}},
    {"max_tokens": 22000},
])
def test_anthropic_high_effort_streams_without_cancel_event(options: dict) -> None:
    """xhigh 把 max_tokens 提到 64000，真实 SDK 拒绝这类非流式请求；

    压缩、目标评估等不带取消令牌的调用也必须走流式，否则直接失败。
    """
    requests: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_anthropic_sse("answer"),
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
        client = anthropic.Anthropic(
            api_key="test-key", base_url="https://example.test", max_retries=0, http_client=http_client,
        )
        model = ChatModel(
            "claude-test", backend="anthropic", client=client,
            max_retry_times=1, wait_time=0, **options,
        )
        model._watch_stream_cancel = Mock(side_effect=AssertionError("No cancellation watcher is needed"))
        result = model.generate([{"role": "user", "content": "task"}])

    assert result["content"] == "answer"
    assert result["usage"]["total_tokens"] == 10
    assert len(result["usage_calls"]) == 1
    expected_tokens = options.get("max_tokens", 64000)
    assert [(request["stream"], request["max_tokens"]) for request in requests] == [(True, expected_tokens)]


def test_anthropic_model_specific_nonstreaming_limit_uses_stream() -> None:
    """部分 Opus 的 SDK 非流式上限仅为 8192，16000 也需要流式。"""
    model, create = _model_with_response("anthropic", "unused", max_tokens=16000)
    model.model_name = "claude-opus-4-20250514"
    stream = _ProviderStream([
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="answer")),
    ])
    model.client.messages.stream = Mock(return_value=stream)

    assert model.generate([{"role": "user", "content": "task"}])["content"] == "answer"

    model.client.messages.stream.assert_called_once()
    create.assert_not_called()


@pytest.mark.parametrize("max_tokens", [4096, 8192])
def test_anthropic_low_budget_keeps_blocking_api_without_cancel_event(max_tokens: int) -> None:
    model, create = _model_with_response("anthropic", "answer", max_tokens=max_tokens)
    model.client.messages.stream = Mock(side_effect=AssertionError("A low-budget call can use create"))

    assert model.generate([{"role": "user", "content": "task"}])["content"] == "answer"

    create.assert_called_once()
    model.client.messages.stream.assert_not_called()


def test_anthropic_high_budget_stream_keeps_thinking_fallback_and_usage() -> None:
    """无取消令牌的流式入口也复用 thinking 降级，并只记录实际请求。"""
    model, create = _model_with_response(
        "anthropic", "unused", max_tokens=64000, thinking={"type": "adaptive"},
    )
    stream = _ProviderStream([
        SimpleNamespace(type="message_start", message=SimpleNamespace(usage={"input_tokens": 7})),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="analysis")),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="answer")),
        SimpleNamespace(type="message_delta", usage={"output_tokens": 3}),
    ])
    stream_method = Mock(side_effect=[TypeError("unexpected keyword argument 'thinking'"), stream, stream])
    model.client.messages.stream = stream_method

    for _ in range(2):
        result = model.generate([{"role": "user", "content": "task"}])
        assert result["content"] == "answer"
        assert result["reasoning_content"] == "analysis"
        assert result["usage"]["total_tokens"] == 10
        assert len(result["usage_calls"]) == 1

    assert "thinking" in stream_method.call_args_list[0].kwargs
    assert all("thinking" not in call.kwargs for call in stream_method.call_args_list[1:])
    create.assert_not_called()


def _provider_status_error(status: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://example.test/v1/messages")
    return anthropic.APIStatusError(
        f"Error code: {status}", response=httpx.Response(status, request=request), body=None,
    )


@pytest.mark.parametrize("status, retried", [
    (400, False),   # 请求参数错误
    (401, False),   # 凭证错误
    (None, False),  # 无法归类的异常
    (429, True),    # 限流
    (500, True),
    (529, True),    # Anthropic 过载
])
def test_chat_retries_only_transient_failures(status: int | None, retried: bool) -> None:
    """请求或凭证错误重试只会重复失败；只有限流和服务端错误值得再次请求。"""
    model, create = _model_with_response("anthropic", "answer", max_retry_times=2)
    error = RuntimeError("unclassified") if status is None else _provider_status_error(status)
    create.side_effect = [error, create.return_value]

    if retried:
        assert model.generate([{"role": "user", "content": "task"}])["content"] == "answer"
    else:
        with pytest.raises(type(error)) as raised:
            model.generate([{"role": "user", "content": "task"}])
        assert raised.value is error
    assert create.call_count == (2 if retried else 1)


@pytest.mark.parametrize("backend", _BACKEND_SDK)
@pytest.mark.parametrize("status, retried", [
    (400, False), (401, False), (403, False), (404, False), (422, False),
    (408, True), (409, True), (429, True), (500, True), (529, True),
])
def test_chat_retry_decision_uses_provider_http_status(backend: str, status: int, retried: bool) -> None:
    """错误正文可能包含重试关键词，实际 HTTP 状态才决定是否重试。"""
    model, create = _model_with_response(backend, "answer", max_retry_times=2)
    provider = {"openai": openai, "anthropic": anthropic, "doubao": ark_exceptions}[backend]
    error_type = getattr(provider, "ArkAPIStatusError" if backend == "doubao" else "APIStatusError")
    kwargs = {"request_id": "test-request"} if backend == "doubao" else {}
    request = httpx.Request("POST", "https://example.test")
    error = error_type(
        "connection timeout rate limit 429 503", response=httpx.Response(status, request=request), body=None, **kwargs,
    )
    create.side_effect = [error, create.return_value]

    if retried:
        assert model.generate([{"role": "user", "content": "task"}])["content"] == "answer"
    else:
        with pytest.raises(type(error)) as raised:
            model.generate([{"role": "user", "content": "task"}])
        assert raised.value is error
    assert create.call_count == (2 if retried else 1)


@pytest.mark.parametrize("backend", _BACKEND_SDK)
@pytest.mark.parametrize("timeout", [False, True])
def test_chat_retries_sdk_transport_failures(backend: str, timeout: bool) -> None:
    """各 SDK 传输异常并非内建 ConnectionError 子类，必须按其类型识别。"""
    model, create = _model_with_response(backend, "answer", max_retry_times=2)
    provider = {"openai": openai, "anthropic": anthropic, "doubao": ark_exceptions}[backend]
    name = "APITimeoutError" if timeout else "APIConnectionError"
    error_type = getattr(provider, "Ark" + name if backend == "doubao" else name)
    kwargs = {"request_id": "test-request"} if backend == "doubao" else {}
    create.side_effect = [error_type(request=httpx.Request("POST", "https://example.test"), **kwargs), create.return_value]

    result = model.generate([{"role": "user", "content": "task"}])

    assert result["content"] == "answer"
    assert len(result["usage_calls"]) == 2
    assert create.call_count == 2


def test_chat_does_not_retry_unknown_errors_with_transport_keywords() -> None:
    model, create = _model_with_response("anthropic", "answer", max_retry_times=2)
    error = RuntimeError("connection timeout rate limit 429 503")
    create.side_effect = [error, create.return_value]

    with pytest.raises(RuntimeError) as raised:
        model.generate([{"role": "user", "content": "task"}])

    assert raised.value is error
    assert create.call_count == 1
