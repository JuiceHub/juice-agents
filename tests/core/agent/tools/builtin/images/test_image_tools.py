"""Image attachment and generation tool contracts."""

import base64
import io
import json
import os
import unittest
from pathlib import Path

import httpx
import pytest
from PIL import Image
from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime._exceptions import ArkBadRequestError

from juice_agents.core.agent.agent_type import ObservationImage
from juice_agents.core.agent.tools.builtin.images.attachments_tools import AddImageTool
from juice_agents.core.agent.tools.builtin.images.images_tools import GenerateEditImageTool

SAMPLE_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5W9r8AAAAASUVORK5CYII="
)


class FakeData:
    def __init__(self, b64_json=None, url=None):
        self.b64_json = b64_json
        self.url = url


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeImages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return self.response

    def edits(self, **kwargs):
        self.calls.append(("edits", kwargs))
        return self.response


class FakeClient:
    def __init__(self, response):
        self.images = FakeImages(response)


class FakeLayout:
    def __init__(self, root: Path):
        self.observation_images_dir = root / ".juice" / "sessions" / "run123" / "observation_images"


class FakeRunnerContext:
    def __init__(self, root: Path):
        self.layout = FakeLayout(root)


class FakeOwnerAgent:
    def __init__(self, root: Path):
        self.runner_context = FakeRunnerContext(root)


