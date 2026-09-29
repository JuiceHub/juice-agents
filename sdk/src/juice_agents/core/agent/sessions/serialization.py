"""Session JSON 编解码。"""

from __future__ import annotations

import json
import re
from typing import Any

from ..agent_type import ObservationImage
from .errors import AgentError
from .session import AgentSession
from .steps import ActionStep, SummaryStep, TaskStep

try:  # json_repair is optional, but useful for old non-strict action payloads.
    from json_repair import repair_json
except Exception:  # pragma: no cover - optional dependency guard
    repair_json = None

_MODEL_OUTPUT_BLOCK_RE = re.compile(
    r"<(?P<tag>[A-Za-z_][\w:-]*)>\s*(?P<body>.*?)\s*</(?P=tag)>",
    re.DOTALL,
)


def _serialize_image(value: Any) -> dict[str, Any]:
    if isinstance(value, ObservationImage):
        return {
            "image_url": value.ensure_image_url(),
            "description": str(value.description or ""),
        }
    return {
        "image_url": str(value or ""),
        "description": "",
    }


def _deserialize_image(payload: dict[str, Any]) -> Any:
    image_url = str(payload.get("image_url") or "").strip()
    description = str(payload.get("description") or "").strip()
    if not image_url:
        return ""
    try:
        return ObservationImage(image_url=image_url, description=description)
    except Exception:  # pragma: no cover - 运行时保护
        return image_url


def _extract_model_output_block(model_output: Any, tag: str) -> str:
    text = "" if model_output is None else str(model_output)
    if not text.strip():
        return ""
    matches = [
        str(match.group("body") or "").strip()
        for match in _MODEL_OUTPUT_BLOCK_RE.finditer(text)
        if str(match.group("tag") or "").strip() == tag
    ]
    return matches[-1] if matches else ""


def _load_legacy_actions(model_output: Any) -> list[dict[str, Any]]:
    raw_actions = _extract_model_output_block(model_output, "actions")
    if not raw_actions:
        return []
    candidates = [raw_actions]
    if repair_json is not None:
        try:
            repaired = repair_json(raw_actions)
            candidates.append(repaired)
        except Exception:
            pass
    parsed: Any = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except Exception:
            continue
    if not isinstance(parsed, list):
        return []
    tool_calls: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        args = item.get("args")
        if not name or name == "submit_output" or not isinstance(args, dict):
            continue
        tool_calls.append(
            {
                "name": name,
                "args": dict(args),
                # Historical action payloads could contain the removed generic
                # ``execution`` hint.  It was never authoritative during
                # replay, so discard it and project the call as the
                # framework-owned conservative mode.
                "mode": "serial",
                "execution_mode": "serial",
                "status": "unknown",
                "observation_index": None,
                "is_terminal": False,
            }
        )
    return tool_calls


def serialize_session(session: AgentSession) -> dict[str, Any]:
    """把 AgentSession 转成可恢复的纯 JSON 结构。"""
    serialized_steps: list[dict[str, Any]] = []
    for step in list(session.steps or []):
        if isinstance(step, TaskStep):
            serialized_steps.append(
                {
                    "type": "task",
                    "task": step.task,
                    "task_images": [_serialize_image(item) for item in list(step.task_images or [])],
                    "attachments": [dict(item) for item in list(step.attachments or [])],
                }
            )
            continue
        if isinstance(step, ActionStep):
            serialized_steps.append(
                {
                    "type": "action",
                    "step_num": int(step.step_num),
                    "model_output": step.model_output,
                    "thought": step.thought,
                    "tool_calls": [dict(item) for item in list(step.tool_calls or []) if isinstance(item, dict)],
                    "code_action": step.code_action,
                    "reasoning_content": step.reasoning_content,
                    "observations": list(step.observations or []),
                    "observation_images": [_serialize_image(item) for item in list(step.observations_images or [])],
                    "attachments": [dict(item) for item in list(step.attachments or [])],
                    "error": "" if step.error is None else str(step.error),
                    "observation_limits": list(step.observation_limits or []),
                    "request_bytes": int(step.request_bytes or 0),
                    "usage": None if step.usage is None else dict(step.usage),
                    "round_outcome": step.round_outcome,
                    "output": step.output,
                }
            )
            continue
        if isinstance(step, SummaryStep):
            serialized_steps.append({"type": "summary", "content": step.content})
    return {
        "system_prompt": session.system_prompt,
        "system_prompt_static": session.system_prompt_static,
        "system_prompt_dynamic": session.system_prompt_dynamic,
        "include_reasoning_in_context": bool(session.include_reasoning_in_context),
        "steps": serialized_steps,
    }


