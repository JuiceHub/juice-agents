import threading
import unittest

from juice_agents.core.models import AnthropicModel, DoubaoModel, OpenAIModel, _EmptyModelResponseError
from juice_agents.core.agent.sessions.content import CACHE_BOUNDARY_KEY
from juice_agents.core.runner.execution.cancellation import StreamCancelled


class FakeMessage:
    def __init__(self, role: str, content: str, reasoning_content: str | None = None):
        self.role = role
        self.content = content
        self.reasoning_content = reasoning_content


class FakeChoice:
    def __init__(self, message: FakeMessage):
        self.message = message


class FakeResponse:
    def __init__(self, message: FakeMessage, usage=None):
        self.choices = [FakeChoice(message)]
        self.usage = usage


class FakeCompletions:
    def __init__(self, response: FakeResponse, fail_times: int = 0):
        self.response = response
        self.fail_times = fail_times
        self.call_count = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.call_count += 1
        self.last_kwargs = kwargs
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient error")
        return self.response


class FakeDelta:
    def __init__(self, content: str = "", reasoning_content: str = ""):
        self.content = content
        self.reasoning_content = reasoning_content


class FakeStreamChoice:
    def __init__(self, delta: FakeDelta):
        self.delta = delta


class FakeStreamChunk:
    def __init__(self, content: str = "", reasoning_content: str = "", usage=None):
        self.choices = [FakeStreamChoice(FakeDelta(content, reasoning_content))]
        self.usage = usage


class FakeClosableStream:
    def __init__(self, chunks, cancel_event: threading.Event | None = None):
        self.chunks = list(chunks)
        self.cancel_event = cancel_event
        self.closed = False

    def __iter__(self):
        for idx, chunk in enumerate(self.chunks):
            if idx == 1 and self.cancel_event is not None:
                self.cancel_event.set()
            yield chunk

    def close(self):
        self.closed = True


class StreamingCompletions:
    def __init__(self, chunks, cancel_event: threading.Event | None = None):
        self.chunks = list(chunks)
        self.cancel_event = cancel_event
        self.last_stream: FakeClosableStream | None = None
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if kwargs.get("stream"):
            self.last_stream = FakeClosableStream(self.chunks, self.cancel_event)
            return self.last_stream
        return FakeResponse(FakeMessage("assistant", "blocking"))


class StreamingClient:
    def __init__(self, chunks, cancel_event: threading.Event | None = None):
        self.chat = FakeChat(StreamingCompletions(chunks, cancel_event=cancel_event))


class StreamOptionsUnsupportedCompletions:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.calls = []
        self.last_kwargs = None

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        self.last_kwargs = kwargs
        if "stream_options" in kwargs:
            raise TypeError("unexpected keyword argument 'stream_options'")
        if kwargs.get("stream"):
            return FakeClosableStream(self.chunks)
        return FakeResponse(FakeMessage("assistant", "blocking"))


class StreamOptionsUnsupportedClient:
    def __init__(self, chunks):
        self.completions = StreamOptionsUnsupportedCompletions(chunks)
        self.chat = FakeChat(self.completions)


class SequenceFakeCompletions:
    """按顺序返回响应或抛出异常，用于测试重试链路。"""

    def __init__(self, events):
        self.events = list(events)
        self.call_count = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.call_count += 1
        self.last_kwargs = kwargs
        if not self.events:
            raise AssertionError("sequence events exhausted")

        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


class FakeChat:
    def __init__(self, completions: FakeCompletions):
        self.completions = completions


class FakeClient:
    def __init__(self, response: FakeResponse, fail_times: int = 0):
        self.chat = FakeChat(FakeCompletions(response, fail_times=fail_times))


class SequenceFakeClient:
    def __init__(self, events):
        self.chat = FakeChat(SequenceFakeCompletions(events))


class FakeTypeErrorCompletions:
    def __init__(self):
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        raise TypeError("invalid payload")


class FakeTypeErrorClient:
    def __init__(self):
        self.completions = FakeTypeErrorCompletions()
        self.chat = FakeChat(self.completions)


