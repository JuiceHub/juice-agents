"""图片只在聊天请求出站时编码；真实 SDK 通过离线 HTTP transport 验证协议。"""

from __future__ import annotations

import base64
import asyncio
import copy
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from openai import AsyncOpenAI
from PIL import Image
from volcenginesdkarkruntime import Ark, AsyncArk

from juice_agents.core.agent import agent_type
from juice_agents.core.agent.agent_type import ObservationImage, image_to_api_source
from juice_agents.core.agent.context.content import CACHE_BOUNDARY_KEY
from juice_agents.core.agent.context.nodes import ContextNode
from juice_agents.core.agent.context.serialization import deserialize_context_node, serialize_context_node
from juice_agents.core.agent.context.steps import ActionStep, TaskStep
from juice_agents.core.agent.tools.builtin.images.images_tools import GenerateEditImageTool
from juice_agents.core.models.chat_models import ChatModel


pytestmark = pytest.mark.anyio


def _image_bytes(image_format: str) -> bytes:
    """生成很小但真实可解码的图片，避免依赖仓库或网络中的图片文件。"""

    output = io.BytesIO()
    Image.new("RGB", (3, 2), "#c43a56").save(output, format=image_format)
    return output.getvalue()


def _chat_response() -> httpx.Response:
    return httpx.Response(200, json={
        "id": "offline-chat", "object": "chat.completion", "created": 1,
        "model": "offline-vision",
        "choices": [{
            "index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "image received"},
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    })


@asynccontextmanager
async def _chat_model(backend: str, requests: list[httpx.Request]):
    """仅替换网络 transport，保留 provider SDK 的参数处理和 JSON 序列化。"""

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _chat_response()

    client_type = AsyncOpenAI if backend == "openai" else AsyncArk
    async with client_type(
        api_key="offline-image-transport-key", base_url="https://vision.test/api/v3",
        max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        yield ChatModel(
            model_name="offline-vision", backend=backend, client=client,
            max_retry_times=1, wait_time=0,
        )


def _image_urls(messages: list[dict]) -> list[str]:
    return [
        block["image_url"]["url"]
        for message in messages
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "image_url"
    ]


def _assert_image_source(source: str, image_format: str, expected_bytes: bytes) -> None:
    """同时核对协议 MIME、解码结果和原始字节，发现错误后缀或 PNG 重编码。"""

    prefix, encoded = source.split(",", 1)
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[image_format]
    assert prefix == f"data:{mime};base64"
    decoded_bytes = base64.b64decode(encoded, validate=True)
    assert decoded_bytes == expected_bytes
    with Image.open(io.BytesIO(decoded_bytes)) as decoded:
        assert decoded.format == image_format
        assert decoded.size == (3, 2)


@pytest.mark.parametrize("backend", ["openai", "doubao"])
@pytest.mark.parametrize("image_format,suffix", [("PNG", ".png"), ("JPEG", ".png"), ("WEBP", ".webp")])
async def test_local_images_use_actual_mime_without_mutating_messages(tmp_path, backend, image_format, suffix):
    """真实 HTTP 请求包含图片数据；真实格式优先于扩展名，detail 和缓存语义保留。"""

    data = _image_bytes(image_format)
    path = tmp_path / f"source{suffix}"
    path.write_bytes(data)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "Describe this image."},
        {"type": "image_url", "image_url": {"url": str(path), "detail": "high"}, CACHE_BOUNDARY_KEY: True},
    ]}]
    original = copy.deepcopy(messages)
    requests = []

    async with _chat_model(backend, requests) as model:
        assert (await model.generate(messages))["content"] == "image received"

    assert len(requests) == 1
    assert requests[0].url.path == "/api/v3/chat/completions"
    sent_messages = json.loads(requests[0].content)["messages"]
    _assert_image_source(_image_urls(sent_messages)[0], image_format, data)
    assert sent_messages[0]["content"][1]["image_url"]["detail"] == "high"
    assert CACHE_BOUNDARY_KEY not in sent_messages[0]["content"][1]
    assert messages == original
    assert path.read_bytes() == data


@pytest.mark.parametrize("backend", ["openai", "doubao"])
@pytest.mark.parametrize("origin", ["task", "action", "attachment"])
async def test_step_images_are_encoded_only_for_http_and_sessions_keep_paths(tmp_path, backend, origin):
    """任务、工具观测与后续附件共用出站转换，会话恢复仍使用可读的本地引用。"""

    data = _image_bytes("PNG")
    path = tmp_path / "step-image.png"
    path.write_bytes(data)
    if origin == "task":
        step = TaskStep(task="Inspect the image.", task_images=[str(path)])
    elif origin == "action":
        step = ActionStep(
            step_num=1, model_output="<thought>Inspect the result.</thought>",
            observations_images=[ObservationImage(image_url=str(path), description="tool result")],
        )
    else:
        step = TaskStep(task="Continue.", attachments=[{
            "attachment_type": "user_message", "message_id": "image-followup", "created_at": 1.0,
            "payload": {"text": "Inspect this attachment.", "task_images": [str(path)]},
        }])
    messages = step.to_messages()
    assert _image_urls(messages) == [str(path)]
    original = copy.deepcopy(messages)
    node = ContextNode(node_id="image-step", step=step)
    saved_before = serialize_context_node(node)
    requests = []

    async with _chat_model(backend, requests) as model:
        (await model.generate(messages))

    assert len(requests) == 1
    _assert_image_source(_image_urls(json.loads(requests[0].content)["messages"])[0], "PNG", data)
    assert messages == original
    assert serialize_context_node(node) == saved_before
    serialized = json.dumps(saved_before)
    assert str(path) in serialized
    assert "data:image/" not in serialized
    restored = deserialize_context_node(saved_before)
    assert _image_urls(restored.step.to_messages()) == [str(path)]


