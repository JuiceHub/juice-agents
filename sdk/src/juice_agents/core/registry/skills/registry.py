"""Hermes 风格 skills 发现、读取与本地管理。"""

from __future__ import annotations

from copy import deepcopy
import json
import logging
import os
import re
import shutil
import time
from importlib.resources import files
from pathlib import Path
from typing import Any

from juice_agents.core.config.runtime_config import DEFAULT_RUNTIME_CONFIG_PATH, read_runtime_config
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.common.store import atomic_write_text

from .types import SkillMetadata, SkillSource

try:
    import yaml
except Exception:  # pragma: no cover - yaml 是可选运行时依赖
    yaml = None  # type: ignore

logger = logging.getLogger(__name__)

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_EXCLUDED_DIRS = {".git", ".github", ".hub", "__pycache__"}
_SUPPORTING_DIRS = {"references", "templates", "assets", "scripts"}
_MAX_SKILL_MD_CHARS = 100_000
_MAX_SUPPORTING_FILE_BYTES = 1024 * 1024
_DANGEROUS_PATTERNS = (
    r"rm\s+-rf\s+/",
    r"curl\s+[^|;]*\|\s*(?:sh|bash)",
    r"wget\s+[^|;]*\|\s*(?:sh|bash)",
    r"chmod\s+777",
)


def default_builtin_skills_dir() -> Path:
    """Return the immutable Skill resources bundled in the SDK wheel."""

    return Path(str(files("juice_agents._assets").joinpath("skills"))).resolve()


def default_local_skills_dir() -> Path:
    return Path.cwd().resolve() / ".juice" / "skills"


def _workspace_dir_from_local_dir(local_dir: Path) -> Path | None:
    """Infer `<workspace>` from `<workspace>/.juice/skills` when possible."""

    try:
        if local_dir.name == "skills" and local_dir.parent.name == ".juice":
            return local_dir.parent.parent
    except IndexError:
        return None
    return None


def default_project_external_skills_dir(local_dir: str | Path | None = None) -> Path:
    """
    解析当前 workspace 的项目级 skills 目录。

    `local_dir` 默认是 `<workspace>/.juice/skills`，因此向上回退两级即可稳定
    定位到 `<workspace>/.agents/skills`。这样调用方只需要提供 local_dir，
    无需在 CLI、agent、tool 三层重复维护同样的路径拼装逻辑。
    """

    resolved_local_dir = _expand_path(local_dir) if local_dir is not None else default_local_skills_dir()
    workspace_root = resolved_local_dir.parent.parent
    return workspace_root / ".agents" / "skills"


def default_user_external_skills_dir() -> Path:
    """解析用户级全局 skills 目录，作为默认 external source。"""

    return Path.home().expanduser().resolve() / ".agents" / "skills"


def _expand_path(raw: str | Path) -> Path:
    return Path(os.path.expandvars(str(raw))).expanduser().resolve()


def _normalize_name(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SKILL_NAME_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} 仅允许小写字母、数字、点、下划线、中划线，且最长 64 字符")
    return normalized


def _frontmatter(content: str) -> tuple[dict[str, Any], str, bool]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content, False
    end_idx: int | None = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}, content, False

    raw_frontmatter = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    if yaml is not None:
        try:
            parsed = yaml.safe_load(raw_frontmatter) or {}
            if isinstance(parsed, dict):
                return dict(parsed), body, True
        except Exception:
            logger.warning("解析 skill frontmatter 失败，退化到简单 key:value")

    data: dict[str, Any] = {}
    for line in raw_frontmatter.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data, body, True


def _body_description(body: str) -> str:
    paragraph: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#"):
            continue
        paragraph.append(stripped)
    return " ".join(paragraph).strip()


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _conditions(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _string_tuple(item) for key, item in value.items()}


def _visibility_values(*sources: Any, key: str) -> tuple[str, ...]:
    """从多个 metadata 层级读取同一个 Hermes visibility 字段。"""
    values: list[str] = []
    for source in sources:
        if isinstance(source, dict):
            values.extend(_string_tuple(source.get(key)))
    return tuple(values)


def _atomic_write(path: Path, content: str) -> None:
    """复用 registry 公共原子写入，避免 Skill 与 YAML 的落盘语义分叉。"""
    atomic_write_text(path, content)


