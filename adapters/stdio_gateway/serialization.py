"""One JSON contract shared by stdio and WebSocket public event delivery."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from juice_agents.core.agent.agent_type import ObservationImage
from juice_agents.core.agent.context.steps import ActionStep
from juice_agents.core.agent.context.serialization import _serialize_step


def serialize_action_step(step: ActionStep) -> dict[str, Any]:
    """Reuse the committed SDK projection, including round/step/tool identities."""
    return _json_value(_serialize_step(step))


def serialize_runner_stream_event(event: dict[str, Any]) -> dict[str, Any]:
    """Serialize only the public envelope and its typed ``kind + data`` payload.

    Identity and timestamps originate in the Runner, never in a transport.
    Recursive conversion preserves committed steps, results and tool call IDs
    without rebuilding a second event schema or leaking storage details.
    """
    common = ("kind", "event_id", "timestamp", "runner_id", "agent_id", "agent_name", "agent_role")
    payload = {key: _json_value(event.get(key)) for key in common}
    for key in ("round_id", "step_num"):
        if event.get(key) is not None:
            payload[key] = _json_value(event[key])
    payload["data"] = _json_value(event.get("data") or {})
    return payload


def _json_value(value: Any) -> Any:
    """Flatten nested SDK values while retaining their public field identities."""
    if isinstance(value, ActionStep):
        return serialize_action_step(value)
    if isinstance(value, ObservationImage):
        return _serialize_observation_image(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_value(to_dict())
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _serialize_observation_image(img: ObservationImage) -> dict[str, Any]:
    """Flatten an observation image object into the schema consumed by the CLI."""
    return {
        "image_url": img.ensure_image_url(),
        "description": str(img.description or ""),
    }