async def test_generated_jpeg_is_visible_to_the_next_chat_request_without_reencoding(tmp_path):
    """回归实际故障链路：Ark 生成 JPEG 落盘，再将其完整字节传给下一轮聊天。"""

    generated_bytes = _image_bytes("JPEG")
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v3/images/generations":
            return httpx.Response(200, json={
                "model": "offline-image", "created_at": 1,
                "data": [{"b64_json": base64.b64encode(generated_bytes).decode("ascii")}],
            })
        return _chat_response()

    with Ark(
        api_key="offline-image-transport-key", base_url="https://vision.test/api/v3", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        tool = GenerateEditImageTool(client=client, model_name="offline-image")
        tool.cache_dir = str(tmp_path / "observations")
        image = tool(description="generated image", prompt="Draw a flower.")
        local_path = Path(image.image_url)
        assert local_path.is_relative_to(tmp_path)
        assert local_path.read_bytes() == generated_bytes
        step = ActionStep(step_num=1, model_output="<thought>Review the image.</thought>", observations_images=[image])
        messages = step.to_messages()
    # 图片工具仍使用同步 SDK；聊天改用独立的原生异步连接。
    async with AsyncArk(
        api_key="offline-image-transport-key", base_url="https://vision.test/api/v3", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        model = ChatModel(model_name="offline-vision", backend="doubao", client=client, max_retry_times=1, wait_time=0)
        assert (await model.generate(messages))["content"] == "image received"

    assert [request.url.path for request in requests] == ["/api/v3/images/generations", "/api/v3/chat/completions"]
    sent_messages = json.loads(requests[1].content)["messages"]
    _assert_image_source(_image_urls(sent_messages)[0], "JPEG", generated_bytes)
    assert _image_urls(messages) == [str(local_path)]
    assert image.image_url == str(local_path)
    assert local_path.read_bytes() == generated_bytes


@pytest.mark.parametrize("backend", ["openai", "doubao"])
async def test_remote_and_encoded_images_remain_unchanged_without_source_io(tmp_path, monkeypatch, backend):
    """远程图片由服务端获取；既有 data URL 不再解析或重复编码。"""

    data_url = "data:image/png;base64," + base64.b64encode(_image_bytes("PNG")).decode("ascii")
    sources = ["http://images.example/source.png", "https://images.example/source.jpg", data_url]
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": source}} for source in sources
    ]}]
    original = copy.deepcopy(messages)
    remote_observation = ObservationImage(image_url=sources[1])
    requests = []

    def unexpected_io(*args, **kwargs):
        pytest.fail("Remote or pre-encoded image unexpectedly caused source I/O")

    async with _chat_model(backend, requests) as model:
        # SDK 初始化先完成，再阻断图片读取，避免干扰 SDK 自身的初始化行为。
        monkeypatch.setattr(agent_type, "urlopen", unexpected_io)
        monkeypatch.setattr("urllib.request.urlopen", unexpected_io)
        monkeypatch.setattr(Path, "read_bytes", unexpected_io)
        monkeypatch.setattr(Image, "open", unexpected_io)
        assert image_to_api_source(remote_observation) == sources[1]
        (await model.generate(messages))

    assert len(requests) == 1
    assert _image_urls(json.loads(requests[0].content)["messages"]) == sources
    assert messages == original
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("backend", ["openai", "doubao"])
@pytest.mark.parametrize("failure", ["missing", "invalid"])
async def test_unreadable_or_invalid_local_images_fail_before_http(tmp_path, backend, failure):
    """错误在请求发送前暴露，避免将路径或损坏图片交给服务商并触发无效请求。"""

    path = tmp_path / "unusable.png"
    if failure == "invalid":
        path.write_bytes(b"this is not image data")
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": str(path)}}]}]
    original = copy.deepcopy(messages)
    requests = []

    async with _chat_model(backend, requests) as model:
        with pytest.raises((ValueError, OSError)):
            (await model.generate(messages))

    assert requests == []
    assert messages == original


@pytest.mark.parametrize("kind", ["path", "bytes", "bytearray", "pil"])
def test_shared_image_source_supports_in_memory_and_path_inputs(tmp_path, kind):
    """共享编码入口同时服务图片编辑工具，覆盖路径和常用内存图片输入。"""

    data = _image_bytes("PNG")
    path = tmp_path / "input.png"
    path.write_bytes(data)
    if kind == "path":
        source = path
    elif kind == "bytes":
        source = data
    elif kind == "bytearray":
        source = bytearray(data)
    else:
        source = Image.new("RGB", (3, 2), "#c43a56")

    _assert_image_source(image_to_api_source(source), "PNG", data)