def deserialize_session(payload: dict[str, Any]) -> AgentSession:
    """从持久化结构恢复 AgentSession。"""
    static_value = payload.get("system_prompt_static")
    dynamic_value = payload.get("system_prompt_dynamic")
    session = AgentSession(
        system_prompt=str(payload.get("system_prompt") or ""),
        include_reasoning_in_context=bool(payload.get("include_reasoning_in_context")),
        system_prompt_static=str(static_value) if static_value is not None else None,
        system_prompt_dynamic=str(dynamic_value) if dynamic_value is not None else None,
    )
    # max_observation_length / max_observation_context_length 是旧版全局预算。
    # 它们可能仍存在于历史 JSON 中，但新协议故意忽略，避免恢复已删除语义。
    for raw in list(payload.get("steps") or []):
        if not isinstance(raw, dict):
            continue
        step_type = str(raw.get("type") or "").strip()
        if step_type == "task":
            session.append_step(
                TaskStep(
                    task=str(raw.get("task") or ""),
                    task_images=[
                        _deserialize_image(dict(item))
                        for item in list(raw.get("task_images") or [])
                        if isinstance(item, dict)
                    ],
                    attachments=[dict(item) for item in list(raw.get("attachments") or []) if isinstance(item, dict)],
                )
            )
            continue
        if step_type == "action":
            error_text = str(raw.get("error") or "").strip()
            model_output = str(raw.get("model_output") or "")
            legacy_blocks = dict(raw.get("model_output_blocks") or {}) if isinstance(raw.get("model_output_blocks"), dict) else {}
            thought = str(raw.get("thought") or legacy_blocks.get("thought") or _extract_model_output_block(model_output, "thought") or "")
            code_action = str(raw.get("code_action") or legacy_blocks.get("code") or _extract_model_output_block(model_output, "code") or "")
            raw_tool_calls = raw.get("tool_calls")
            tool_calls = (
                [dict(item) for item in raw_tool_calls if isinstance(item, dict)]
                if isinstance(raw_tool_calls, list)
                else _load_legacy_actions(model_output)
            )
            session.append_step(
                ActionStep(
                    step_num=int(raw.get("step_num") or 0),
                    model_output=model_output,
                    thought=thought,
                    tool_calls=tool_calls,
                    code_action=code_action,
                    reasoning_content=str(raw.get("reasoning_content") or ""),
                    observations=list(raw.get("observations") or []),
                    observations_images=[
                        _deserialize_image(dict(item))
                        for item in list(raw.get("observation_images") or [])
                        if isinstance(item, dict)
                    ],
                    attachments=[dict(item) for item in list(raw.get("attachments") or []) if isinstance(item, dict)],
                    error=AgentError(error_text) if error_text else None,
                    observation_limits=list(raw.get("observation_limits") or []),
                    request_bytes=int(raw.get("request_bytes") or 0),
                    usage=dict(raw.get("usage")) if isinstance(raw.get("usage"), dict) else None,
                    round_outcome=str(raw.get("round_outcome") or "continue"),
                    output=raw.get("output"),
                )
            )
            continue
        if step_type == "summary":
            session.append_step(SummaryStep(content=str(raw.get("content") or "")))
    return session


__all__ = [
    "serialize_session",
    "deserialize_session",
]
