"""
视觉工具模块，为智能体提供图片生成/编辑能力。
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any, Optional

from juice_agents.core.agent.agent_type import DEFAULT_OBSERVATION_IMAGE_CACHE_DIR, ObservationImage

from ...runtime.base_tools import Tool

logger = logging.getLogger(__name__)

try:
    from volcenginesdkarkruntime import Ark  # type: ignore
except ImportError:  # pragma: no cover - 运行时按需安装
    Ark = None  # type: ignore


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()  # type: ignore[misc]
            except Exception:
                pass
    return {}


def _get_field(obj: Any, *names: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj.get(name)
        return default
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _strip_data_url(value: str) -> str:
    if value.startswith("data:image"):
        parts = value.split(",", 1)
        if len(parts) == 2:
            return parts[1]
    return value


def _decode_base64_image(value: str) -> bytes:
    return base64.b64decode(_strip_data_url(value))


def _download_image(url: str, timeout_s: float) -> bytes:
    from urllib.request import urlopen

    with urlopen(url, timeout=timeout_s) as response:  # noqa: S310 - 受控下载
        return response.read()


def _coerce_image_bytes(image: Any, timeout_s: float = 60.0) -> bytes:
    if ObservationImage is not None and isinstance(image, ObservationImage):
        return image.to_bytes(timeout_s=timeout_s)

    if isinstance(image, bytes):
        return image
    if isinstance(image, bytearray):
        return bytes(image)

    if isinstance(image, str):
        if image.startswith("http://") or image.startswith("https://"):
            return _download_image(image, timeout_s)
        path = Path(image).expanduser()
        if path.exists():
            return path.read_bytes()
        raise ValueError("image 字符串必须是有效的网络 URL 或本地文件路径")

    try:
        from PIL import Image  # type: ignore

        if isinstance(image, Image.Image):
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        pass

    raise TypeError(
        "image 必须为 ObservationImage / PIL.Image / bytes / 可读本地文件路径 / 网络 URL"
    )


class GenerateEditImageTool(Tool):
    """
    使用火山引擎 doubao-seedream-4-0-250828 模型生成或编辑图片。
    """

    name = "generate_edit_image"
    description = "基于火山引擎 doubao-seedream-4-0-250828 生成或编辑图片"
    inputs = {
        "description": {"type": "string", "description": "生成图片简述，仅用于输出标识"},
        "prompt": {"type": "string", "description": "用于生成/编辑图片的提示词"},
        "image": {
            "type": "image",
            "description": "用于编辑的原图，传 None 表示生成图片",
            "required": False,
        },
    }
    outputs = {"image": {"type": "ObservationImage", "description": "ObservationImage类型的图片"}}

    DEFAULT_API_BASE = "https://ark.cn-beijing.volces.com/api/v3"
    DEFAULT_MODEL_NAME = "doubao-seedream-4-0-250828"

    def __init__(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        model_name: str | None = None,
        client: Any | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        super().__init__()
        self.timeout_s = timeout_s
        self.cache_dir: str | None = DEFAULT_OBSERVATION_IMAGE_CACHE_DIR

        if client is not None:
            self.client = client
            self.api_base = api_base or ""
            self.api_key = api_key or ""
            self.model_name = model_name or self.DEFAULT_MODEL_NAME
            logger.info("GenerateEditImageTool 使用外部注入 client")
            return

        self.model_name = model_name or self.DEFAULT_MODEL_NAME
        self.api_base = str(api_base or self.DEFAULT_API_BASE)
        self.api_key = str(api_key or "")

        if not self.api_key:
            raise ValueError("GenerateEditImageTool 需要 api_key")

        self.client = self._init_client()
        logger.info(
            "GenerateEditImageTool 初始化完成 model=%s api_base=%s",
            self.model_name,
            self.api_base,
        )

    def bind_owner_agent(self, agent: Any) -> None:
        context = getattr(agent, "runner_context", None)
        layout = getattr(context, "layout", None)
        cache_dir = getattr(layout, "observation_images_dir", None)
        if cache_dir is not None:
            self.cache_dir = str(cache_dir)

    def _init_client(self) -> Any:
        if Ark is None:
            raise ImportError('未安装 volcengine-python-sdk[ark]，无法创建图像客户端')
        try:
            return Ark(api_key=self.api_key, base_url=self.api_base)
        except TypeError:
            return Ark(api_key=self.api_key, api_base=self.api_base)

    def _call_images_api(self, prompt: str, image_bytes: Optional[bytes]) -> Any:
        images_client = getattr(self.client, "images", None)
        if images_client is None:
            raise AttributeError("client 不支持 images 接口")

        payload: dict[str, Any] = {"model": self.model_name, "prompt": prompt}
        method_candidates: tuple[str, ...]
        if image_bytes is None:
            method_candidates = ("generate", "create")
        else:
            payload["image"] = io.BytesIO(image_bytes)
            method_candidates = ("edits", "edit", "generate", "create")

        for name in method_candidates:
            method = getattr(images_client, name, None)
            if callable(method):
                logger.info("调用 images.%s 生成/编辑图片", name)
                return method(**payload)

        raise AttributeError("client.images 缺少可用的生成/编辑方法")

    def _extract_image_bytes(self, response: Any) -> bytes:
        if response is None:
            return b""

        data = _get_field(response, "data", "images", default=None)
        if data is None:
            response_dict = _as_dict(response)
            data = response_dict.get("data") or response_dict.get("images")
        if data is None:
            data = [response]
        if not isinstance(data, list):
            data = [data]

        for item in data:
            if isinstance(item, (bytes, bytearray)):
                return bytes(item)

            item_dict = _as_dict(item)
            for key in ("b64_json", "base64", "b64", "image"):
                value = _get_field(item, key, default=item_dict.get(key))
                if isinstance(value, (bytes, bytearray)):
                    return bytes(value)
                if isinstance(value, str) and value:
                    try:
                        return _decode_base64_image(value)
                    except Exception:
                        continue

            url = _get_field(item, "url", "image_url", default=item_dict.get("url"))
            if isinstance(url, str) and url:
                try:
                    return _download_image(url, self.timeout_s)
                except Exception as exc:
                    logger.warning("下载图片失败: %s", exc)
                    continue

        return b""

    def forward(
        self, description: str, prompt: str, image: Any | None = None
    ) -> ObservationImage:
        if not isinstance(description, str):
            raise TypeError("description 必须为字符串")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt 必须为非空字符串")

        is_edit = image is not None
        logger.info(
            "GenerateEditImageTool 调用 mode=%s prompt_len=%d description_len=%d",
            "edit" if is_edit else "generate",
            len(prompt),
            len(description),
        )

        image_bytes = _coerce_image_bytes(image, timeout_s=self.timeout_s) if is_edit else None
        response = self._call_images_api(prompt, image_bytes)
        result_bytes = self._extract_image_bytes(response)
        if not result_bytes:
            raise ValueError("未获取到有效的图片数据")

        # 统一返回 ObservationImage（由 ObservationImage 管理本地落盘和 image_url）
        return ObservationImage.from_image(
            image=result_bytes,
            description=description or "",
            cache_dir=self.cache_dir,
        )


__all__ = ["GenerateEditImageTool"]