class SkillRegistry:
    """发现内置、外部和 workspace-local skills 的统一入口。

    性能优化：
    - 懒加载：初始化时不扫描，首次调用时才构建索引
    - 增量更新：基于目录 mtime 检测变化，仅在需要时重新扫描
    """

    def __init__(
        self,
        *,
        local_dir: str | Path | None = None,
        builtin_dir: str | Path | None = None,
        external_dirs: list[str | Path] | None = None,
        config_path: str | Path | None = None,
        workspace_dir: str | Path | None = None,
        platform: str = "cli",
        allow_names: list[str] | tuple[str, ...] | None = None,
        deny_names: list[str] | tuple[str, ...] | None = None,
        available_tools: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> None:
        self.config_path = Path(config_path) if config_path is not None else DEFAULT_RUNTIME_CONFIG_PATH
        self.platform = str(platform or "cli").strip() or "cli"
        self.local_dir = _expand_path(local_dir) if local_dir is not None else default_local_skills_dir()
        self.builtin_dir = _expand_path(builtin_dir) if builtin_dir is not None else default_builtin_skills_dir()
        self.workspace_dir = (
            _expand_path(workspace_dir)
            if workspace_dir is not None
            else _workspace_dir_from_local_dir(self.local_dir)
        )
        self._explicit_external_dirs = list(external_dirs or [])
        self.allow_names = {
            str(item).strip().lower()
            for item in tuple(allow_names or ())
            if str(item).strip()
        }
        self.deny_names = {
            str(item).strip().lower()
            for item in tuple(deny_names or ())
            if str(item).strip()
        }
        self.available_tools = tuple(str(item).strip() for item in tuple(available_tools or ()) if str(item).strip())
        self._config = self._load_config()

        # 懒加载状态
        self._index_cache: dict[str, SkillMetadata] | None = None
        self._index_mtime: float = 0
        self._dir_mtimes: dict[Path, float] = {}

    @classmethod
    def default(cls, **kwargs: Any) -> "SkillRegistry":
        return cls(**kwargs)

    def _load_config(self) -> dict[str, Any]:
        try:
            payload = read_runtime_config(self.config_path, workspace_dir=self.workspace_dir)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning("读取 skills 运行时配置失败: %s", exc)
            return {}
        raw = payload.get("skills", {})
        return dict(raw) if isinstance(raw, dict) else {}

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("enabled", True))

    @property
    def guard_agent_created(self) -> bool:
        return bool(self._config.get("guard_agent_created"))

    @property
    def template_vars_enabled(self) -> bool:
        return bool(self._config.get("template_vars", True))

    @property
    def configured_values(self) -> dict[str, Any]:
        raw = self._config.get("config", {})
        return dict(raw) if isinstance(raw, dict) else {}

    @property
    def external_dirs(self) -> list[Path]:
        raw_dirs = self._explicit_external_dirs
        if not raw_dirs:
            config_dirs = self._config.get("external_dirs", [])
            if isinstance(config_dirs, (list, tuple)):
                raw_dirs = list(config_dirs)
        # 默认 external sources 固定包含：
        # 1. 当前项目下只读的 `.agents/skills`
        # 2. 用户 home 下可复用的 `~/.agents/skills`
        # 额外配置项只是在这两个默认来源后继续追加。
        plugin_context = ConfigurationContext.from_workspace(
            self.workspace_dir,
            project_config_path=self.config_path,
        )
        from juice_agents.core.registry.plugins.registry import PluginRegistry

        plugin_skill_roots = PluginRegistry(
            workspace_dir=self.workspace_dir,
            project_dir=self.workspace_dir,
            config_context=plugin_context,
        ).skill_roots()
        ordered: list[Path] = [
            *plugin_skill_roots,
            default_project_external_skills_dir(self.local_dir),
            default_user_external_skills_dir(),
        ]
        seen = {path.resolve() for path in ordered}
        for item in raw_dirs:
            if not str(item).strip():
                continue
            resolved = _expand_path(item)
            if resolved in seen:
                continue
            ordered.append(resolved)
            seen.add(resolved)
        return ordered

    def _disabled_names(self) -> set[str]:
        values = set(_string_tuple(self._config.get("disabled")))
        platform_disabled = self._config.get("platform_disabled", {})
        if isinstance(platform_disabled, dict):
            values.update(_string_tuple(platform_disabled.get(self.platform)))
        return {item.lower() for item in values} | self.deny_names

    def invalidate(self) -> None:
        """显式使索引失效，供同一进程内的 capability refresh 立即重建。"""
        self._index_cache = None
        self._index_mtime = 0
        self._dir_mtimes.clear()

    def _is_visible_for_capabilities(self, meta: SkillMetadata) -> bool:
        """
        判断 skill 是否适用于当前 agent/CLI 能力。

        `requires_*` 表示必须全部满足；`fallback_for_*` 表示对应能力缺失时
        才展示该 fallback skill，避免正式工具可用时出现重复选择噪音。
        """
        top = meta.metadata
        metadata = top.get("metadata") if isinstance(top.get("metadata"), dict) else {}
        hermes = metadata.get("hermes") if isinstance(metadata.get("hermes"), dict) else {}
        conditions = dict(meta.conditions or {})
        requires_tools = _visibility_values(top, metadata, hermes, conditions, key="requires_tools")
        fallback_for_tools = _visibility_values(top, metadata, hermes, conditions, key="fallback_for_tools")
        available = set(self.available_tools)
        if requires_tools and not set(requires_tools).issubset(available):
            return False
        if fallback_for_tools and set(fallback_for_tools).issubset(available):
            return False
        return True

    def _iter_skill_files(self, root: Path) -> list[Path]:
        if not root.exists() or not root.is_dir():
            return []
        files: list[Path] = []
        for path in sorted(root.rglob("SKILL.md")):
            if any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts):
                continue
            files.append(path)
        return files

    def _category_for(self, root: Path, skill_file: Path) -> str | None:
        rel_parent = skill_file.parent.relative_to(root)
        parts = rel_parent.parts
        if len(parts) >= 2:
            return parts[0]
        return None

    def _parse_metadata(self, root: Path, skill_file: Path, *, source: SkillSource) -> SkillMetadata | None:
        try:
            content = skill_file.read_text(encoding="utf-8")
            frontmatter, body, _ = _frontmatter(content)
        except Exception as exc:
            logger.warning("读取 skill 失败 %s: %s", skill_file, exc)
            return None

        name = str(frontmatter.get("name") or skill_file.parent.name).strip().lower()
        try:
            name = _normalize_name(name, field_name="skill name")
        except ValueError:
            logger.warning("跳过非法 skill name: %s (%s)", name, skill_file)
            return None

        category = frontmatter.get("category")
        category_text = str(category).strip().lower() if category is not None else self._category_for(root, skill_file)
        if category_text:
            try:
                category_text = _normalize_name(category_text, field_name="skill category")
            except ValueError:
                logger.warning("跳过非法 skill category: %s (%s)", category_text, skill_file)
                return None
        description = str(frontmatter.get("description") or _body_description(body) or "").strip()
        platforms = _string_tuple(frontmatter.get("platforms"))
        if platforms and self.platform not in platforms:
            return None

        hermes_metadata = frontmatter.get("metadata")
        metadata = dict(hermes_metadata) if isinstance(hermes_metadata, dict) else {}
        conditions = _conditions(frontmatter.get("conditions") or metadata.get("conditions"))
        # Keep the lexical absolute path here. Resolving it would erase the
        # information that a source file or parent directory is a symlink,
        # which copy-on-write must reject before copying the whole artifact.
        lexical_skill_file = skill_file.absolute()
        return SkillMetadata(
            name=name,
            description=description,
            category=category_text,
            version=None if frontmatter.get("version") is None else str(frontmatter.get("version")),
            source=source,
            skill_dir=lexical_skill_file.parent,
            skill_file=lexical_skill_file,
            read_only=source != "local",
            platforms=platforms,
            conditions=conditions,
            metadata={key: value for key, value in frontmatter.items()},
        )

    def _scan_source(self, root: Path, *, source: SkillSource) -> list[SkillMetadata]:
        return [
            meta
            for skill_file in self._iter_skill_files(root)
            if (meta := self._parse_metadata(root, skill_file, source=source)) is not None
        ]

    def _is_index_fresh(self) -> bool:
        """
        检查索引是否新鲜（基于目录 mtime）

        返回：
            True: 索引有效，无需重新扫描
            False: 目录已变化，需要重新扫描
        """
        if self._index_cache is None:
            return False

        # 检查所有 skills 目录的 mtime
        for skills_dir in self._all_skills_dirs():
            if not skills_dir.exists():
                continue

            try:
                current_mtime = skills_dir.stat().st_mtime
                cached_mtime = self._dir_mtimes.get(skills_dir, 0)

                if current_mtime > cached_mtime:
                    logger.debug(f"Skills 目录已变化: {skills_dir}")
                    return False
            except Exception as e:
                logger.warning(f"无法检查目录 mtime: {skills_dir}, {e}")
                return False

        return True

    def _all_skills_dirs(self) -> list[Path]:
        """获取所有需要扫描的 skills 目录"""
        return [
            self.local_dir,
            self.builtin_dir,
            *self.external_dirs,
        ]

    def _refresh_index(self):
        """重新扫描并构建 skills 索引"""
        logger.debug("刷新 skills 索引")

        disabled = self._disabled_names()
        selected: dict[str, SkillMetadata] = {}

        # 记录当前目录的 mtime
        self._dir_mtimes.clear()

        for root, source in (
            (self.local_dir, "local"),
            (self.builtin_dir, "builtin"),
            *[(path, "external") for path in self.external_dirs],
        ):
            # 记录目录 mtime
            if root.exists():
                try:
                    self._dir_mtimes[root] = root.stat().st_mtime
                except Exception:
                    pass

            for meta in self._scan_source(root, source=source):  # type: ignore[arg-type]
                keys = {meta.name, meta.qualified_name.lower()}
                if keys & disabled:
                    continue
                if self.allow_names and not (keys & self.allow_names):
                    continue
                if not self._is_visible_for_capabilities(meta):
                    continue
                selected.setdefault(meta.name, meta)

        self._index_cache = selected
        self._index_mtime = time.time()
        logger.info(f"Skills 索引已刷新，共 {len(selected)} 个 skill")

    def _all_metadata(self, *, include_disabled: bool = False, ignore_global_enabled: bool = False) -> list[SkillMetadata]:
        if not self.enabled and not ignore_global_enabled:
            return []

        # 懒加载：仅在需要时构建索引
        if not self._is_index_fresh():
            self._refresh_index()

        # 如果需要包含 disabled，重新过滤
        if include_disabled or not self._index_cache:
            disabled = self._disabled_names()
            selected: dict[str, SkillMetadata] = {}
            for root, source in (
                (self.local_dir, "local"),
                (self.builtin_dir, "builtin"),
                *[(path, "external") for path in self.external_dirs],
            ):
                for meta in self._scan_source(root, source=source):  # type: ignore[arg-type]
                    keys = {meta.name, meta.qualified_name.lower()}
                    if not include_disabled and keys & disabled:
                        continue
                    if self.allow_names and not (keys & self.allow_names):
                        continue
                    if not self._is_visible_for_capabilities(meta):
                        continue
                    selected.setdefault(meta.name, meta)
            return sorted(selected.values(), key=lambda item: ((item.category or ""), item.name, item.source))

        return sorted(self._index_cache.values(), key=lambda item: ((item.category or ""), item.name, item.source))

    def list(self, *, category: str | None = None) -> list[SkillMetadata]:
        normalized_category = str(category or "").strip().lower()
        items = self._all_metadata()
        if normalized_category:
            items = [item for item in items if (item.category or "") == normalized_category]
        return items

    @staticmethod
    def _copy_metadata(meta: SkillMetadata) -> SkillMetadata:
        """Return a detached metadata value without retaining Registry cache data."""

        return SkillMetadata(
            name=meta.name,
            description=meta.description,
            category=meta.category,
            version=meta.version,
            source=meta.source,
            skill_dir=meta.skill_dir,
            skill_file=meta.skill_file,
            read_only=meta.read_only,
            platforms=tuple(meta.platforms),
            conditions={key: tuple(value) for key, value in meta.conditions.items()},
            metadata=deepcopy(meta.metadata),
        )

    def resolve(self, raw: str | SkillMetadata) -> SkillMetadata:
        """Resolve a Skill declaration; never load it into an Agent session."""

        if isinstance(raw, SkillMetadata):
            return self._copy_metadata(raw)
        if not isinstance(raw, str):
            raise TypeError("skill 只支持通过 name 或 SkillMetadata 解析")
        return self._copy_metadata(self.get(raw))

    def validate(self, raw: str | SkillMetadata) -> SkillMetadata:
        """Validate that metadata still points at a declared, readable Skill."""

        candidate = self.resolve(raw)
        # A caller-provided SkillMetadata must still correspond to a discovered
        # source.  This prevents an arbitrary filesystem path from bypassing
        # Registry source precedence and path-safety checks.
        for root, source in (
            (self.local_dir, "local"),
            (self.builtin_dir, "builtin"),
            *[(path, "external") for path in self.external_dirs],
        ):
            try:
                candidate.skill_file.relative_to(root)
            except ValueError:
                continue
            reparsed = self._parse_metadata(root, candidate.skill_file, source=source)  # type: ignore[arg-type]
            if reparsed is not None:
                return self._copy_metadata(reparsed)
        raise FileNotFoundError(f"skill 不存在或未被 Registry 发现: {candidate.qualified_name}")

    def instantiate(self, raw: str | SkillMetadata, **runtime_kwargs: Any) -> SkillMetadata:
        """Return a fresh Skill descriptor for a Manager to bind to an Agent."""

        if runtime_kwargs:
            unexpected = ", ".join(sorted(runtime_kwargs))
            raise TypeError(f"SkillRegistry.instantiate 不接受运行时参数: {unexpected}")
        metadata = self.validate(raw)
        logger.info("实例化 fresh skill descriptor: name=%s source=%s", metadata.name, metadata.source)
        return metadata

    def categories(self) -> list[str]:
        return sorted({item.category for item in self._all_metadata() if item.category})

    def category_descriptions(self) -> dict[str, str]:
        if not self.enabled:
            return {}
        descriptions: dict[str, str] = {}
        for root in [self.local_dir, self.builtin_dir, *self.external_dirs]:
            if not root.exists():
                continue
            for path in sorted(root.rglob("DESCRIPTION.md")):
                if any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts):
                    continue
                category = path.parent.relative_to(root).parts[0] if path.parent != root else path.parent.name
                if category not in descriptions:
                    descriptions[category] = _body_description(path.read_text(encoding="utf-8"))
        return descriptions

    def get(
        self,
        name: str,
        *,
        include_disabled: bool = False,
        ignore_global_enabled: bool = False,
    ) -> SkillMetadata:
        raw = str(name or "").strip()
        if not raw:
            raise ValueError("skill name 不能为空")
        path = _expand_path(raw)
        if path.exists() and path.name == "SKILL.md":
            root_candidates = [self.local_dir, self.builtin_dir, *self.external_dirs]
            for root in root_candidates:
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                if root == self.local_dir:
                    source: SkillSource = "local"
                elif root == self.builtin_dir:
                    source = "builtin"
                else:
                    source = "external"
                meta = self._parse_metadata(root, path, source=source)
                if meta is not None:
                    return meta
        normalized = raw.lower()
        for meta in self._all_metadata(
            include_disabled=include_disabled,
            ignore_global_enabled=ignore_global_enabled,
        ):
            if normalized in {meta.name, meta.qualified_name.lower()}:
                return meta
        raise FileNotFoundError(f"skill 不存在: {name}")

    def config_status(self) -> dict[str, Any]:
        """Return editable workspace skill state, including disabled skills."""

        disabled = self._disabled_names()
        skills = []
        for meta in self._all_metadata(include_disabled=True, ignore_global_enabled=True):
            keys = {meta.name, meta.qualified_name.lower()}
            skill_enabled = self.enabled and not bool(keys & disabled)
            payload = meta.to_dict()
            payload["enabled"] = skill_enabled
            skills.append(payload)
        return {
            "success": True,
            "enabled": self.enabled,
            "disabled": sorted(disabled),
            "skills": skills,
            "count": len(skills),
        }

    def _safe_skill_path(self, meta: SkillMetadata, file_path: str | None = None) -> Path:
        if not file_path:
            return meta.skill_file
        raw = Path(file_path)
        if raw.is_absolute() or ".." in raw.parts:
            raise ValueError("file_path 必须是 skill 内部相对路径，不能包含 ..")
        resolved = (meta.skill_dir / raw).resolve()
        try:
            resolved.relative_to(meta.skill_dir)
        except ValueError as exc:
            raise ValueError("file_path 越界") from exc
        return resolved

    def _available_files(self, meta: SkillMetadata) -> list[str]:
        files: list[str] = []
        for path in sorted(meta.skill_dir.rglob("*")):
            if path.is_file() and not any(part in _EXCLUDED_DIRS for part in path.relative_to(meta.skill_dir).parts):
                files.append(str(path.relative_to(meta.skill_dir)))
        return files

    def _linked_files(self, meta: SkillMetadata) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {"references": [], "templates": [], "assets": [], "scripts": [], "other": []}
        for rel in self._available_files(meta):
            if rel == "SKILL.md":
                continue
            first = Path(rel).parts[0]
            group = first if first in _SUPPORTING_DIRS else "other"
            grouped[group].append(rel)
        return grouped

    def _environment_status(self, names: Any) -> dict[str, bool]:
        return {name: bool(os.environ.get(name)) for name in _string_tuple(names)}

    def _credential_status(self, paths: Any) -> dict[str, bool]:
        return {item: _expand_path(item).exists() for item in _string_tuple(paths)}

    def _template_content(self, content: str, meta: SkillMetadata) -> str:
        if not self.template_vars_enabled:
            return content
        replacements = {
            "JUICE_SKILL_DIR": str(meta.skill_dir),
            "JUICE_SESSION_ID": os.environ.get("JUICE_SESSION_ID", ""),
            "HERMES_SKILL_DIR": str(meta.skill_dir),
            "HERMES_SESSION_ID": os.environ.get("JUICE_SESSION_ID", os.environ.get("HERMES_SESSION_ID", "")),
        }
        for key, value in replacements.items():
            content = content.replace("${" + key + "}", value)
        return content

    def view(
        self,
        name: str,
        *,
        file_path: str | None = None,
        include_disabled: bool = False,
        ignore_global_enabled: bool = False,
    ) -> dict[str, Any]:
        meta = self.get(
            name,
            include_disabled=include_disabled,
            ignore_global_enabled=ignore_global_enabled,
        )
        target = self._safe_skill_path(meta, file_path)
        if not target.exists() or not target.is_file():
            return {
                "success": False,
                "error": f"文件不存在: {file_path or 'SKILL.md'}",
                "available_files": self._available_files(meta),
                "skill": meta.to_dict(),
            }
        content = target.read_text(encoding="utf-8")
        rendered = self._template_content(content, meta)
        frontmatter, _, _ = _frontmatter(content if target.name == "SKILL.md" else "")
        return {
            "success": True,
            "name": meta.name,
            "qualified_name": meta.qualified_name,
            "content": rendered,
            "raw_content": content,
            "path": str(target),
            "skill_dir": str(meta.skill_dir),
            "metadata": meta.to_dict(),
            "linked_files": self._linked_files(meta),
            "available_files": self._available_files(meta),
            "required_environment_variables": self._environment_status(
                frontmatter.get("required_environment_variables")
                or meta.metadata.get("required_environment_variables")
            ),
            "required_credential_files": self._credential_status(
                frontmatter.get("required_credential_files")
                or meta.metadata.get("required_credential_files")
            ),
            "config": self.configured_values.get(meta.name) or self.configured_values.get(meta.qualified_name) or {},
        }

    def view_explicit(self, name: str, *, file_path: str | None = None) -> dict[str, Any]:
        """Read a user-requested Skill even when automatic discovery is disabled.

        Explicit invocation keeps the usual source order and safe path checks;
        it bypasses only the global/per-Skill filters used for automatic prompt
        exposure.
        """

        return self.view(
            name,
            file_path=file_path,
            include_disabled=True,
            ignore_global_enabled=True,
        )

    def _local_skill_dir(self, name: str, category: str | None = None) -> Path:
        skill_name = _normalize_name(name, field_name="skill name")
        if category:
            return self.local_dir / _normalize_name(category, field_name="skill category") / skill_name
        return self.local_dir / skill_name

    def _copy_to_local(self, meta: SkillMetadata) -> SkillMetadata:
        """Materialize a read-only skill as a workspace-local shadow.

        Copying the complete directory preserves scripts/references/assets. Any
        symlink is rejected so later edits cannot escape the new local root and
        accidentally mutate the original external or built-in source.
        """
        destination = self._local_skill_dir(meta.name, meta.category)
        if destination.exists():
            self._index_cache = None
            return self.get(meta.name)

        source_root: Path | None = None
        for root in [self.builtin_dir, *self.external_dirs]:
            try:
                meta.skill_dir.relative_to(root)
            except ValueError:
                continue
            source_root = root
            break
        if source_root is None:
            raise ValueError(f"Skill 来源不在已配置目录中: {meta.skill_dir}")

        relative_dir = meta.skill_dir.relative_to(source_root)
        lexical_parents = [
            source_root.joinpath(*relative_dir.parts[:index])
            for index in range(1, len(relative_dir.parts) + 1)
        ]
        for path in [*lexical_parents, meta.skill_file, *meta.skill_dir.rglob("*")]:
            if path.is_symlink():
                raise ValueError(f"Skill 写时复制不支持符号链接: {path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(meta.skill_dir, destination, copy_function=shutil.copy2)
        self._index_cache = None
        copied = self.get(meta.name)
        if copied.source != "local":  # pragma: no cover - defensive invariant
            raise RuntimeError(f"Skill 本地副本未获得最高优先级: {meta.name}")
        logger.info(
            "skill 写时复制: name=%s source=%s from=%s to=%s",
            meta.name,
            meta.source,
            meta.skill_dir,
            destination,
        )
        return copied

    def _editable_meta(self, name: str) -> SkillMetadata:
        meta = self.get(name)
        return meta if meta.source == "local" else self._copy_to_local(meta)

    def _local_meta_for_delete(self, name: str) -> SkillMetadata:
        meta = self.get(name)
        if meta.source != "local":
            raise PermissionError(
                "只读 Skill 来源不能删除；删除 .juice 本地副本后下层来源会重新显现，"
                "如需隐藏请使用 skills.disabled"
            )
        return meta

    def _validate_skill_markdown(self, content: str, *, expected_name: str | None = None) -> None:
        if len(content) > _MAX_SKILL_MD_CHARS:
            raise ValueError("SKILL.md 超过 100000 字符")
        frontmatter, _, has_frontmatter = _frontmatter(content)
        if not has_frontmatter or not str(frontmatter.get("name") or "").strip():
            raise ValueError("SKILL.md 必须包含 YAML frontmatter.name")
        if not str(frontmatter.get("description") or "").strip():
            raise ValueError("SKILL.md 必须包含 YAML frontmatter.description")
        if len(str(frontmatter.get("description"))) > 1024:
            raise ValueError("description 不能超过 1024 字符")
        frontmatter_name = _normalize_name(str(frontmatter.get("name")), field_name="skill name")
        if expected_name is not None:
            normalized_expected = _normalize_name(
                str(expected_name).split("/")[-1],
                field_name="skill name",
            )
            if frontmatter_name != normalized_expected:
                raise ValueError(
                    "Skill 目录名、manage name 与 frontmatter.name 必须一致: "
                    f"expected={normalized_expected!r}, actual={frontmatter_name!r}"
                )
        if self.guard_agent_created:
            lowered = content.lower()
            for pattern in _DANGEROUS_PATTERNS:
                if re.search(pattern, lowered):
                    raise ValueError(f"SKILL.md 命中危险内容: {pattern}")

    def _create_local(
        self,
        *,
        name: str,
        content: str,
        category: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        self._validate_skill_markdown(content, expected_name=name)
        skill_file = self._local_skill_dir(name, category) / "SKILL.md"
        if skill_file.exists() and not overwrite:
            raise FileExistsError(f"本地 Skill 已存在: {skill_file}")
        _atomic_write(skill_file, content)
        self._index_cache = None
        return {"success": True, "action": "create", "path": str(skill_file), "copied_from": None}

    def edit(self, *, name: str, content: str) -> dict[str, Any]:
        self._validate_skill_markdown(content, expected_name=name)
        original = self.get(name)
        meta = self._editable_meta(name)
        _atomic_write(meta.skill_file, content)
        self._index_cache = None
        return {
            "success": True,
            "action": "edit",
            "path": str(meta.skill_file),
            "copied_from": str(original.skill_dir) if original.source != "local" else None,
        }

    def patch(
        self,
        *,
        name: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        if not old_string:
            raise ValueError("old_string 不能为空")
        original = self.get(name)
        meta = self._editable_meta(name)
        content = meta.skill_file.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            raise ValueError("old_string 未命中")
        if count > 1 and not replace_all:
            raise ValueError("old_string 命中多次；请设置 replace_all=true 或提供唯一片段")
        updated = content.replace(old_string, new_string, -1 if replace_all else 1)
        self._validate_skill_markdown(updated, expected_name=name)
        _atomic_write(meta.skill_file, updated)
        self._index_cache = None
        return {
            "success": True,
            "action": "patch",
            "path": str(meta.skill_file),
            "replacements": count if replace_all else 1,
            "copied_from": str(original.skill_dir) if original.source != "local" else None,
        }

    def delete(self, *, name: str) -> dict[str, Any]:
        meta = self._local_meta_for_delete(name)
        shutil.rmtree(meta.skill_dir)
        parent = meta.skill_dir.parent
        if parent != self.local_dir and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        self._index_cache = None
        return {"success": True, "action": "delete", "path": str(meta.skill_dir)}

    @staticmethod
    def _supporting_path(meta: SkillMetadata, file_path: str) -> Path:
        raw = Path(file_path)
        if raw.is_absolute() or ".." in raw.parts:
            raise ValueError("file_path 必须是 Skill 内部相对路径，不能包含 ..")
        if not raw.parts or raw.parts[0] not in _SUPPORTING_DIRS:
            raise ValueError(f"file_path 必须位于 {sorted(_SUPPORTING_DIRS)} 之一")
        resolved = (meta.skill_dir / raw).resolve()
        try:
            resolved.relative_to(meta.skill_dir)
        except ValueError as exc:
            raise ValueError("file_path 越界") from exc
        return resolved

    def write_file(self, *, name: str, file_path: str, content: str) -> dict[str, Any]:
        if len(content.encode("utf-8")) > _MAX_SUPPORTING_FILE_BYTES:
            raise ValueError("supporting file 超过 1 MiB")
        original = self.get(name)
        meta = self._editable_meta(name)
        target = self._supporting_path(meta, file_path)
        _atomic_write(target, content)
        self._index_cache = None
        return {
            "success": True,
            "action": "write_file",
            "path": str(target),
            "copied_from": str(original.skill_dir) if original.source != "local" else None,
        }

    def remove_file(self, *, name: str, file_path: str) -> dict[str, Any]:
        original = self.get(name)
        meta = self._editable_meta(name)
        target = self._supporting_path(meta, file_path)
        if not target.is_file():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        target.unlink()
        self._index_cache = None
        return {
            "success": True,
            "action": "remove_file",
            "path": str(target),
            "copied_from": str(original.skill_dir) if original.source != "local" else None,
        }

    def manage(self, action: str, **kwargs: Any) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        try:
            if normalized == "create":
                return self._create_local(
                    name=str(kwargs.get("name") or ""),
                    category=kwargs.get("category"),
                    content=str(kwargs.get("content") or ""),
                    overwrite=bool(kwargs.get("overwrite")),
                )
            if normalized == "edit":
                return self.edit(name=str(kwargs.get("name") or ""), content=str(kwargs.get("content") or ""))
            if normalized == "patch":
                return self.patch(
                    name=str(kwargs.get("name") or ""),
                    old_string=str(kwargs.get("old_string") or ""),
                    new_string=str(kwargs.get("new_string") or ""),
                    replace_all=bool(kwargs.get("replace_all")),
                )
            if normalized == "write_file":
                return self.write_file(
                    name=str(kwargs.get("name") or ""),
                    file_path=str(kwargs.get("file_path") or ""),
                    content=str(kwargs.get("content") or ""),
                )
            raise ValueError(f"未知 skill_manage action: {action}")
        except Exception as exc:
            logger.warning(
                "skill_manage 失败 action=%s kwargs=%s error=%s",
                normalized,
                json.dumps(kwargs, ensure_ascii=False, default=str),
                exc,
            )
            return {"success": False, "action": normalized, "error": str(exc)}

__all__ = ["SkillRegistry", "default_builtin_skills_dir", "default_local_skills_dir"]
