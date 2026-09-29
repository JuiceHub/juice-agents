"""Graph registry public types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from juice_agents.core.graph.types import GraphBuildContext

GraphSource = Literal["builtin", "local"]


@dataclass(frozen=True, slots=True)
class GraphMetadata:
    """Serializable metadata discovered without executing graph source."""

    name: str
    description: str
    source: GraphSource
    path: Path | None
    read_only: bool = False
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    source_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "path": "" if self.path is None else str(self.path),
            "read_only": self.read_only,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "source_hash": self.source_hash,
        }


__all__ = ["GraphBuildContext", "GraphMetadata", "GraphSource"]