class ThinkingUnsupportedCompletions:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.call_count = 0
        self.last_kwargs = None
        self.calls = []

    def create(self, **kwargs):
        self.call_count += 1
        self.last_kwargs = kwargs
        self.calls.append(dict(kwargs))
        if "thinking" in kwargs:
            raise TypeError("Completions.create() got an unexpected keyword argument 'thinking'")
        return self.response


class ThinkingUnsupportedClient:
    def __init__(self, response: FakeResponse):
        self.completions = ThinkingUnsupportedCompletions(response)
        self.chat = FakeChat(self.completions)


class OpenAIExtraBodyCompletions:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.call_count = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.call_count += 1
        self.last_kwargs = kwargs
        if "thinking" in kwargs:
            raise TypeError("Completions.create() got an unexpected keyword argument 'thinking'")
        return self.response


class OpenAIExtraBodyClient:
    def __init__(self, response: FakeResponse):
        self.completions = OpenAIExtraBodyCompletions(response)
        self.chat = FakeChat(self.completions)


class FakeAnthropicTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeAnthropicResponse:
    def __init__(self, text: str, role: str = "assistant", usage=None):
        self.role = role
        self.content = [FakeAnthropicTextBlock(text)]
        self.usage = usage


class FakeAnthropicMessages:
    def __init__(self, events):
        self.events = list(events)
        self.call_count = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.call_count += 1
        self.last_kwargs = kwargs
        if not self.events:
            raise AssertionError("anthropic events exhausted")
        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


class FakeAnthropicClient:
    def __init__(self, events):
        self.messages = FakeAnthropicMessages(events)