class VisionToolsTests(unittest.TestCase):
    def test_add_image_tool_defaults_to_runner_observation_dir_when_bound(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tool = AddImageTool()
            tool.bind_owner_agent(FakeOwnerAgent(Path(d)))

            result = tool(image=SAMPLE_PNG_BYTES, description="runner image")

            self.assertIsInstance(result, ObservationImage)
            self.assertIn(".juice/sessions/run123/observation_images", result.image_url)
            self.assertTrue(os.path.exists(result.image_url))

    def test_generate_image_returns_observation(self):
        payload = SAMPLE_PNG_BYTES
        b64 = base64.b64encode(payload).decode("utf-8")
        response = FakeResponse([FakeData(b64_json=b64)])
        tool = GenerateEditImageTool(client=FakeClient(response))

        result = tool(description="demo", prompt="a cat", image=None)

        self.assertIsInstance(result, ObservationImage)
        self.assertEqual(result.description, "demo")
        self.assertFalse(result.image_url.startswith("data:image"))
        self.assertTrue(result.image_url.startswith("http") or os.path.exists(result.image_url))
        self.assertTrue(result.to_bytes())
        method, kwargs = tool.client.images.calls[0]
        self.assertEqual(method, "generate")
        self.assertEqual(kwargs["model"], tool.model_name)
        self.assertEqual(kwargs["prompt"], "a cat")
        self.assertNotIn("image", kwargs)

    def test_edit_image_uses_generate_with_data_url(self):
        payload = SAMPLE_PNG_BYTES
        b64 = base64.b64encode(payload).decode("utf-8")
        response = FakeResponse([FakeData(b64_json=b64)])
        tool = GenerateEditImageTool(client=FakeClient(response))

        result = tool(
            description="edit",
            prompt="add a hat",
            image=ObservationImage.from_image(image=SAMPLE_PNG_BYTES, description=""),
        )

        self.assertIsInstance(result, ObservationImage)
        method, kwargs = tool.client.images.calls[0]
        self.assertEqual(method, "generate")
        self.assertEqual(kwargs["model"], tool.model_name)
        self.assertEqual(kwargs["prompt"], "add a hat")
        self.assertIn("image", kwargs)
        self.assertTrue(kwargs["image"].startswith("data:image/png;base64,"))
        with Image.open(io.BytesIO(base64.b64decode(kwargs["image"].split(",", 1)[1]))) as edited:
            self.assertEqual(edited.size, (1, 1))

    def test_prompt_required(self):
        tool = GenerateEditImageTool(client=FakeClient(FakeResponse([])))
        with self.assertRaises(ValueError):
            tool(description="demo", prompt="  ", image=None)


def _edit_source(kind: str, tmp_path: Path):
    """Create actual image inputs; every generated file belongs to tmp_path."""

    if kind in {"url", "observation_url"}:
        url = "https://images.example/source.png"
        return ObservationImage(image_url=url) if kind == "observation_url" else url, url
    if kind == "pil":
        return Image.new("RGB", (2, 2), "red"), "image/png"
    if kind == "jpeg":
        output = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(output, format="JPEG")
        return output.getvalue(), "image/jpeg"
    if kind == "bytes":
        return SAMPLE_PNG_BYTES, "image/png"
    if kind == "bytearray":
        return bytearray(SAMPLE_PNG_BYTES), "image/png"
    if kind == "data_url":
        return "data:image/png;base64," + base64.b64encode(SAMPLE_PNG_BYTES).decode(), "image/png"
    path = tmp_path / "source.png"
    path.write_bytes(SAMPLE_PNG_BYTES)
    if kind == "observation_path":
        return ObservationImage(image_url=str(path)), "image/png"
    return str(path) if kind == "str_path" else path, "image/png"


@pytest.mark.parametrize("kind", [
    "generate", "bytes", "bytearray", "path", "str_path", "pil", "jpeg",
    "url", "observation_url", "observation_path", "data_url",
])
def test_real_ark_sdk_serializes_generation_and_edit_inputs(tmp_path, kind):
    """Exercise Ark's real JSON encoder and route with a mock HTTP transport."""

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={
            "model": "ep-image", "created_at": 1,
            "data": [{"b64_json": base64.b64encode(SAMPLE_PNG_BYTES).decode()}],
        })

    # Only transport is replaced: Ark's SDK, response model and image tool
    # execute normally. Remote source URLs must never be downloaded locally.
    with Ark(api_key="offline-image-key", base_url="https://ark.test/api/v3", max_retries=0,
             http_client=httpx.Client(transport=httpx.MockTransport(handle))) as client:
        tool = GenerateEditImageTool(client=client, model_name="ep-image")
        tool.bind_owner_agent(FakeOwnerAgent(tmp_path))
        source, expected = (None, None) if kind == "generate" else _edit_source(kind, tmp_path)
        result = tool(description="result", prompt="draw a bird", image=source)

    # ObservationImage intentionally normalizes image encoding; compare the
    # decoded pixels rather than depending on a particular PNG compression.
    with Image.open(io.BytesIO(SAMPLE_PNG_BYTES)) as original:
        assert result.to_pil().tobytes() == original.tobytes()
    assert Path(result.image_url).is_relative_to(tmp_path)
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/api/v3/images/generations"
    assert request.headers["authorization"] == "Bearer offline-image-key"
    payload = json.loads(request.content)
    assert payload["model"] == "ep-image"
    assert payload["response_format"] == "b64_json"
    if expected is None:
        assert payload.get("image") is None
    elif expected.startswith("https://"):
        assert payload["image"] == expected
    else:
        prefix, encoded = payload["image"].split(",", 1)
        assert prefix == f"data:{expected};base64"
        with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as decoded:
            assert Image.MIME[decoded.format] == expected


@pytest.mark.parametrize("status, body, error", [
    (400, {"error": {"code": "InvalidParameter", "message": "invalid image"}}, ArkBadRequestError),
    (200, {"data": [], "error": {"code": "GenerationFailed", "message": "failed"}}, ValueError),
    (200, {"data": [{"b64_json": "invalid base64"}]}, ValueError),
])
def test_real_ark_sdk_errors_do_not_persist_images(tmp_path, status, body, error, caplog):
    with Ark(api_key="offline-secret-key", base_url="https://ark.test/api/v3", max_retries=0,
             http_client=httpx.Client(transport=httpx.MockTransport(
                 lambda request: httpx.Response(status, json=body)))) as client:
        tool = GenerateEditImageTool(client=client)
        tool.bind_owner_agent(FakeOwnerAgent(tmp_path))
        with pytest.raises(error):
            tool(description="result", prompt="draw")
    assert not list(tmp_path.rglob("*"))
    assert "offline-secret-key" not in caplog.text


if __name__ == "__main__":
    unittest.main()
