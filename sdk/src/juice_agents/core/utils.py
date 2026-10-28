"""
通用工具函数。
"""

from __future__ import annotations

import json
import logging
import math
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


def _get_field(obj: Any, field: str) -> Any:
    return obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)


def _to_plain_data(value: Any, *, _depth: int = 0) -> Any:
    """把 SDK 的 Pydantic / 普通对象转换为可保存的原始用量快照。"""
    if _depth > 8:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _to_plain_data(item, _depth=_depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain_data(item, _depth=_depth + 1) for item in value]
    for name in ("model_dump", "dict"):
        dump = getattr(value, name, None)
        if callable(dump):
            try:
                return _to_plain_data(dump(), _depth=_depth + 1)
            except TypeError:
                continue
    fields: dict[str, Any] = {}
    for name in dir(value):
        if name.startswith("_"):
            continue
        try:
            item = getattr(value, name)
        except Exception:
            continue
        if not callable(item):
            fields[name] = item
    return _to_plain_data(fields, _depth=_depth + 1) if fields else str(value)


def _coerce_token_count(value: Any) -> int | None:
    """接受非负整数；拒绝布尔值、负数、非有限数及需要截断的小数。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value >= 0 and value.is_integer() else None
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    return None


def normalize_token_usage(usage: Any) -> dict[str, Any] | None:
    """归一化 OpenAI / Ark / Anthropic 用量，并保留 provider 原始详情。

    仅在输入和输出均已知时推导 total；不把未知值默认为 0。函数幂等，
    再次传入归一化记录时保留原 provider_usage，不增加嵌套包装。
    """
    if usage is None:
        return None
    input_value = _get_field(usage, "input_tokens")
    output_value = _get_field(usage, "output_tokens")
    input_tokens = _coerce_token_count(input_value if input_value is not None else _get_field(usage, "prompt_tokens"))
    output_tokens = _coerce_token_count(output_value if output_value is not None else _get_field(usage, "completion_tokens"))
    total_tokens = _coerce_token_count(_get_field(usage, "total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    provider_usage = usage["provider_usage"] if isinstance(usage, dict) and "provider_usage" in usage else usage
    normalized: dict[str, Any] = {"provider_usage": _to_plain_data(provider_usage)}
    for name, value in zip(("input_tokens", "output_tokens", "total_tokens"), (input_tokens, output_tokens, total_tokens)):
        if value is not None:
            normalized[name] = value
    return normalized



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
    "normalize_token_usage",
    "load_env_file",
    "parse_model_json",
    "strip_quotes",
    "sanitize_path_component",
]
