"""真实 SDK 的取消回归：服务器停住响应，客户端仍须自行退出。

这些测试刻意使用本地 TCP，而不是可立即返回的 MockTransport/假迭代器：
只有真实的 socket 读取才能覆盖“关闭流但阻塞 recv 没被唤醒”的问题。
"""

from __future__ import annotations

import asyncio
import json
import select
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

import pytest

from juice_agents.core.managers.models import ModelManager
from juice_agents.core.runner.execution.async_bridge import run_async
from juice_agents.core.models.chat_models import ChatModel
from juice_agents.core.models.reward_models import GenerativeRewardModel
from juice_agents.core.runner.execution.cancellation import StreamCancelled


_BACKENDS = ("openai", "anthropic", "doubao")
_CANCEL_TIMEOUT_SECONDS = 2.0


@pytest.fixture(autouse=True)
def _bypass_proxy_for_local_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试始终直连回环地址，不让开发环境 HTTP 代理接管本地请求。"""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def _sse_event(data: dict[str, Any], event: str | None = None) -> bytes:
    prefix = f"event: {event}\n" if event is not None else ""
    return f"{prefix}data: {json.dumps(data)}\n\n".encode()


def _response_parts(backend: str) -> tuple[bytes, bytes]:
    """提供 SDK 可解析的原生 SSE；停顿点放在响应头或后续正文之前。"""
    if backend == "anthropic":
        initial = b"".join(
            _sse_event(payload, payload["type"])
            for payload in [
                {"type": "message_start", "message": {
                    "id": "msg_local", "type": "message", "role": "assistant",
                    "model": "test-model", "content": [], "stop_reason": None,
                    "stop_sequence": None, "usage": {"input_tokens": 7, "output_tokens": 1},
                }},
                {"type": "content_block_start", "index": 0,
                 "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": "answer"}},
            ]
        )
        final = b"".join(
            _sse_event(payload, payload["type"])
            for payload in [
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {
                    "stop_reason": "end_turn", "stop_sequence": None,
                }, "usage": {"output_tokens": 3}},
                {"type": "message_stop"},
            ]
        )
        return initial, final
    chunk = {"id": "chatcmpl_local", "object": "chat.completion.chunk",
             "created": 0, "model": "test-model"}
    initial = _sse_event({**chunk, "choices": [{
        "index": 0, "delta": {"role": "assistant", "content": "answer"},
        "finish_reason": None,
    }], "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8}})
    final = _sse_event({**chunk, "choices": [{
        "index": 0, "delta": {}, "finish_reason": "stop",
    }], "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})
    return initial, final + b"data: [DONE]\n\n"


@dataclass
class _ResponseControl:
    """服务器不自行超时释放，确保取消测试不会误把服务器结束当作修复。"""

    requested: threading.Event = field(default_factory=threading.Event)
    body_started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    peer_closed: threading.Event = field(default_factory=threading.Event)
    request_count: int = 0


@contextmanager
def _stalled_server(
    backend: str, phase: str, prompts: tuple[str, ...] = ("cancelled",),
) -> Iterator[tuple[str, dict[str, _ResponseControl]]]:
    """为每个请求分别控制响应，finally 回收请求线程和监听线程。"""
    controls = {prompt: _ResponseControl() for prompt in prompts}
    initial, final = _response_parts(backend)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            pass

        def wait_for_release(self, control: _ResponseControl) -> None:
            """只观察 EOF，不替客户端关闭连接，也不提前结束服务器响应。"""
            while not control.release.wait(0.01):
                if control.peer_closed.is_set():
                    continue
                readable, _, _ = select.select([self.connection], [], [], 0)
                if readable:
                    try:
                        closed = not self.connection.recv(1, socket.MSG_PEEK)
                    except ConnectionResetError:
                        closed = True
                    if closed:
                        control.peer_closed.set()

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            content = payload["messages"][-1]["content"]
            prompt = content if isinstance(content, str) else content[-1]["text"]
            # GRM 会把用户内容包装成评分 JSON；单请求测试无需依赖其提示词格式。
            control = controls[prompt] if prompt in controls else controls[prompts[0]]
            control.request_count += 1
            control.requested.set()
            try:
                if phase == "retry":
                    body = json.dumps({"error": {
                        "type": "rate_limit_error", "message": "retry later",
                    }}).encode()
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Retry-After", "60")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                    return
                if phase == "headers":
                    self.wait_for_release(control)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(initial)
                self.wfile.flush()
                control.body_started.set()
                if phase == "body":
                    self.wait_for_release(control)
                self.wfile.write(final)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # 取消必须关闭客户端连接，服务器在 finally 释放时可能发现断开。
                pass
            finally:
                self.close_connection = True
                control.finished.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01),
        name="model-cancel-test-server",
    )
    serving.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", controls
    finally:
        for control in controls.values():
            control.release.set()
        server.shutdown()
        server.server_close()  # ThreadingMixIn 在这里等待全部 handler 退出。
        serving.join(timeout=3)
        assert not serving.is_alive(), "本地测试服务器未退出"


@dataclass
class _Generation:
    cancel: threading.Event = field(default_factory=threading.Event)
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    thread: threading.Thread = field(init=False)

    def start(self, model: ChatModel | GenerativeRewardModel, prompt: str) -> None:
        def run() -> None:
            try:
                if isinstance(model, GenerativeRewardModel):
                    result = ModelManager.invoke_sync(
                        model, "evaluate_batch", purpose="scoring", cancel_event=self.cancel,
                        kwargs={"context": [{"role": "user", "content": prompt}],
                                "actions": [{"role": "assistant", "content": "candidate"}]},
                    )
                else:
                    result = ModelManager.invoke_sync(
                        model, "generate", purpose="generation", cancel_event=self.cancel,
                        args=([{"role": "user", "content": prompt}],),
                    )
                self.results.append(result)
            except Exception as exc:
                self.errors.append(exc)

        self.thread = threading.Thread(target=run, name=f"model-cancel-test-{prompt}")
        self.thread.start()

    def cleanup(self) -> None:
        self.cancel.set()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "模型调用线程未退出"


@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize("phase", ("headers", "body"))
@pytest.mark.parametrize("model_kind", ("chat", "reward"))
def test_generate_cancels_stalled_http_request(
    backend: str, phase: str, model_kind: str,
) -> None:
    """响应头未到达、或正文中途停顿时，取消都不能依赖服务器恢复发送。"""
    with _stalled_server(backend, phase) as (api_base, controls):
        options = {"backend": backend, "api_base": api_base, "api_key": "test-key"}
        model = (
            ChatModel("test-model", **options, max_retry_times=3, wait_time=0)
            if model_kind == "chat" else GenerativeRewardModel("test-model", **options)
        )
        call = _Generation()
        control = controls["cancelled"]
        call.start(model, "cancelled")
        try:
            assert control.requested.wait(5), "真实 SDK 没有发出 HTTP 请求"
            if phase == "body":
                assert control.body_started.wait(5), "服务器没有开始发送 SSE"
                # 首块已发送后，给 SDK 消费它并进入下一次 socket 读取的机会。
                # 下方还会检查已消费的 usage，防止取消只命中了流建立阶段。
                threading.Event().wait(0.1)
            call.cancel.set()
            call.thread.join(timeout=_CANCEL_TIMEOUT_SECONDS)
            assert not control.release.is_set()
            assert not control.finished.is_set(), "服务器提前结束，未覆盖阻塞中的取消"
            assert not call.thread.is_alive(), f"{backend} 在等待 {phase} 时没有响应取消"
            assert control.peer_closed.wait(1), "取消返回后，服务器仍未观察到客户端断开连接"
            assert not call.results
            assert len(call.errors) == 1
            assert isinstance(call.errors[0], StreamCancelled)
            assert len(call.errors[0].usage_calls) == 1
            assert control.request_count == 1, "取消不应触发 SDK 或模型重试"
            if phase == "body":
                assert call.errors[0].usage_calls[0]["usage"]["total_tokens"] == 8
        finally:
            control.release.set()
            try:
                call.cleanup()
            finally:
                client = model.client if isinstance(model, ChatModel) else model._client
                if client is not None:
                    run_async(model.aclose(), owner=model)


@pytest.mark.parametrize("backend", _BACKENDS)
def test_cancelling_one_request_preserves_other_request_on_shared_model(backend: str) -> None:
    """同一客户端并发请求只取消指定请求，另一个仍能接收最终正文和 usage。"""
    with _stalled_server(backend, "body", ("cancelled", "survivor")) as (api_base, controls):
        model = ChatModel(
            "test-model", backend=backend, api_base=api_base, api_key="test-key",
            max_retry_times=1, wait_time=0,
        )
        cancelled, survivor = _Generation(), _Generation()
        cancelled.start(model, "cancelled")
        survivor.start(model, "survivor")
        try:
            assert all(control.body_started.wait(5) for control in controls.values())
            threading.Event().wait(0.1)
            cancelled.cancel.set()
            cancelled.thread.join(timeout=_CANCEL_TIMEOUT_SECONDS)
            assert not cancelled.thread.is_alive(), "被取消的请求仍卡在正文读取"
            assert len(cancelled.errors) == 1
            assert isinstance(cancelled.errors[0], StreamCancelled)
            assert survivor.thread.is_alive(), "取消错误地结束了共享客户端的另一个请求"
            assert not survivor.cancel.is_set()
            controls["survivor"].release.set()
            survivor.thread.join(timeout=5)
            assert not survivor.thread.is_alive()
            assert not survivor.errors
            assert survivor.results[0]["content"] == "answer"
            assert survivor.results[0]["usage"]["total_tokens"] == 10
            assert len(survivor.results[0]["usage_calls"]) == 1
        finally:
            for control in controls.values():
                control.release.set()
            try:
                cancelled.cleanup()
            finally:
                try:
                    survivor.cleanup()
                finally:
                    run_async(model.aclose(), owner=model)


@pytest.mark.parametrize("backend", _BACKENDS)
def test_cancel_interrupts_sdk_retry_delay(backend: str) -> None:
    """SDK 遵循 429 Retry-After 进行长时间退避时，取消仍须及时退出。"""
    with _stalled_server(backend, "retry") as (api_base, controls):
        model = ChatModel(
            "test-model", backend=backend, api_base=api_base, api_key="test-key",
            max_retry_times=3, wait_time=60,
        )
        call = _Generation()
        control = controls["cancelled"]
        call.start(model, "cancelled")
        try:
            assert control.finished.wait(5), "服务器没有完成 429 响应"
            # 让 SDK 处理完 429，取消应覆盖重试等待而不只是响应读取。
            threading.Event().wait(0.1)
            assert call.thread.is_alive(), "模型没有等待下一次重试"
            call.cancel.set()
            call.thread.join(timeout=_CANCEL_TIMEOUT_SECONDS)
            assert not call.thread.is_alive(), "取消仍在等待 SDK 或模型的重试延迟"
            assert not call.results
            assert len(call.errors) == 1
            assert isinstance(call.errors[0], StreamCancelled)
            assert control.request_count == 1
            assert len(call.errors[0].usage_calls) == 1
        finally:
            try:
                call.cleanup()
            finally:
                run_async(model.aclose(), owner=model)


@pytest.mark.anyio
@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize("phase", ("headers", "body", "retry"))
@pytest.mark.parametrize("model_kind", ("chat", "reward"))
async def test_native_task_cancel_closes_real_sdk_request(backend, phase, model_kind):
    """不经过同步桥接，直接 Task.cancel() 也必须打断 SDK 并确认 socket 关闭。"""
    async def wait_until(predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.01)

    with _stalled_server(backend, phase) as (api_base, controls):
        options = {"backend": backend, "api_base": api_base, "api_key": "test-key"}
        model = (ChatModel("test-model", **options) if model_kind == "chat"
                 else GenerativeRewardModel("test-model", **options))
        control = controls["cancelled"]
        async with model:
            operation = (model.generate([{"role": "user", "content": "cancelled"}], stream=True)
                         if model_kind == "chat" else model.evaluate_batch(
                             context=[], actions=[{"content": "candidate"}], stream=True,
                         ))
            task = asyncio.create_task(operation)
            try:
                await wait_until(control.body_started.is_set if phase == "body" else control.requested.is_set)
                if phase in {"body", "retry"}:
                    await asyncio.sleep(0.1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError) as raised:
                    await asyncio.wait_for(task, 2)
                assert len(raised.value.usage_calls) == 1
                assert control.request_count == 1
                if phase != "retry":
                    await wait_until(control.peer_closed.is_set)
                    assert not control.release.is_set()
                if phase == "body":
                    assert raised.value.usage_calls[0]["usage"]["total_tokens"] == 8
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
