"""Image attachment and generation tool contracts."""

import base64
import io
import os
import unittest
from pathlib import Path

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
        self.observation_images_dir = root / ".juice" / "runners" / "run123" / "observation_images"


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
            self.assertIn(".juice/runners/run123/observation_images", result.image_url)
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

    def test_edit_image_uses_edits_method(self):
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
        self.assertEqual(method, "edits")
        self.assertEqual(kwargs["model"], tool.model_name)
        self.assertEqual(kwargs["prompt"], "add a hat")
        self.assertIn("image", kwargs)
        self.assertIsInstance(kwargs["image"], io.BytesIO)
        self.assertTrue(kwargs["image"].getvalue().startswith(b"\x89PNG"))

    def test_prompt_required(self):
        tool = GenerateEditImageTool(client=FakeClient(FakeResponse([])))
        with self.assertRaises(ValueError):
            tool(description="demo", prompt="  ", image=None)


if __name__ == "__main__":
    unittest.main()
