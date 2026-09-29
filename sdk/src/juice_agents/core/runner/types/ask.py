"""Runner 级用户提问协议。

`ask` 是面向 root/user-facing agent 的同步交互能力。工具层只传递
JSON-friendly dict，Runner 负责把请求交给当前 adapter 或 API consumer。
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any, Literal, TypedDict

AskStatus = Literal["answered", "cancelled", "error"]


class AskOption(TypedDict):
    label: str
    value: str
    description: str


class AskRequest(TypedDict, total=False):
    request_id: str
    question: str
    options: list[AskOption]
    multiple: bool
    allow_custom: bool
    timeout_seconds: float | None


class AskResponse(TypedDict, total=False):
    status: AskStatus
    request_id: str
    question: str
    selected: list[AskOption]
    custom_response: str
    error: str


AskHandler = Callable[[AskRequest], AskResponse | dict[str, Any]]


def normalize_ask_option(raw: Any) -> AskOption:
    """把 string/object 选项统一成前端和模型都能稳定消费的结构。"""

    if isinstance(raw, str):
        label = raw.strip()
        if not label:
            raise ValueError("ask options 不能包含空字符串")
        return {"label": label, "value": label, "description": ""}
    if not isinstance(raw, dict):
        raise ValueError("ask options 只支持 string 或 object")
    label = str(raw.get("label") or raw.get("value") or "").strip()
    value = str(raw.get("value") or label).strip()
    if not label or not value:
        raise ValueError("ask option 必须包含非空 label/value")
    return {
        "label": label,
        "value": value,
        "description": str(raw.get("description") or "").strip(),
    }


def normalize_ask_options(raw_options: Any) -> list[AskOption]:
    """规范化 ask 选项列表；None 表示没有预设选项，只允许自由回复。"""

    if raw_options is None:
        return []
    if not isinstance(raw_options, list):
        raise ValueError("options 必须为 list")
    return [normalize_ask_option(item) for item in raw_options]


def normalize_ask_response(
    response: Any,
    *,
    request: AskRequest,
) -> AskResponse:
    """收敛 provider 返回值，保证工具 observation 始终结构化。"""

    request_id = str(request.get("request_id") or "").strip()
    question = str(request.get("question") or "")
    if not isinstance(response, dict):
        return {
            "status": "error",
            "request_id": request_id,
            "question": question,
            "selected": [],
            "custom_response": "",
            "error": "ask handler 返回值必须为 object",
        }

    status = str(response.get("status") or "answered").strip()
    if status not in {"answered", "cancelled", "error"}:
        status = "error"
    raw_selected = response.get("selected") or []
    selected = normalize_ask_options(raw_selected) if isinstance(raw_selected, list) else []
    return {
        "status": status,  # type: ignore[typeddict-item]
        "request_id": str(response.get("request_id") or request_id),
        "question": str(response.get("question") or question),
        "selected": selected,
        "custom_response": str(response.get("custom_response") or ""),
        "error": str(response.get("error") or ""),
    }


def clone_ask_request(request: AskRequest) -> AskRequest:
    """复制请求，避免 handler/UI 意外修改工具侧对象。"""

    return deepcopy(dict(request))  # type: ignore[return-value]


__all__ = [
    "AskHandler",
    "AskOption",
    "AskRequest",
    "AskResponse",
    "clone_ask_request",
    "normalize_ask_option",
    "normalize_ask_options",
    "normalize_ask_response",
]
