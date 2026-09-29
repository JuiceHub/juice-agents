"""Static Team declaration and member configuration Registry.

The Registry owns relation validation only. It does not construct a Team
runtime, coordinator, inbox, or task board; those are Runner-owned state.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.agents.store import AgentConfigStore
from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.registry.common.store import normalize_name

from .store import TeamConfigStore
from .types import TeamConfig, TeamManifest

logger = logging.getLogger(__name__)


class TeamRegistry:
    """Resolve Team manifests and each member's declared configuration source."""

    def __init__(
        self,
        workspace_dir: str | Path | None = None,
        *,
        config_context: ConfigurationContext | None = None,
    ) -> None:
        self.config_context = config_context or ConfigurationContext.from_workspace(workspace_dir)
        base_dir = self.config_context.workspace_dir or Path.cwd().resolve()
        self._store = TeamConfigStore(base_dir)
        # Use the AgentConfigStore directly. Team validation needs declarations,
        # not instantiated Agents or an AgentRegistry that might itself be
        # checking Team references during an Agent delete/save operation.
        self._agent_store = AgentConfigStore(self.config_context.agents_dir)

    @classmethod
    def default(cls, workspace_dir: str | Path | None = None) -> "TeamRegistry":
        return cls(workspace_dir)

    def list(self) -> list[str]:
        return self._store.list_names()

    def list_shared_member_candidates(self) -> list[dict[str, str]]:
        """List reusable Agent declarations that a new Team member can use.

        This is separate from Runner's ``agents_list``: that command shows
        members already selected for this Team, so an empty Team would offer
        no way to discover a shared candidate.
        """
        candidates = []
        for name in self._agent_store.list():
            config = self._agent_store.load(name)
            if (config.lifecycle == "persistent"
                    and (config.allowed_modes is None or "team" in config.allowed_modes)):
                candidates.append({"name": name, "description": config.description})
        return candidates

    def list_manifests(self) -> list[TeamManifest]:
        """Return detached static definitions for UI and cold queries."""

        return [TeamManifest.from_dict(manifest.to_dict()) for manifest in self._store.list()]

    def get_manifest(self, team_name: str) -> TeamManifest:
        return TeamManifest.from_dict(self._store.get_manifest(team_name).to_dict())

    def manifest_path(self, team_name: str) -> Path:
        """Return a declaration path for transport/UI display only."""

        return self._store.manifest_path(team_name)

    def member_config_dir(self, team_name: str) -> Path:
        """Private AgentConfigStore directory for Runner declaration metadata."""

        return self._store.member_config_dir(team_name)

    def member_config_path(self, team_name: str, member_name: str) -> Path:
        return self._store.member_config_path(team_name, member_name)

    def load_member_config(self, team_name: str, member_name: str) -> AgentConfig:
        """Resolve a member to its shared or Team-private Agent declaration."""

        manifest = self.get_manifest(team_name)
        shared_name = manifest.shared_agent_name(member_name)
        if shared_name is not None:
            config = self._agent_store.load(shared_name)
        else:
            config = self._store.load_member_config(team_name, member_name)
        return AgentConfig.from_dict(config)

    def resolve(self, raw: str | dict[str, Any] | TeamManifest) -> TeamManifest:
        """Resolve a name or serialized declaration without accepting live Agents."""

        if isinstance(raw, TeamManifest):
            return TeamManifest.from_dict(raw.to_dict())
        if isinstance(raw, dict):
            return TeamManifest.from_dict(raw)
        if isinstance(raw, str):
            name = raw.strip()
            if not name:
                raise ValueError("team name 不能为空")
            return self.get_manifest(name)
        raise TypeError("team 只支持通过 name / manifest object / TeamManifest 解析")

    def validate(self, raw: str | dict[str, Any] | TeamManifest) -> TeamManifest:
        """Validate every member source without creating runtime state."""

        manifest = self.resolve(raw)
        for member_name in manifest.member_names:
            try:
                shared_name = manifest.shared_agent_name(member_name)
                agent = (
                    self._agent_store.load(shared_name)
                    if shared_name is not None
                    else self._store.load_member_config(manifest.team_name, member_name)
                )
            except FileNotFoundError as exc:
                source = "全局 Agent" if shared_name is not None else "专属成员配置"
                raise ValueError(f"Team 引用了不存在的{source}: team={manifest.team_name} member={member_name}") from exc
            # ``allowed_modes`` is introduced on AgentConfig by the unified
            # declaration layer. Keep the check local to the relationship so a
            # Team can never publish an Agent explicitly excluded from team.
            allowed_modes = getattr(agent, "allowed_modes", None)
            if allowed_modes is not None and "team" not in allowed_modes:
                raise ValueError(
                    "Team 成员不适用于 team mode: "
                    f"team={manifest.team_name} agent={member_name}"
                )
        return TeamManifest.from_dict(manifest.to_dict())

    def instantiate(
        self,
        raw: str | dict[str, Any] | TeamManifest,
        **runtime_kwargs: Any,
    ) -> TeamManifest:
        """Return a fresh static descriptor for an AgentManager to use later."""

        if runtime_kwargs:
            unexpected = ", ".join(sorted(runtime_kwargs))
            raise TypeError(f"TeamRegistry.instantiate 不接受运行时参数: {unexpected}")
        manifest = self.validate(raw)
        logger.info(
            "实例化 fresh team descriptor: team=%s members=%d",
            manifest.team_name,
            len(manifest.member_names),
        )
        return manifest

    def create(self, raw: dict[str, Any] | TeamManifest) -> TeamManifest:
        manifest = self.validate(raw)
        self._store.create(manifest)
        return manifest

    def create_empty(self, team_name: str, description: str = "") -> TeamManifest:
        """Create an empty Team; members are added by the root when needed."""

        return self.create(TeamManifest(team_name=team_name, description=description, shared_agent_names={}))

    def add_member(
        self,
        team_name: str,
        member_name: str,
        *,
        agent_name: str | None = None,
        config: AgentConfig | dict[str, Any] | None = None,
    ) -> AgentConfig:
        """Persist one member with exactly one shared or private config source.

        The manifest is changed last. If its write fails after a private YAML
        was created, that new YAML is removed so a retry can use the name.
        """

        normalized_member = normalize_name(member_name)
        if (agent_name is None) == (config is None):
            raise ValueError("team_member_create 必须且只能提供 agent_name 或 config")
        manifest = self.get_manifest(team_name)
        if normalized_member in manifest.member_names:
            raise ValueError(f"Team 成员已存在: {normalized_member}")

        shared = manifest.explicit_shared_agent_names()
        if agent_name is not None:
            normalized_agent = normalize_name(agent_name)
            try:
                candidate = self._agent_store.load(normalized_agent)
            except FileNotFoundError as exc:
                raise ValueError(
                    f"Shared Agent {normalized_agent!r} does not exist; "
                    "use agents_list or provide a Team-local config"
                ) from exc
            shared[normalized_member] = normalized_agent
        else:
            if isinstance(config, dict):
                # Team mode owns its own persistence and visibility rules. A
                # small role config therefore works without asking the model
                # to repeat these fixed fields on every member creation.
                payload = dict(config)
                known_fields = set(AgentConfig.__dataclass_fields__)
                unknown = sorted(set(payload) - known_fields)
                if unknown:
                    raise ValueError(
                        f"Unknown Team member config fields: {unknown}; "
                        "use instructions, model_config_name and lifecycle"
                    )
                payload.setdefault("lifecycle", "persistent")
                payload.setdefault("allowed_modes", ["team"])
                candidate = AgentConfig.from_dict(payload, config_name=normalized_member)
            else:
                candidate = AgentConfig.from_dict(config, config_name=normalized_member)
        self._validate_member_candidate(candidate, team_name, normalized_member)
        # Validate referenced tools before either YAML or manifest is written.
        # Otherwise the member would appear dispatchable until its first turn.
        from juice_agents.core.registry.agents.registry import AgentRegistry

        candidate = AgentRegistry(config_context=self.config_context).validate(candidate)

        private_written = False
        try:
            if agent_name is None:
                self._store.save_member_config(team_name, normalized_member, candidate)
                private_written = True
            updated = manifest.copy_with(
                member_names=[*manifest.member_names, normalized_member],
                shared_agent_names=shared,
            )
            self._store.update(updated)
        except Exception:
            if private_written:
                self._store.delete_member_config(team_name, normalized_member)
            raise
        logger.info("已添加 Team 成员: team=%s member=%s", team_name, normalized_member)
        return AgentConfig.from_dict(candidate)

    def remove_member(self, team_name: str, member_name: str) -> None:
        """Remove a persisted member definition, including its private YAML.

        The caller must first release or roll back the member's runtime state.
        """

        manifest = self.get_manifest(team_name)
        normalized_member = normalize_name(member_name)
        shared_name = manifest.shared_agent_name(normalized_member)
        shared = manifest.explicit_shared_agent_names()
        shared.pop(normalized_member, None)
        updated = manifest.copy_with(
            member_names=[name for name in manifest.member_names if name != normalized_member],
            shared_agent_names=shared,
        )
        self._store.update(updated)
        if shared_name is None:
            self._store.delete_member_config(team_name, normalized_member)
        logger.info("已移除 Team 成员: team=%s member=%s", team_name, normalized_member)

    @staticmethod
    def _validate_member_candidate(config: AgentConfig, team_name: str, member_name: str) -> None:
        if config.allowed_modes is not None and "team" not in config.allowed_modes:
            raise ValueError(f"Team 成员不适用于 team mode: team={team_name} member={member_name}")
        if config.lifecycle != "persistent":
            raise ValueError(f"Team 成员必须使用 persistent lifecycle: team={team_name} member={member_name}")

    def update(self, raw: dict[str, Any] | TeamManifest) -> TeamManifest:
        manifest = self.validate(raw)
        self._store.update(manifest)
        return manifest

    def delete(self, team_name: str) -> None:
        self._store.delete(team_name)

    def referencing_teams(self, agent_name: str) -> tuple[str, ...]:
        return self._store.referencing_teams(normalize_name(agent_name))


__all__ = ["TeamConfig", "TeamRegistry"]