class DoubaoModelTests(unittest.TestCase):
    def test_generate_filters_empty_text_blocks_without_mutating_session_messages(self):
        client = FakeClient(FakeResponse(FakeMessage(role="assistant", content="ok")))
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": ""},
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": "  \n"},
                ],
            }
        ]

        model.generate(messages=messages)

        self.assertEqual(
            client.chat.completions.last_kwargs["messages"][0]["content"],
            [{"type": "text", "text": "hello"}],
        )
        self.assertEqual(len(messages[0]["content"]), 3)

    def test_generate_rejects_message_left_empty_after_filter_with_indexes(self):
        client = FakeClient(FakeResponse(FakeMessage(role="assistant", content="ok")))
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
        )

        with self.assertRaisesRegex(
            ValueError,
            r"messages\[0\].*empty_block_indexes=\[0,1\]",
        ):
            model.generate(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": ""},
                            {"type": "text", "text": "\n"},
                        ],
                    }
                ]
            )
        self.assertEqual(client.chat.completions.call_count, 0)

    def test_generate_raises_when_cancelled_before_call(self):
        cancel_event = threading.Event()
        cancel_event.set()
        client = FakeClient(FakeResponse(FakeMessage(role="assistant", content="ok")))
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
        )

        with self.assertRaises(StreamCancelled):
            model.generate(messages=[{"role": "user", "content": "hi"}], cancel_event=cancel_event)
        self.assertEqual(client.chat.completions.call_count, 0)

    def test_generate_streaming_cancel_closes_stream(self):
        cancel_event = threading.Event()
        client = StreamingClient(
            [FakeStreamChunk("hello"), FakeStreamChunk(" world")],
            cancel_event=cancel_event,
        )
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
        )

        with self.assertRaises(StreamCancelled):
            model.generate(messages=[{"role": "user", "content": "hi"}], cancel_event=cancel_event)

        self.assertTrue(client.chat.completions.last_kwargs["stream"])
        self.assertIsNotNone(client.chat.completions.last_stream)
        assert client.chat.completions.last_stream is not None
        self.assertTrue(client.chat.completions.last_stream.closed)

    def test_generate_streaming_aggregates_chunks(self):
        cancel_event = threading.Event()
        client = StreamingClient(
            [
                FakeStreamChunk("hello"),
                FakeStreamChunk(" world", "why", usage={"prompt_tokens": 3, "completion_tokens": 4}),
            ]
        )
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}], cancel_event=cancel_event)

        self.assertEqual(result["content"], "hello world")
        self.assertEqual(result["reasoning_content"], "why")
        self.assertEqual(result["usage"]["input_tokens"], 3)
        self.assertEqual(result["usage"]["output_tokens"], 4)
        self.assertEqual(result["usage"]["total_tokens"], 7)
        self.assertEqual(result["usage"]["provider_usage"]["prompt_tokens"], 3)

    def test_generate_streaming_retries_without_stream_options_when_unsupported(self):
        cancel_event = threading.Event()
        client = StreamOptionsUnsupportedClient(
            [FakeStreamChunk("ok", usage={"prompt_tokens": 2, "completion_tokens": 3})]
        )
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}], cancel_event=cancel_event)

        self.assertEqual(result["content"], "ok")
        self.assertEqual(result["usage"]["total_tokens"], 5)
        self.assertIn("stream_options", client.completions.calls[0])
        self.assertNotIn("stream_options", client.completions.calls[1])

    def test_cancel_watcher_closes_stream_when_cancel_event_is_set(self):
        cancel_event = threading.Event()
        stream = FakeClosableStream([])
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=StreamingClient([]),
        )

        stop_watching = model._watch_stream_cancel(stream, cancel_event)
        cancel_event.set()

        self.assertTrue(cancel_event.wait(1.0))
        for _ in range(20):
            if stream.closed:
                break
            threading.Event().wait(0.01)
        stop_watching()
        self.assertTrue(stream.closed)

    def test_generate_returns_reasoning_separately_and_disables_thinking_by_default(self):
        response = FakeResponse(
            FakeMessage(
                role="assistant",
                content="回答",
                reasoning_content="深入思考",
            ),
            usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        )
        client = FakeClient(response)
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence=["stop"])

        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["content"], "回答")
        self.assertEqual(result["reasoning_content"], "深入思考")
        self.assertEqual(result["usage"]["input_tokens"], 11)
        self.assertEqual(result["usage"]["output_tokens"], 7)
        self.assertEqual(result["usage"]["total_tokens"], 18)
        self.assertEqual(result["usage"]["provider_usage"]["completion_tokens"], 7)
        self.assertEqual(
            client.chat.completions.last_kwargs["stop"],
            ["stop"],
        )
        self.assertEqual(client.chat.completions.last_kwargs["thinking"], {"type": "disabled"})

    def test_generate_respects_explicit_thinking_override(self):
        response = FakeResponse(FakeMessage(role="assistant", content="回答"))
        client = FakeClient(response)
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            thinking={"type": "enabled"},
            client=client,
        )

        model.generate(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(client.chat.completions.last_kwargs["thinking"], {"type": "enabled"})

    def test_retry_on_failure_and_return_message(self):
        response = FakeResponse(FakeMessage(role="assistant", content="ok"))
        client = FakeClient(response, fail_times=1)
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            wait_time=0,
            max_retry_times=2,
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence=None)

        self.assertEqual(result["content"], "ok")
        self.assertEqual(client.chat.completions.call_count, 2)

    def test_retry_on_empty_content_and_return_message(self):
        empty_response = FakeResponse(FakeMessage(role="assistant", content=""))
        ok_response = FakeResponse(FakeMessage(role="assistant", content="ok"))
        client = SequenceFakeClient([empty_response, ok_response])
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            wait_time=0,
            max_retry_times=2,
            client=client,
        )

        with self.assertLogs("juice_agents.core.models", level="WARNING") as logs:
            result = model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence=None)

        self.assertEqual(result["content"], "ok")
        self.assertEqual(client.chat.completions.call_count, 2)
        self.assertTrue(any("返回空内容" in message for message in logs.output))

    def test_retry_on_whitespace_only_content_and_return_message(self):
        whitespace_response = FakeResponse(FakeMessage(role="assistant", content=" \n\t "))
        ok_response = FakeResponse(FakeMessage(role="assistant", content="ok"))
        client = SequenceFakeClient([whitespace_response, ok_response])
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            wait_time=0,
            max_retry_times=2,
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence=None)

        self.assertEqual(result["content"], "ok")
        self.assertEqual(client.chat.completions.call_count, 2)

    def test_retry_loop_handles_runtime_error_then_empty_content_then_success(self):
        """
        生命周期重构后，瞬时异常和空内容校验仍应走同一条公共重试链路。
        """
        empty_response = FakeResponse(FakeMessage(role="assistant", content=""))
        ok_response = FakeResponse(FakeMessage(role="assistant", content="ok"))
        client = SequenceFakeClient([RuntimeError("transient error"), empty_response, ok_response])
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            wait_time=0,
            max_retry_times=3,
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence=None)

        self.assertEqual(result["content"], "ok")
        self.assertEqual(client.chat.completions.call_count, 3)

    def test_raise_empty_response_error_when_all_attempts_are_empty(self):
        empty_response = FakeResponse(FakeMessage(role="assistant", content=""))
        whitespace_response = FakeResponse(FakeMessage(role="assistant", content=" \n\t "))
        client = SequenceFakeClient([empty_response, whitespace_response])
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            wait_time=0,
            max_retry_times=2,
            client=client,
        )

        with self.assertRaisesRegex(_EmptyModelResponseError, "空内容"):
            model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence=None)
        self.assertEqual(client.chat.completions.call_count, 2)

    def test_reasoning_content_does_not_bypass_empty_content_validation(self):
        response = FakeResponse(FakeMessage(role="assistant", content="", reasoning_content="只有思考"))
        client = SequenceFakeClient([response, response])
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            wait_time=0,
            max_retry_times=2,
            client=client,
        )

        with self.assertRaisesRegex(_EmptyModelResponseError, "空内容"):
            model.generate(messages=[{"role": "user", "content": "hi"}])

    def test_generate_invalid_messages_type(self):
        client = FakeClient(FakeResponse(FakeMessage(role="assistant", content="ok")))
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
        )

        with self.assertRaises(TypeError):
            model.generate(messages="not-a-list")
        with self.assertRaises(TypeError):
            model.generate(messages=[1, 2, 3])

    def test_generate_invalid_stop_sequence(self):
        client = FakeClient(FakeResponse(FakeMessage(role="assistant", content="ok")))
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
        )

        with self.assertRaises(TypeError):
            model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence="stop")
        with self.assertRaises(TypeError):
            model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence=[1, 2])

    def test_non_retryable_exception(self):
        client = FakeTypeErrorClient()
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
            wait_time=0,
            max_retry_times=3,
        )

        with self.assertRaises(TypeError):
            model.generate(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(client.completions.call_count, 1)

    def test_generate_falls_back_when_client_does_not_accept_thinking(self):
        response = FakeResponse(FakeMessage(role="assistant", content="ok"))
        client = ThinkingUnsupportedClient(response)
        model = DoubaoModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="doubao",
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(result["content"], "ok")
        self.assertEqual(client.completions.call_count, 2)
        self.assertIn("thinking", client.completions.calls[0])
        self.assertNotIn("thinking", client.completions.calls[1])

        model.generate(messages=[{"role": "user", "content": "hi again"}])

        self.assertEqual(client.completions.call_count, 3)
        self.assertNotIn("thinking", client.completions.calls[2])


class OpenAIModelTests(unittest.TestCase):
    def test_generate_returns_reasoning_separately(self):
        response = FakeResponse(
            FakeMessage(
                role="assistant",
                content="回答",
                reasoning_content="深入思考",
            ),
            usage={"input_tokens": 5, "output_tokens": 6},
        )
        client = FakeClient(response)
        model = OpenAIModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="gpt-4o",
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence=["stop"])

        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["content"], "回答")
        self.assertEqual(result["reasoning_content"], "深入思考")
        self.assertEqual(result["usage"]["input_tokens"], 5)
        self.assertEqual(result["usage"]["output_tokens"], 6)
        self.assertEqual(result["usage"]["total_tokens"], 11)
        self.assertEqual(
            client.chat.completions.last_kwargs["stop"],
            ["stop"],
        )
        self.assertNotIn("thinking", client.chat.completions.last_kwargs)
        self.assertEqual(
            client.chat.completions.last_kwargs["extra_body"]["thinking"],
            {"type": "disabled"},
        )

    def test_generate_respects_explicit_thinking_override_via_extra_body(self):
        response = FakeResponse(FakeMessage(role="assistant", content="回答"))
        client = FakeClient(response)
        model = OpenAIModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="gpt-4o",
            thinking={"type": "enabled"},
            client=client,
        )

        model.generate(messages=[{"role": "user", "content": "hi"}])

        self.assertNotIn("thinking", client.chat.completions.last_kwargs)
        self.assertEqual(
            client.chat.completions.last_kwargs["extra_body"]["thinking"],
            {"type": "enabled"},
        )

    def test_retry_on_failure_and_return_message(self):
        response = FakeResponse(FakeMessage(role="assistant", content="ok"))
        client = FakeClient(response, fail_times=1)
        model = OpenAIModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="gpt-4o",
            wait_time=0,
            max_retry_times=2,
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence=None)

        self.assertEqual(result["content"], "ok")
        self.assertEqual(client.chat.completions.call_count, 2)

    def test_retry_on_empty_content_and_return_message(self):
        empty_response = FakeResponse(FakeMessage(role="assistant", content=""))
        ok_response = FakeResponse(FakeMessage(role="assistant", content="ok"))
        client = SequenceFakeClient([empty_response, ok_response])
        model = OpenAIModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="gpt-4o",
            wait_time=0,
            max_retry_times=2,
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}], stop_sequence=None)

        self.assertEqual(result["content"], "ok")
        self.assertEqual(client.chat.completions.call_count, 2)

    def test_generate_uses_extra_body_for_openai_sdk_compatible_client(self):
        response = FakeResponse(FakeMessage(role="assistant", content="ok"))
        client = OpenAIExtraBodyClient(response)
        model = OpenAIModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="gpt-4o",
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(result["content"], "ok")
        self.assertEqual(client.chat.completions.call_count, 1)
        self.assertNotIn("thinking", client.chat.completions.last_kwargs)
        self.assertEqual(
            client.chat.completions.last_kwargs["extra_body"]["thinking"],
            {"type": "disabled"},
        )


