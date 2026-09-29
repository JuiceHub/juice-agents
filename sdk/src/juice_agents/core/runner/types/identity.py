"""Opaque identifiers for Runner and Agent runtime records."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

_RUNNER_ID_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


def new_runner_id(mode_id: str, root_agent_name: str) -> str:
    """Create a time-sortable opaque Runner id.

    The arguments document the composition call site but are deliberately not
    encoded in persistent identity; all semantics are in schema-5 manifest
    data instead of filesystem names.
    """

    del mode_id, root_agent_name
    timestamp = datetime.now(timezone.utc).strftime(_RUNNER_ID_TIMESTAMP_FORMAT)
    return f"{timestamp}-{uuid4().hex[:8]}"


def new_agent_id() -> str:
    """Create an opaque Agent runtime id owned by ``AgentManager``."""

    return f"agent:{uuid4().hex}"


def new_root_agent_id() -> str:
    """Semantic alias for the root Agent's initial id."""

    return new_agent_id()


__all__ = ["new_agent_id", "new_root_agent_id", "new_runner_id"]
