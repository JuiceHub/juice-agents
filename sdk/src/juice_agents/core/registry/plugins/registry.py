"""Layered, read-only plugin discovery for runtime assembly."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from juice_agents.core.config.context import ConfigurationContext

from .types import (
    PLUGIN_MANIFEST_RELATIVE_PATH,
    PluginConflict,
    PluginConflictError,
    PluginManifest,
    PluginRecord,
    normalize_plugin_name,
    resolve_plugin_path,
)

logger = logging.getLogger(__name__)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"plugin manifest 不是合法 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"plugin manifest 必须为 JSON object: {path}")
    return dict(value)


class PluginRegistry:
    """Discover plugins from the configured workspace/project/user layers.

    Discovery order is workspace, project, then user. Duplicate ids are errors,
    not implicit overrides, because silently mixing code and skills from two
    installations makes agent behavior impossible to audit.

    Plugin V1 is deliberately read-only and only contributes Skill roots.
    Tool/MCP/command/hook contributions fail manifest validation until those
    contracts are implemented explicitly.
    """

    def __init__(
        self,
        workspace_dir: str | Path | None = None,
        *,
        project_dir: str | Path | None = None,
        user_plugins_dir: str | Path | None = None,
        config_context: ConfigurationContext | None = None,
        max_view_bytes: int = 1_048_576,
    ) -> None:
        self.config_context = config_context or ConfigurationContext.from_workspace(workspace_dir)
        workspace = self.config_context.workspace_dir
        self.workspace_dir = Path(workspace).resolve() if workspace is not None else None
        self.project_dir = Path(project_dir).resolve() if project_dir is not None else (self.workspace_dir or Path.cwd().resolve())
        self.local_root = (self.workspace_dir or self.project_dir) / ".juice" / "plugins"
        self.project_root = self.project_dir / ".agents" / "plugins"
        self.user_root = Path(user_plugins_dir).expanduser().resolve() if user_plugins_dir else Path.home() / ".agents" / "plugins"
        if not isinstance(max_view_bytes, int) or max_view_bytes <= 0:
            raise ValueError("max_view_bytes 必须为正整数")
        self.max_view_bytes = max_view_bytes

    @classmethod
    def default(cls, workspace_dir: str | Path | None = None) -> "PluginRegistry":
        return cls(workspace_dir=workspace_dir)

    def _plugin_settings(self) -> tuple[set[str] | None, set[str]]:
        try:
            raw = self.config_context.read_merged_config().get("plugins", {})
        # Registry creation is also used by tests/legacy callers with non-YAML
        # runtime files. Match SkillRegistry's graceful config fallback here;
        # malformed explicit `plugins` values are still validated below.
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError("plugins 配置必须为 object")

        def names(key: str) -> set[str] | None:
            value = raw.get(key)
            if value is None:
                return None
            if isinstance(value, bool):
                return None if value else set()
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"plugins.{key} 必须为 list[string] 或 boolean")
            return {normalize_plugin_name(item) for item in value}

        enabled = names("enabled")
        disabled = names("disabled") or set()
        # disabled is an explicit deny overlay and intentionally wins over an
        # allow-list entry, which makes temporary deactivation concise.
        return enabled, disabled

    def _read_record(
        self,
        root: Path,
        *,
        source: str,
        enabled: bool = True,
        expected_name: str | None = None,
    ) -> PluginRecord:
        if root.is_symlink():
            raise ValueError(f"plugin 根目录不能是符号链接: {root}")
        manifest_path = root / PLUGIN_MANIFEST_RELATIVE_PATH
        manifest = PluginManifest.from_dict(
            _read_json_object(manifest_path),
            directory_name=expected_name or root.name,
        )
        record = PluginRecord(manifest=manifest, root=root.resolve(), source=source, enabled=enabled)
        # Resolve the only supported contribution now so a broken plugin fails
        # discovery rather than much later during prompt construction.
        record.skill_roots()
        record.skill_names()
        return record

    def _discover_all(self) -> list[PluginRecord]:
        records: list[PluginRecord] = []
        for source, base in (("workspace", self.local_root), ("project", self.project_root), ("user", self.user_root)):
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir(), key=lambda item: item.name):
                if child.name.startswith(".") or not child.is_dir():
                    continue
                normalize_plugin_name(child.name)
                manifest = child / PLUGIN_MANIFEST_RELATIVE_PATH
                if manifest.is_file():
                    records.append(self._read_record(child, source=source))
        return records

    def diagnose_conflicts(self) -> list[PluginConflict]:
        grouped: dict[str, list[Path]] = {}
        for record in self._discover_all():
            grouped.setdefault(record.name, []).append(record.root)
        return [
            PluginConflict(name=name, locations=tuple(paths))
            for name, paths in sorted(grouped.items())
            if len(paths) > 1
        ]

    def list_plugins(self, *, enabled_only: bool = False) -> list[PluginRecord]:
        records = self._discover_all()
        grouped: dict[str, list[PluginRecord]] = {}
        for record in records:
            grouped.setdefault(record.name, []).append(record)
        conflicts = [PluginConflict(name, tuple(item.root for item in values)) for name, values in grouped.items() if len(values) > 1]
        if conflicts:
            raise PluginConflictError(conflicts)

        allow, deny = self._plugin_settings()
        resolved = [
            PluginRecord(
                manifest=item.manifest,
                root=item.root,
                source=item.source,
                enabled=item.name not in deny and (allow is None or item.name in allow),
            )
            for item in records
        ]
        if enabled_only:
            resolved = [item for item in resolved if item.enabled]
        return sorted(resolved, key=lambda item: item.name)

    # Integration-facing aliases deliberately return metadata instead of bare
    # names: source and enablement are important when presenting plugin state.
    def list(self, include_disabled: bool = False) -> list[PluginRecord]:
        return self.list_plugins(enabled_only=not include_disabled)

    def read(self, name: str, *, require_enabled: bool = False) -> PluginRecord:
        normalized = normalize_plugin_name(name)
        for record in self.list_plugins(enabled_only=require_enabled):
            if record.name == normalized:
                return record
        raise FileNotFoundError(f"plugin 不存在或未启用: {normalized}")

    def get(self, name: str) -> PluginRecord:
        return self.read(name)

    @staticmethod
    def _copy_record(record: PluginRecord) -> PluginRecord:
        """Detach manifest metadata before returning a Registry declaration."""

        return PluginRecord(
            manifest=PluginManifest.from_dict(record.manifest.to_dict()),
            root=Path(record.root),
            source=record.source,
            enabled=record.enabled,
        )

    def resolve(self, raw: str | PluginRecord) -> PluginRecord:
        """Resolve a discovered plugin without loading any plugin runtime code."""

        if isinstance(raw, PluginRecord):
            return self._copy_record(raw)
        if not isinstance(raw, str):
            raise TypeError("plugin 只支持通过 name 或 PluginRecord 解析")
        return self._copy_record(self.read(raw))

    def validate(self, raw: str | PluginRecord) -> PluginRecord:
        """Re-read and validate the manifest and contribution paths statically."""

        candidate = self.resolve(raw)
        discovered = self.read(candidate.name)
        if discovered.root != candidate.root:
            raise ValueError(f"plugin 不属于当前 Registry 目录层级: {candidate.root}")
        validated = self._read_record(
            candidate.root,
            source=discovered.source,
            enabled=discovered.enabled,
            expected_name=candidate.name,
        )
        return self._copy_record(validated)

    def instantiate(self, raw: str | PluginRecord, **runtime_kwargs: Any) -> PluginRecord:
        """Return a fresh immutable plugin descriptor for capability assembly."""

        if runtime_kwargs:
            unexpected = ", ".join(sorted(runtime_kwargs))
            raise TypeError(f"PluginRegistry.instantiate 不接受运行时参数: {unexpected}")
        record = self.validate(raw)
        logger.info("实例化 fresh plugin descriptor: name=%s source=%s", record.name, record.source)
        return record

    def view(self, name: str, file_path: str | None = None) -> dict[str, Any] | str:
        """View manifest metadata or one UTF-8 plugin file without path escape."""
        record = self.read(name, require_enabled=True)
        if file_path is None:
            return record.to_dict()
        path = resolve_plugin_path(record.root, file_path, must_exist=True)
        if not path.is_file():
            raise IsADirectoryError(f"plugin view 只支持文件: {path}")
        if path.stat().st_size > self.max_view_bytes:
            raise ValueError(f"plugin file 超过 view 大小限制 {self.max_view_bytes} bytes: {path}")
        return path.read_text(encoding="utf-8")

    def enabled_skill_roots(self) -> list[Path]:
        return [path for item in self.list_plugins(enabled_only=True) for path in item.skill_roots()]

    def skill_roots(self) -> list[Path]:
        return self.enabled_skill_roots()

    def diagnostics(self) -> list[dict[str, Any]]:
        """Return explicit, non-throwing discovery diagnostics for UI/CLI use."""
        diagnostics: list[dict[str, Any]] = []
        valid: list[PluginRecord] = []
        for source, base in (("workspace", self.local_root), ("project", self.project_root), ("user", self.user_root)):
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir(), key=lambda item: item.name):
                if child.name.startswith(".") or not child.is_dir():
                    continue
                try:
                    valid.append(self._read_record(child, source=source))
                except Exception as exc:
                    diagnostics.append({"level": "error", "source": source, "path": str(child), "message": str(exc)})
        grouped: dict[str, list[PluginRecord]] = {}
        for record in valid:
            grouped.setdefault(record.name, []).append(record)
        for name, records in sorted(grouped.items()):
            if len(records) > 1:
                diagnostics.append({
                    "level": "error",
                    "name": name,
                    "paths": [str(item.root) for item in records],
                    "message": PluginConflict(name, tuple(item.root for item in records)).message(),
                })
        try:
            self._plugin_settings()
        except ValueError as exc:
            diagnostics.append({"level": "error", "source": "config", "message": str(exc)})
        return diagnostics

__all__ = ["PluginRegistry"]
