"""Serialization helpers that turn core action steps into JSON-RPC payloads."""

from __future__ import annotations

from typing import Any

from juice_agents.core.agent.agent_type import ObservationImage
from juice_agents.core.agent.sessions.steps import ActionStep


def serialize_action_step(step: ActionStep) -> dict[str, Any]:
    """Convert an ActionStep into plain JSON-serializable values for transport."""
    return {
        "step_num": int(step.step_num),
        "model_output": str(step.model_output or ""),
        "thought": str(step.thought or ""),
        "tool_calls": [dict(item) for item in list(step.tool_calls or []) if isinstance(item, dict)],
        "code_action": str(step.code_action or ""),
        "reasoning_content": str(step.reasoning_content or ""),
        "observations": list(step.observations or []),
        "observation_limits": list(step.observation_limits or []),
        "request_bytes": int(step.request_bytes or 0),
        "usage": dict(step.usage) if isinstance(step.usage, dict) else None,
        "observation_images": [
            _serialize_observation_image(img)
            for img in (step.observations_images or [])
        ],
        "attachments": [dict(att) for att in (step.attachments or [])],
        "error": str(step.error) if step.error else None,
        "round_outcome": step.round_outcome,
        "output": step.output,
    }


def serialize_runner_stream_event(event: dict[str, Any]) -> dict[str, Any]:
    """Convert a RunnerStreamEvent into transport-safe JSON values."""

    action_step = event.get("action_step")
    # TeamManager emits domain data at the event root. Keep the wire format's
    # existing team_event envelope so stdio and WebSocket clients share one
    # shape, while accepting older callers that already provide the envelope.
    team_event = event.get("team_event")
    if event.get("kind") == "team_update" and team_event is None:
        team_event = {
            "team_name": event.get("team_name"),
            "actor": event.get("agent_name") or event.get("actor_name") or "team",
            "update": event.get("update") or {},
            "snapshot": event.get("snapshot") or {},
        }
    return {
        "kind": str(event.get("kind") or ""),
        "permission_mode": str(event.get("permission_mode") or ""),
        "agent_mode": str(event.get("agent_mode") or event.get("mode_id") or ""),
        "runner_id": str(event.get("runner_id") or ""),
        "actor_name": event.get("actor_name") or event.get("agent_name"),
        "actor_role": event.get("actor_role") or event.get("agent_role"),
        "agent_name": event.get("agent_name"),
        "agent_id": event.get("agent_id"),
        "agent_role": event.get("agent_role"),
        "step_num": event.get("step_num"),
        "action_step": (
            serialize_action_step(action_step)
            if isinstance(action_step, ActionStep)
            else action_step
        ),
        "team_event": team_event,
        "lifecycle_event": event.get("lifecycle_event"),
        "round_id": str(event.get("round_id") or ""),
        "outcome": str(event.get("outcome") or ""),
        "output": event.get("output"),
        "reason": str(event.get("reason") or ""),
        "stop_reason": str(event.get("stop_reason") or ""),
    }


def _serialize_observation_image(img: ObservationImage) -> dict[str, Any]:
    """Flatten an observation image object into the schema consumed by the CLI."""
    return {
        "image_url": img.ensure_image_url(),
        "description": str(img.description or ""),
    }
