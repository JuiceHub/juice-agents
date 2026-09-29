"""Static Team declarations and member configuration sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from juice_agents.core.registry.common.store import normalize_name


@dataclass(frozen=True, slots=True)
class TeamManifest:
    """Persisted Team membership without embedding Agent configurations.

    An absent ``shared_agent_names`` is the original schema-2 spelling: each
    member refers to a shared Agent with the same name. An explicit mapping
    identifies shared Agent references; any other member has a private YAML
    declaration under this Team. This distinction lets existing manifests
    continue to load while new Teams choose each member's source explicitly.
    """

    team_name: str
    member_names: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    schema_version: int = 2
    shared_agent_names: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        team_name = normalize_name(str(self.team_name or "").strip())
        member_names = tuple(
            normalize_name(str(name or "").strip())
            for name in tuple(self.member_names or ())
        )
        if len(set(member_names)) != len(member_names):
            raise ValueError("TeamManifest.member_names 中存在重名成员")
        shared_agent_names = self.shared_agent_names
        if shared_agent_names is not None:
            if not isinstance(shared_agent_names, Mapping):
                raise ValueError("TeamManifest.shared_agent_names 必须为 object")
            normalized_sources = {
                normalize_name(member_name): normalize_name(agent_name)
                for member_name, agent_name in shared_agent_names.items()
            }
            if len(normalized_sources) != len(shared_agent_names):
                raise ValueError("TeamManifest.shared_agent_names 中存在重名成员")
            unknown = sorted(set(normalized_sources) - set(member_names))
            if unknown:
                raise ValueError(f"TeamManifest.shared_agent_names 包含未知成员: {unknown}")
            shared_agent_names = MappingProxyType(normalized_sources)
        if int(self.schema_version) != 2:
            raise ValueError(
                "不支持的 TeamManifest schema_version: "
                f"{self.schema_version}；当前仅支持 schema 2"
            )
        object.__setattr__(self, "team_name", team_name)
        object.__setattr__(self, "member_names", member_names)
        object.__setattr__(self, "shared_agent_names", shared_agent_names)
        object.__setattr__(self, "description", str(self.description or ""))
        object.__setattr__(self, "schema_version", 2)

    def to_dict(self) -> dict[str, Any]:
        """Return the exact stable persisted shape for a Team definition."""

        payload = {
            "schema_version": self.schema_version,
            "team_name": self.team_name,
            "description": self.description,
            "member_names": list(self.member_names),
        }
        if self.shared_agent_names is not None:
            payload["shared_agent_names"] = dict(self.shared_agent_names)
        return payload

    def shared_agent_name(self, member_name: str) -> str | None:
        """Resolve a member's shared declaration, or ``None`` for private YAML."""

        normalized = normalize_name(member_name)
        if normalized not in self.member_names:
            raise KeyError(f"Team 成员不存在: {normalized}")
        if self.shared_agent_names is None:
            return normalized
        return self.shared_agent_names.get(normalized)

    def explicit_shared_agent_names(self) -> dict[str, str]:
        """Expand legacy same-name references before changing membership."""

        if self.shared_agent_names is None:
            return {name: name for name in self.member_names}
        return dict(self.shared_agent_names)

    to_persisted_dict = to_dict

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamManifest":
        if not isinstance(data, dict):
            raise ValueError("TeamManifest 必须为 object")
        allowed_fields = {"schema_version", "team_name", "description", "member_names", "shared_agent_names"}
        unknown_fields = sorted(set(data) - allowed_fields)
        if unknown_fields:
            raise ValueError(f"TeamManifest 包含未知字段: {unknown_fields}")
        member_names = data.get("member_names")
        if not isinstance(member_names, list):
            raise ValueError("TeamManifest.member_names 必须为数组")
        shared_agent_names = data.get("shared_agent_names")
        if "shared_agent_names" in data and not isinstance(shared_agent_names, dict):
            raise ValueError("TeamManifest.shared_agent_names 必须为 object")
        return cls(
            schema_version=int(data.get("schema_version", 0)),
            team_name=str(data.get("team_name") or ""),
            description=str(data.get("description") or ""),
            member_names=tuple(str(name or "") for name in member_names),
            shared_agent_names=shared_agent_names,
        )

    def copy_with(self, **changes: Any) -> "TeamManifest":
        payload = self.to_dict()
        payload.update(changes)
        return type(self).from_dict(payload)


# ``TeamConfig`` remains a public spelling for callers that construct a Team
# declaration in memory. It is an alias, never a second aggregate type.
TeamConfig = TeamManifest


__all__ = ["TeamConfig", "TeamManifest"]
