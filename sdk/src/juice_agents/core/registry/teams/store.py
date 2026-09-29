"""Persistence for Team manifests and Team-private member declarations."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from juice_agents.core.registry.agents.store import AgentConfigStore
from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.registry.common.store import (
    as_config_path,
    normalize_name,
    read_yaml_config,
    require_yaml,
    write_yaml_config,
)

from .types import TeamManifest

logger = logging.getLogger(__name__)

_MANIFEST_FILE_NAME = "manifest.yaml"
_LEGACY_CONFIG_FILE_NAME = "config.yaml"


class TeamConfigStore:
    """Store Team manifests and private members under ``.juice/teams``.

    The former ``agents/teammates`` layout is intentionally not migrated or
    read. New private member declarations live in ``members/`` and are listed
    by the manifest, so each member has exactly one configuration source.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir).expanduser().resolve()
        self._teams_dir = self._base_dir / ".juice" / "teams"

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @property
    def teams_dir(self) -> Path:
        return self._teams_dir

    def team_dir(self, team_name: str) -> Path:
        return self._teams_dir / normalize_name(team_name)

    def manifest_path(self, team_name: str) -> Path:
        return self.team_dir(team_name) / _MANIFEST_FILE_NAME

    def member_config_dir(self, team_name: str) -> Path:
        """Return the private YAML directory without creating it."""

        return self.team_dir(team_name) / "members"

    def member_config_path(self, team_name: str, member_name: str) -> Path:
        return as_config_path(member_name, self.member_config_dir(team_name))

    def load_member_config(self, team_name: str, member_name: str) -> AgentConfig:
        """Load a Team-private Agent declaration; never inspect global Agents."""

        return AgentConfigStore(self.member_config_dir(team_name)).load(member_name)

    def save_member_config(
        self, team_name: str, member_name: str, config: AgentConfig | dict[str, Any]
    ) -> AgentConfig:
        """Create one private declaration and refuse to overwrite stale files."""

        self.get_manifest(team_name)
        path = self.member_config_path(team_name, member_name)
        if path.exists():
            raise ValueError(f"Team 成员配置已存在: {path}")
        candidate = AgentConfig.from_dict(config, config_name=member_name)
        saved = AgentConfigStore(self.member_config_dir(team_name)).save(candidate)
        logger.info("已保存 Team 专属成员配置: team=%s member=%s", team_name, member_name)
        return saved

    def delete_member_config(self, team_name: str, member_name: str) -> None:
        AgentConfigStore(self.member_config_dir(team_name)).delete(member_name)
        logger.info("已删除 Team 专属成员配置: team=%s member=%s", team_name, member_name)

    def legacy_config_path(self, team_name: str) -> Path:
        """Expose the known old aggregate location for inspection/cleanup."""

        return self.team_dir(team_name) / _LEGACY_CONFIG_FILE_NAME

    def exists(self, team_name: str) -> bool:
        return self.manifest_path(team_name).exists()

    def list(self) -> list[TeamManifest]:
        """Read committed manifests without creating directories or files."""

        require_yaml()
        if not self._teams_dir.exists():
            return []
        manifests: list[TeamManifest] = []
        for entry in sorted(self._teams_dir.iterdir()):
            if not entry.is_dir():
                continue
            path = entry / _MANIFEST_FILE_NAME
            if not path.exists():
                if self._is_legacy_team_dir(entry):
                    raise ValueError(
                        "发现不兼容的 Team 私有配置，请先清理后再查询: "
                        f"{entry}"
                    )
                # Non-Team folders are not declarations and remain invisible.
                continue
            manifests.append(self._read_manifest(entry.name))
        return manifests

    def list_names(self) -> list[str]:
        return [manifest.team_name for manifest in self.list()]

    def get(self, team_name: str) -> TeamManifest:
        return self.get_manifest(team_name)

    def get_manifest(self, team_name: str) -> TeamManifest:
        normalized = normalize_name(team_name)
        path = self.manifest_path(normalized)
        if not path.exists():
            legacy_dir = self.team_dir(normalized)
            if self._is_legacy_team_dir(legacy_dir):
                raise ValueError(
                    "Team 私有成员配置已废弃，拒绝自动迁移: "
                    f"{legacy_dir}"
                )
            raise FileNotFoundError(f"Team 配置不存在: {normalized}")
        return self._read_manifest(normalized)

    def create(self, manifest: TeamManifest | dict[str, Any]) -> Path:
        candidate = manifest if isinstance(manifest, TeamManifest) else TeamManifest.from_dict(manifest)
        if self.exists(candidate.team_name):
            raise ValueError(f"Team 配置已存在: {candidate.team_name}")
        team_dir = self.team_dir(candidate.team_name)
        if team_dir.exists() and any(team_dir.iterdir()):
            raise ValueError(
                "Team 目录含有未清理的旧数据，拒绝覆盖: "
                f"{team_dir}"
            )
        path = self.manifest_path(candidate.team_name)
        write_yaml_config(path, candidate.to_persisted_dict())
        logger.info("已保存 Team manifest: team=%s path=%s", candidate.team_name, path)
        return path

    def update(self, manifest: TeamManifest | dict[str, Any]) -> Path:
        candidate = manifest if isinstance(manifest, TeamManifest) else TeamManifest.from_dict(manifest)
        self.get_manifest(candidate.team_name)
        path = self.manifest_path(candidate.team_name)
        write_yaml_config(path, candidate.to_persisted_dict())
        logger.info("已更新 Team manifest: team=%s path=%s", candidate.team_name, path)
        return path

    def delete(self, team_name: str) -> None:
        normalized = normalize_name(team_name)
        if not self.exists(normalized):
            raise FileNotFoundError(f"Team 配置不存在: {normalized}")
        team_dir = self.team_dir(normalized)
        shutil.rmtree(team_dir)
        logger.info("已删除 Team manifest: team=%s", normalized)

    def referencing_teams(self, agent_name: str) -> tuple[str, ...]:
        """Return every Team that references one global Agent declaration."""

        normalized = normalize_name(agent_name)
        return tuple(
            manifest.team_name
            for manifest in self.list()
            if normalized in manifest.explicit_shared_agent_names().values()
        )

    def _read_manifest(self, team_name: str) -> TeamManifest:
        path = self.manifest_path(team_name)
        manifest = TeamManifest.from_dict(read_yaml_config(path))
        if manifest.team_name != normalize_name(team_name):
            raise ValueError(
                "manifest team_name 与目录不一致: "
                f"{manifest.team_name!r} != {team_name!r}"
            )
        return manifest

    @staticmethod
    def _is_legacy_team_dir(team_dir: Path) -> bool:
        """Recognize only previously supported Team-private layouts."""

        return (team_dir / _LEGACY_CONFIG_FILE_NAME).exists() or (team_dir / "agents").exists()


__all__ = ["TeamConfigStore"]