class AnthropicModelTests(unittest.TestCase):
    def test_generate_uses_messages_create_with_system_and_stop_sequences(self):
        client = FakeAnthropicClient([FakeAnthropicResponse("回答", usage={"input_tokens": 13, "output_tokens": 8})])
        model = AnthropicModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="claude-opus-4-7",
            client=client,
        )

        result = model.generate(
            messages=[
                {"role": "system", "content": [{"type": "text", "text": "系统约束"}]},
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            ],
            stop_sequence=["</actions>"],
        )

        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["content"], "回答")
        self.assertEqual(result["reasoning_content"], "")
        self.assertEqual(result["usage"]["input_tokens"], 13)
        self.assertEqual(result["usage"]["output_tokens"], 8)
        self.assertEqual(result["usage"]["total_tokens"], 21)
        self.assertEqual(client.messages.call_count, 1)
        self.assertEqual(
            client.messages.last_kwargs,
            {
                "model": "claude-opus-4-7",
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "hi"}],
                    }
                ],
                "system": "系统约束",
                "stop_sequences": ["</actions>"],
            },
        )

    def test_generate_merges_multiple_text_blocks_from_response(self):
        response = type(
            "AnthropicResponse",
            (),
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": " world"},
                ],
            },
        )()
        client = FakeAnthropicClient([response])
        model = AnthropicModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="claude-sonnet-4-6",
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(result["content"], "hello world")

    def test_retry_on_empty_content_and_return_message(self):
        client = FakeAnthropicClient([FakeAnthropicResponse(""), FakeAnthropicResponse("ok")])
        model = AnthropicModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="claude-haiku-4-5-20251001",
            wait_time=0,
            max_retry_times=2,
            client=client,
        )

        result = model.generate(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(result["content"], "ok")
        self.assertEqual(client.messages.call_count, 2)

    def test_build_payload_with_effort_config_injects_output_config_and_adaptive_thinking(self):
        client = FakeAnthropicClient([FakeAnthropicResponse("ok")])
        model = AnthropicModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="claude-opus-4-7",
            thinking={"type": "adaptive"},
            effort_config={"effort": "medium"},
            client=client,
        )

        model.generate(messages=[{"role": "user", "content": "hi"}])

        kwargs = client.messages.last_kwargs
        self.assertEqual(kwargs["thinking"], {"type": "adaptive"})
        self.assertEqual(kwargs["output_config"], {"effort": "medium"})
        # medium effort 自动提升 max_tokens 至 8192
        self.assertEqual(kwargs["max_tokens"], 8192)

    def test_build_payload_disabled_effort_no_thinking_no_output_config(self):
        client = FakeAnthropicClient([FakeAnthropicResponse("ok")])
        model = AnthropicModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="claude-opus-4-7",
            thinking={"type": "disabled"},
            effort_config=None,
            client=client,
        )

        model.generate(messages=[{"role": "user", "content": "hi"}])

        kwargs = client.messages.last_kwargs
        self.assertNotIn("thinking", kwargs)
        self.assertNotIn("output_config", kwargs)
        self.assertEqual(kwargs["max_tokens"], 4096)

    def test_xhigh_effort_increases_max_tokens(self):
        client = FakeAnthropicClient([FakeAnthropicResponse("ok")])
        model = AnthropicModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="claude-opus-4-7",
            thinking={"type": "adaptive"},
            effort_config={"effort": "xhigh"},
            client=client,
        )

        model.generate(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(client.messages.last_kwargs["max_tokens"], 64000)

    def test_cache_boundary_translated_to_anthropic_cache_control(self):
        client = FakeAnthropicClient([FakeAnthropicResponse("ok")])
        model = AnthropicModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="claude-opus-4-7",
            client=client,
        )

        model.generate(
            messages=[
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "静态前缀", CACHE_BOUNDARY_KEY: True},
                        {"type": "text", "text": "动态后缀"},
                    ],
                },
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            ],
        )

        system_field = client.messages.last_kwargs["system"]
        self.assertEqual(
            system_field,
            [
                {"type": "text", "text": "静态前缀", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "动态后缀"},
            ],
        )
        # 内部标记字段绝不能透传到出站 payload
        for block in system_field:
            self.assertNotIn(CACHE_BOUNDARY_KEY, block)

    def test_system_without_boundary_stays_plain_string(self):
        client = FakeAnthropicClient([FakeAnthropicResponse("ok")])
        model = AnthropicModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="claude-opus-4-7",
            client=client,
        )

        model.generate(
            messages=[
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "静态"},
                        {"type": "text", "text": "动态"},
                    ],
                },
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            ],
        )

        # 无缓存边界时保持与历史一致的纯字符串 system，不引入 block 列表
        self.assertEqual(client.messages.last_kwargs["system"], "静态动态")


