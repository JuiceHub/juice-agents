"""Attachment-oriented tools for adding rich observations to an agent turn."""

from __future__ import annotations

from typing import Any

from juice_agents.core.agent.agent_type import DEFAULT_OBSERVATION_IMAGE_CACHE_DIR, ObservationImage

from ...runtime.base_tools import Tool


class AddImageTool(Tool):
    """Convert an image input into an ``ObservationImage`` for the next observation.

    The tool deliberately owns no presentation policy: it only normalizes either a
    supplied URL or an in-memory image and lets ``ObservationImage`` persist the
    latter in the runner-owned cache directory.
    """

    name = "add_image"
    description = "记录一张图片到观测结果中"
    inputs = {
        "image": {"type": "image", "description": "图片对象（可选）", "required": False},
        "image_url": {
            "type": "string",
            "description": "图片网络 URL 或本地路径（优先于 image）",
            "required": False,
        },
        "description": {"type": "string", "description": "图片描述", "required": False},
    }
    outputs = {"image": {"type": "ObservationImage", "description": "ObservationImage"}}

    def __init__(self) -> None:
        super().__init__()
        self.cache_dir: str | None = DEFAULT_OBSERVATION_IMAGE_CACHE_DIR

    def bind_owner_agent(self, agent: Any) -> None:
        """Use the workspace-specific cache once the tool is attached to an agent."""

        context = getattr(agent, "runner_context", None)
        layout = getattr(context, "layout", None)
        cache_dir = getattr(layout, "observation_images_dir", None)
        if cache_dir is not None:
            self.cache_dir = str(cache_dir)

    def forward(
        self,
        image: Any = None,
        image_url: str | None = None,
        description: str | None = None,
    ) -> ObservationImage:
        """Prefer a URL/path when provided; otherwise serialize the image object."""

        desc = description or ""
        if image_url:
            return ObservationImage.from_image_url(
                image_url=image_url,
                description=desc,
                cache_dir=self.cache_dir,
            )
        return ObservationImage.from_image(image=image, description=desc, cache_dir=self.cache_dir)


__all__ = ["AddImageTool"]
