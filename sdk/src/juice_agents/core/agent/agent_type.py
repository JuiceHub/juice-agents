"""
Agent 特殊类型定义。

包含 ObservationImage，用于在多模态工具或代码执行过程中携带图片及其描述，
供后续步骤作为上下文使用。
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.request import urlopen

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # 仅用于类型检查，避免运行时强依赖 Pillow
    from PIL import Image as PillowImage  # pragma: no cover


try:  # pragma: no cover - Pillow 可选安装
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover - Pillow 缺失时退化为 Any
    Image = None  # type: ignore


DEFAULT_OBSERVATION_IMAGE_CACHE_DIR = ".juice/observation_images"


def _is_http_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _looks_like_base64(value: str) -> bool:
    candidate = value.strip()
    if not candidate or len(candidate) < 48:
        return False
    allowed_chars = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r"
    )
    if any(ch not in allowed_chars for ch in candidate):
        return False
    # 仅作为启发式判断：长度较长且字符集符合时视作 base64 文本
    return True


def _is_data_url(value: str) -> bool:
    return value.lower().startswith("data:image")


def _guess_image_ext(image_bytes: bytes, fallback: str = ".img") -> str:
    """根据常见 magic bytes 推断图片扩展名。"""
    if not image_bytes:
        return fallback
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return ".gif"
    if image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[8:16]:
        return ".webp"
    return fallback


def _load_pillow_from_bytes(image_bytes: bytes) -> "PillowImage | None":
    if Image is None:
        return None
    try:
        parsed = Image.open(io.BytesIO(image_bytes))  # type: ignore[attr-defined]
        parsed.load()
        return parsed.copy()  # type: ignore[return-value]
    except Exception as exc:
        raise ValueError("bytes 不是有效的图片数据，无法解析为 raw_image") from exc


def _pil_to_png_bytes(image: Any) -> bytes:
    if Image is None:
        raise ImportError("未安装 Pillow，无法处理 raw_image")
    if not isinstance(image, Image.Image):  # type: ignore[attr-defined]
        raise TypeError("raw_image 必须为 PIL.Image.Image")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _persist_bytes_to_cache(
    image_bytes: bytes,
    cache_dir: str | None,
    ext: str | None = None,
) -> str:
    if cache_dir is None:
        raise ValueError("cache_dir=None 时无法将图片落盘并生成 image_url")
    if not image_bytes:
        raise ValueError("图片内容为空，无法写入 cache_dir")

    image_ext = ext or _guess_image_ext(image_bytes, fallback=".img")
    cache_path_dir = Path(cache_dir).expanduser()
    cache_path_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(image_bytes).hexdigest()[:16]
    cache_path = cache_path_dir / f"{digest}{image_ext}"
    if not cache_path.exists():
        cache_path.write_bytes(image_bytes)
        logger.info("ObservationImage 已写入缓存: %s", cache_path)
    else:
        logger.debug("ObservationImage 缓存命中: %s", cache_path)
    return str(cache_path)


def normalize_image_url(image_url: str) -> str:
    """
    规范化并校验 image_url（仅允许 http(s) URL 或本地路径）。
    """
    value = str(image_url).strip()
    if not value:
        return ""
    if _is_data_url(value):
        raise ValueError("不再支持 data URL，请传入网络 URL 或本地文件路径")
    if _looks_like_base64(value):
        raise ValueError("不再支持 base64 图片字符串，请传入网络 URL 或本地文件路径")
    if _is_http_url(value):
        return value

    path = Path(value).expanduser()
    if not path.exists():
        raise ValueError("image_url 不是有效的网络 URL 或本地文件路径")
    return str(path)


def image_url_to_bytes(image_url: str, timeout_s: float = 60.0) -> bytes:
    """
    将 image_url（网络 URL / 本地文件路径）转为 bytes。
    """
    normalized = normalize_image_url(image_url)
    if not normalized:
        return b""
    if _is_http_url(normalized):
        with urlopen(normalized, timeout=timeout_s) as response:  # noqa: S310 - 受控下载
            return response.read()
    return Path(normalized).read_bytes()


@dataclass
class ObservationImage:
    """
    带描述的图片包装类型。

    Attributes:
        image_url: 图片网络 URL 或本地文件路径。
        description: 对图片的文字描述。
        raw_image: 可选原始图片对象（PIL.Image.Image）。
        cache_dir: 当仅有 raw_image 时，用于落盘并生成 image_url 的目录。
    """

    image_url: str = ""
    description: str = ""
    raw_image: "PillowImage | None" = None
    cache_dir: str | None = DEFAULT_OBSERVATION_IMAGE_CACHE_DIR

    def __post_init__(self) -> None:
        self.description = str(self.description or "")
        self.image_url = str(self.image_url or "").strip()

        if self.raw_image is not None:
            _pil_to_png_bytes(self.raw_image)

        if self.image_url:
            self.image_url = normalize_image_url(self.image_url)
            return

        if self.raw_image is None:
            raise ValueError("ObservationImage 需要 image_url 或 raw_image 至少一个")

        # 仅有 raw_image 时，自动落盘生成 image_url
        self.image_url = self.ensure_image_url()

    def normalized_url(self) -> str:
        return normalize_image_url(self.image_url)

    def ensure_image_url(self) -> str:
        """
        确保 image_url 可用；当仅有 raw_image 时按 cache_dir 自动落盘并回填。
        """
        if self.image_url:
            self.image_url = normalize_image_url(self.image_url)
            return self.image_url
        if self.raw_image is None:
            raise ValueError("raw_image 为空，无法生成 image_url")
        image_bytes = _pil_to_png_bytes(self.raw_image)
        self.image_url = _persist_bytes_to_cache(
            image_bytes=image_bytes,
            cache_dir=self.cache_dir,
            ext=".png",
        )
        return self.image_url

    def to_bytes(self, timeout_s: float = 60.0) -> bytes:
        if self.raw_image is not None:
            return _pil_to_png_bytes(self.raw_image)
        return image_url_to_bytes(self.image_url, timeout_s=timeout_s)

    def to_pil(self, timeout_s: float = 60.0) -> "PillowImage":
        if self.raw_image is not None:
            return self.raw_image
        if Image is None:
            raise ImportError("未安装 Pillow，无法导出 PIL.Image.Image")
        data = self.to_bytes(timeout_s=timeout_s)
        if not data:
            raise ValueError("图片内容为空，无法导出图片对象")
        parsed = Image.open(io.BytesIO(data))  # type: ignore[attr-defined]
        parsed.load()
        return parsed.copy()  # type: ignore[return-value]

    @classmethod
    def from_image_url(
        cls,
        image_url: str,
        description: str = "",
        cache_dir: str | None = DEFAULT_OBSERVATION_IMAGE_CACHE_DIR,
    ) -> "ObservationImage":
        return cls(image_url=str(image_url), description=description or "", cache_dir=cache_dir)

    @classmethod
    def from_image(
        cls,
        image: Any,
        description: str = "",
        cache_dir: str | None = DEFAULT_OBSERVATION_IMAGE_CACHE_DIR,
    ) -> "ObservationImage":
        if isinstance(image, cls):
            if description:
                image.description = description
            if cache_dir is not None:
                image.cache_dir = cache_dir
            if not image.image_url and image.raw_image is not None:
                image.ensure_image_url()
            return image

        if isinstance(image, str):
            return cls(
                image_url=str(image),
                description=description or "",
                raw_image=None,
                cache_dir=cache_dir,
            )

        if isinstance(image, (bytes, bytearray)):
            image_bytes = bytes(image)
            if not image_bytes:
                raise ValueError("bytes 图片内容为空")
            pil_image = _load_pillow_from_bytes(image_bytes)
            ext = _guess_image_ext(image_bytes, fallback=".img")
            image_url = _persist_bytes_to_cache(
                image_bytes=image_bytes,
                cache_dir=cache_dir,
                ext=ext,
            )
            return cls(
                image_url=image_url,
                description=description or "",
                raw_image=pil_image,
                cache_dir=cache_dir,
            )

        if Image is not None and isinstance(image, Image.Image):  # type: ignore[attr-defined]
            return cls(
                image_url="",
                description=description or "",
                raw_image=image.copy(),
                cache_dir=cache_dir,
            )

        raise TypeError(
            "不支持的图片类型，需传入 ObservationImage / str(URL或本地路径) / "
            "bytes / PIL.Image.Image"
        )


__all__ = ["ObservationImage", "normalize_image_url", "image_url_to_bytes"]
