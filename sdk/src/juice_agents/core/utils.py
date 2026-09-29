"""
通用工具函数。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_JSON_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", re.IGNORECASE)
_JSON_PAYLOAD_RE = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]")
_INVALID_PATH_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

try:
    from json_repair import repair_json
except Exception:  # pragma: no cover - 依赖缺失按降级路径运行
    repair_json = None


def _extract_json_candidate(raw_text: str) -> str | None:
    """
    从文本中抽取优先级最高的 JSON 片段。

    优先返回 markdown 代码块中的内容，否则返回首个 `{...}` 或 `[...]` 片段。
    """
    text = raw_text.strip()
    if not text:
        return None

    fenced = _JSON_CODE_FENCE_RE.search(text)
    if fenced:
        return fenced.group(1).strip()

    matched = _JSON_PAYLOAD_RE.search(text)
    if matched:
        return matched.group(0).strip()

    return None


def _parse_json_text(
    raw_text: str,
    *,
    field_name: str,
    allow_repair: bool,
) -> Any:
    """
    解析单段 JSON 文本，必要时使用 json_repair 兜底。

    这里不做片段抽取，只负责“这段文本本身就是 JSON”的解析路径。
    """
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as strict_exc:
        if not allow_repair or repair_json is None:
            raise ValueError(f"{field_name} 不是合法 JSON: {strict_exc}") from strict_exc
        try:
            repaired = repair_json(raw_text, return_objects=False, ensure_ascii=False)
        except TypeError:
            # 某些版本的 json_repair 签名不支持 return_objects/ensure_ascii 两个参数
            repaired = repair_json(raw_text)
        except Exception as repair_exc:  # pragma: no cover
            raise ValueError(f"{field_name} 解析失败: {repair_exc}") from repair_exc

        try:
            return repaired if not isinstance(repaired, str) else json.loads(repaired)
        except Exception as repair_exc:  # pragma: no cover - 极端修复结果格式
            raise ValueError(f"{field_name} 修复后解析失败: {repair_exc}") from repair_exc


def parse_model_json(
    raw: Any,
    *,
    field_name: str = "json",
    expected_type: type | tuple[type, ...] | None = None,
    allow_repair: bool = True,
) -> Any:
    """
    解析模型结构化输出文本，失败时尽量降级修复。

    - raw 已经是 dict/list 时直接返回；
    - 支持 markdown code fence 里的 JSON；
    - 支持前后有非 JSON 噪声的文本；
    - 支持可选 json_repair 兜底修复。
    """
    if raw is None:
        raise ValueError(f"{field_name} 为空")

    if isinstance(raw, (dict, list)):
        if expected_type is not None and not isinstance(raw, expected_type):
            raise ValueError(
                f"{field_name} 解析结果类型不匹配: expect={expected_type}, actual={type(raw)}"
            )
        return raw

    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    stripped = text.strip()
    if stripped[:1] in {"{", "["}:
        parsed = _parse_json_text(
            stripped,
            field_name=field_name,
            allow_repair=allow_repair,
        )
        if expected_type is not None and not isinstance(parsed, expected_type):
            raise ValueError(
                f"{field_name} 解析结果类型不匹配: expect={expected_type}, actual={type(parsed)}"
            )
        return parsed

    candidate = _extract_json_candidate(text)
    if not candidate:
        raise ValueError(f"{field_name} 中未检测到 JSON 片段")
    parsed = _parse_json_text(
        candidate,
        field_name=field_name,
        allow_repair=allow_repair,
    )

    if expected_type is not None and not isinstance(parsed, expected_type):
        raise ValueError(
            f"{field_name} 解析结果类型不匹配: expect={expected_type}, actual={type(parsed)}"
        )

    return parsed



def strip_quotes(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def sanitize_path_component(value: str, *, fallback: str) -> str:
    """
    将任意文本转换为稳定、可落盘的单个路径片段。
    """
    cleaned = _INVALID_PATH_CHARS_RE.sub("_", str(value or "").strip())
    cleaned = cleaned.strip().strip(".")
    return cleaned or fallback


def load_env_file(path: str | Path) -> Dict[str, str]:
    """
    读取简单 `.env` 文件。

    规则：
    - 支持 `KEY=value`
    - 忽略空行与 `#` 注释
    - 支持可选的 `export KEY=value`
    - 不直接写入 `os.environ`，由调用方决定优先级
    """
    env_path = Path(path).expanduser().resolve()
    if not env_path.exists():
        return {}

    values: Dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            continue
        values[normalized_key] = strip_quotes(value.strip())
    return values


__all__ = [
    "load_env_file",
    "parse_model_json",
    "strip_quotes",
    "sanitize_path_component",
]