class CacheBoundaryStrippingTests(unittest.TestCase):
    """非 Anthropic provider 必须丢弃内部缓存标记，避免 SDK 参数校验失败。"""

    def test_openai_strips_cache_boundary_from_messages(self):
        client = FakeClient(FakeResponse(FakeMessage("assistant", "ok")))
        model = OpenAIModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="gpt-x",
            client=client,
        )

        payload = model._build_payload(
            [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "前缀", CACHE_BOUNDARY_KEY: True},
                        {"type": "text", "text": "后缀"},
                    ],
                },
                {"role": "user", "content": "hi"},
            ],
            None,
        )

        for message in payload["messages"]:
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    self.assertNotIn(CACHE_BOUNDARY_KEY, block)
        # 文本内容本身保持不变
        self.assertEqual(payload["messages"][0]["content"][0]["text"], "前缀")

    def test_strip_is_noop_without_boundary(self):
        model = OpenAIModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="gpt-x",
            client=FakeClient(FakeResponse(FakeMessage("assistant", "ok"))),
        )
        messages = [{"role": "user", "content": "hi"}]
        # 无标记时应原样返回同一对象，不做多余拷贝
        self.assertIs(model._strip_cache_boundary(messages)[0], messages[0])


    def test_explicit_large_max_tokens_not_reduced_by_effort(self):
        client = FakeAnthropicClient([FakeAnthropicResponse("ok")])
        model = AnthropicModel(
            api_base="https://example.com",
            api_key="dummy",
            model_name="claude-opus-4-7",
            thinking={"type": "adaptive"},
            effort_config={"effort": "low"},
            max_tokens=100000,
            client=client,
        )

        model.generate(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(client.messages.last_kwargs["max_tokens"], 100000)


if __name__ == "__main__":
    unittest.main()
