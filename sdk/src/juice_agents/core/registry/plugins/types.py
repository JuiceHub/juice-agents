"""Plugin manifest、发现结果和校验错误的强类型定义。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PLUGIN_MANIFEST_RELATIVE_PATH = Path(".juice-plugin") / "plugin.json"
_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalize_plugin_name(value: Any) -> str:
    """Return a filesystem-safe plugin id shared by config and manifests."""
    name = str(value or "").strip()
    if not name or not _PLUGIN_NAME_RE.fullmatch(name) or name in {".", ".."}:
        raise ValueError("plugin name 仅允许字母、数字、点、下划线和中划线，且必须以字母或数字开头")
    return name


def normalize_plugin_relative_path(value: Any, *, field_name: str = "path") -> str:
    """Validate a manifest path without touching the filesystem.

    Plugin paths always use POSIX separators so manifests remain portable.  A
    caller must still resolve the path through :func:`resolve_plugin_path` to
    defend against symlinks escaping the plugin directory.
    """
    text = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{field_name} 必须是 plugin 内部的安全相对路径")
    return path.as_posix()


def resolve_plugin_path(root: Path, relative_path: str, *, must_exist: bool = False) -> Path:
    """Resolve a plugin-relative path and reject traversal and symlink escape."""
    normalized = normalize_plugin_relative_path(relative_path)
    root = Path(root).resolve()
    candidate = (root / normalized).resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"plugin path 越界: {relative_path}")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(f"plugin path 不存在: {candidate}")
    return candidate


def _normalize_paths(value: Any, *, field_name: str, default: Iterable[str] = ()) -> tuple[str, ...]:
    raw = list(default) if value is None else value
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{field_name} 必须为 string 或 list[string]")
    paths = tuple(normalize_plugin_relative_path(item, field_name=field_name) for item in raw)
    if len(paths) != len(set(paths)):
        raise ValueError(f"{field_name} 不能包含重复路径")
    return paths


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """V1 `.juice-plugin/plugin.json` contract: plugins only bundle Skills."""

    name: str
    schema_version: int = 1
    version: str = "0.1.0"
    description: str = ""
    skills: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Any, *, directory_name: str | None = None) -> "PluginManifest":
        if not isinstance(value, dict):
            raise ValueError("plugin manifest 必须为 JSON object")
        payload = dict(value)
        name = normalize_plugin_name(payload.get("name"))
        if directory_name is not None and name != normalize_plugin_name(directory_name):
            raise ValueError(f"plugin manifest name {name!r} 必须与目录名 {directory_name!r} 一致")
        schema_version = payload.get("schema_version", 1)
        if schema_version != 1:
            raise ValueError("plugin schema_version 当前仅支持 1")
        version = str(payload.get("version") or "0.1.0").strip()
        if not version or any(ch.isspace() for ch in version):
            raise ValueError("plugin version 必须为不含空白的非空字符串")

        unsupported = sorted(
            key for key in {"tools", "tool_configs", "mcp", "commands", "hooks"} if key in payload
        )
        if unsupported:
            raise ValueError(
                "plugin 当前仅支持 skills，暂不支持字段: " + ", ".join(unsupported)
            )
        known = {"schema_version", "name", "version", "description", "skills"}
        return cls(
            name=name,
            schema_version=1,
            version=version,
            description=str(payload.get("description") or ""),
            skills=_normalize_paths(payload.get("skills"), field_name="skills"),
            metadata={key: item for key, item in payload.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "description": self.description,
        }
        if self.skills:
            payload["skills"] = list(self.skills)
        payload.update(dict(self.metadata))
        return payload


@dataclass(frozen=True, slots=True)
class PluginRecord:
    """Validated plugin plus its discovery layer and enablement state."""

    manifest: PluginManifest
    root: Path
    source: str
    enabled: bool = True

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def manifest_path(self) -> Path:
        return self.root / PLUGIN_MANIFEST_RELATIVE_PATH

    def skill_roots(self) -> tuple[Path, ...]:
        roots = tuple(resolve_plugin_path(self.root, item, must_exist=True) for item in self.manifest.skills)
        for root in roots:
            if not root.is_dir():
                raise ValueError(f"plugin skills 路径必须是目录: {root}")
            if root.is_symlink():
                raise ValueError(f"plugin skills 根目录不能是符号链接: {root}")
        return roots

    def skill_names(self) -> tuple[str, ...]:
        """Return stable CLI/prompt aliases for all bundled ``SKILL.md`` files."""
        names: set[str] = set()
        for root in self.skill_roots():
            for skill_file in root.rglob("SKILL.md"):
                relative = skill_file.relative_to(root)
                parents_inside_root = [root / Path(*relative.parts[:index]) for index in range(1, len(relative.parts))]
                if skill_file.is_symlink() or any(parent.is_symlink() for parent in parents_inside_root):
                    raise ValueError(f"plugin Skill 不允许符号链接: {skill_file}")
                names.add(skill_file.parent.name)
        return tuple(sorted(names))

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-friendly discovery metadata for CLI/web integrations."""
        return {
            **self.manifest.to_dict(),
            "root": str(self.root),
            "source": self.source,
            "enabled": self.enabled,
            "skill_roots": [str(path) for path in self.skill_roots()],
            "skill_names": list(self.skill_names()),
        }


# Public integrations use the metadata-oriented name, while internal code uses
# `PluginRecord` to emphasize that the value came from one discovery source.
PluginMetadata = PluginRecord


@dataclass(frozen=True, slots=True)
class PluginConflict:
    name: str
    locations: tuple[Path, ...]

    def message(self) -> str:
        return f"plugin {self.name!r} 在多个来源中重复: " + ", ".join(str(path) for path in self.locations)


class PluginConflictError(ValueError):
    """Raised rather than silently shadowing a plugin from another layer."""

    def __init__(self, conflicts: Iterable[PluginConflict]) -> None:
        self.conflicts = tuple(conflicts)
        super().__init__("; ".join(item.message() for item in self.conflicts))


__all__ = [
    "PLUGIN_MANIFEST_RELATIVE_PATH",
    "PluginConflict",
    "PluginConflictError",
    "PluginManifest",
    "PluginMetadata",
    "PluginRecord",
    "normalize_plugin_name",
    "normalize_plugin_relative_path",
    "resolve_plugin_path",
]
