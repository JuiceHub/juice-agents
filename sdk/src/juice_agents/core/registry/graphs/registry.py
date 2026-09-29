"""Built-in and workspace-local graph discovery and fresh loading."""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.graph.types import CompiledPayloadGraph, GraphBuildContext

from .types import GraphMetadata

logger = logging.getLogger(__name__)

_GRAPH_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_BUILTIN_MODULES = ("deep_research",)
_FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp",
    "ftplib",
    "http",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "urllib",
}
_FORBIDDEN_CALLS = {
    "compile",
    "eval",
    "exec",
    "open",
    "__import__",
}


def _normalize_name(value: str) -> str:
    name = str(value or "").strip().lower()
    if not _GRAPH_NAME_RE.fullmatch(name):
        raise ValueError("graph name 仅允许小写字母、数字和下划线，必须以字母开头，最长 64 字符")
    return name


def _source_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _metadata_literal(tree: ast.Module, *, path: Path, source: str, content: str) -> GraphMetadata:
    payload: dict[str, Any] | None = None
    has_builder = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "build_graph":
            has_builder = True
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == "GRAPH_METADATA" for target in targets):
            value = node.value
            try:
                parsed = ast.literal_eval(value)
            except Exception as exc:
                raise ValueError(f"GRAPH_METADATA 必须是字面量 object: {path}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"GRAPH_METADATA 必须是 object: {path}")
            payload = dict(parsed)
    if payload is None:
        raise ValueError(f"graph 缺少 GRAPH_METADATA: {path}")
    if not has_builder:
        raise ValueError(f"graph 缺少 build_graph(context): {path}")
    name = _normalize_name(str(payload.get("name") or path.stem))
    return GraphMetadata(
        name=name,
        description=str(payload.get("description") or "").strip(),
        source=source,  # type: ignore[arg-type]
        path=path.resolve(),
        read_only=bool(payload.get("read_only")),
        input_schema=dict(payload.get("input_schema") or {}),
        output_schema=dict(payload.get("output_schema") or {}),
        # 哈希调用方已持有的源码，不回头读盘：create 时目标文件尚不存在，
        # 且内存内容才是本次真正被校验和写入的那一份（消除 TOCTOU 窗口）。
        source_hash=_source_hash(content),
    )


def validate_graph_source(content: str, *, path: Path, local: bool) -> GraphMetadata:
    """Validate the supported graph-script subset before import."""

    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"graph Python 语法错误: {exc}") from exc
    if local:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                blocked = roots & _FORBIDDEN_IMPORT_ROOTS
                if blocked:
                    raise ValueError(f"local graph 禁止直接导入副作用模块: {sorted(blocked)}")
            elif isinstance(node, ast.ImportFrom):
                root = str(node.module or "").split(".", 1)[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    raise ValueError(f"local graph 禁止直接导入副作用模块: {root}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                raise ValueError(f"local graph 禁止直接调用 {node.func.id}；请通过受权限控制的 tool")
    return _metadata_literal(
        tree,
        path=path,
        source="local" if local else "builtin",
        content=content,
    )


def _load_module(path: Path, *, source_hash: str) -> ModuleType:
    module_name = f"juice_graph_{path.stem}_{source_hash[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 graph: {path}")
    module = importlib.util.module_from_spec(spec)
    # get_type_hints() resolves postponed annotations through sys.modules.
    # Register the isolated content-hash module before executing its source.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


class GraphRegistry:
    """Discover, validate, and freshly construct graph definitions.

    Registry never invokes a graph or retains its instance.  ``GraphRunManager``
    owns invocation, checkpoints, cancellation, and recovery after it receives
    a fresh object from ``instantiate``.
    """

    def __init__(
        self,
        *,
        config_context: ConfigurationContext | None = None,
        local_dir: str | Path | None = None,
        builtin_dir: str | Path | None = None,
    ) -> None:
        self.config_context = config_context or ConfigurationContext.from_workspace(Path.cwd())
        self.local_dir = Path(local_dir).resolve() if local_dir is not None else self.config_context.juice_root / "graphs"
        self.builtin_dir = (
            Path(builtin_dir).resolve()
            if builtin_dir is not None
            else Path(__file__).resolve().parents[2] / "graph" / "builtins"
        )

    def _scan(self, root: Path, *, source: str) -> list[GraphMetadata]:
        if not root.exists():
            return []
        metadata: list[GraphMetadata] = []
        paths = [root / f"{name}.py" for name in _BUILTIN_MODULES] if source == "builtin" else sorted(root.glob("*.py"))
        for path in paths:
            if not path.exists() or path.name == "__init__.py":
                continue
            try:
                metadata.append(validate_graph_source(path.read_text(encoding="utf-8"), path=path, local=source == "local"))
            except Exception as exc:
                logger.warning("跳过非法 graph %s: %s", path, exc)
        return metadata

    def list_metadata(self) -> list[GraphMetadata]:
        selected: dict[str, GraphMetadata] = {}
        for meta in self._scan(self.builtin_dir, source="builtin"):
            selected[meta.name] = meta
        for meta in self._scan(self.local_dir, source="local"):
            selected[meta.name] = meta
        return sorted(selected.values(), key=lambda item: item.name)

    def list(self) -> list[str]:
        return [item.name for item in self.list_metadata()]

    def get_metadata(self, name: str) -> GraphMetadata:
        normalized = _normalize_name(name)
        for meta in self.list_metadata():
            if meta.name == normalized:
                return meta
        raise FileNotFoundError(f"graph 不存在: {normalized}")

    def view(self, name: str) -> dict[str, Any]:
        meta = self.get_metadata(name)
        assert meta.path is not None
        return {"success": True, "metadata": meta.to_dict(), "content": meta.path.read_text(encoding="utf-8")}

    @staticmethod
    def _copy_metadata(meta: GraphMetadata) -> GraphMetadata:
        """Detach mutable schema maps before handing a definition to a caller."""

        return GraphMetadata(
            name=meta.name,
            description=meta.description,
            source=meta.source,
            path=meta.path,
            read_only=meta.read_only,
            input_schema=dict(meta.input_schema),
            output_schema=dict(meta.output_schema),
            source_hash=meta.source_hash,
        )

    def resolve(self, raw: str | GraphMetadata) -> GraphMetadata:
        """Resolve a graph name to metadata without importing executable code."""

        if isinstance(raw, GraphMetadata):
            return self._copy_metadata(raw)
        if not isinstance(raw, str):
            raise TypeError("graph 只支持通过 name 或 GraphMetadata 解析")
        meta = self.get_metadata(raw)
        return self._copy_metadata(meta)

    def validate(self, raw: str | GraphMetadata) -> GraphMetadata:
        """Revalidate graph source without importing or executing it."""

        meta = self.resolve(raw)
        assert meta.path is not None
        source = meta.path.read_text(encoding="utf-8")
        validated = validate_graph_source(
            source,
            path=meta.path,
            local=meta.source == "local",
        )
        if validated.name != meta.name:
            raise ValueError("graph 元数据名称与源文件不一致")
        return validated

    @staticmethod
    def _load_builder(meta: GraphMetadata) -> Callable[..., Any]:
        """Load executable graph code only at the fresh-construction boundary."""

        assert meta.path is not None
        module = _load_module(meta.path, source_hash=meta.source_hash)
        builder = getattr(module, "build_graph", None)
        if not callable(builder):
            raise ValueError(f"graph 缺少 build_graph(context): {meta.path}")
        return builder

    def instantiate(self, raw: str | GraphMetadata, **runtime_kwargs: Any) -> Any:
        """Build one fresh graph instance; execution remains outside Registry."""

        metadata = self.validate(raw)
        builder = self._load_builder(metadata)
        context = runtime_kwargs.pop("context", None)
        if context is None:
            context = GraphBuildContext(
                config_context=self.config_context,
                workspace_dir=self.config_context.workspace_dir,
                callbacks=dict(runtime_kwargs.pop("callbacks", {}) or {}),
                **runtime_kwargs,
            )
            runtime_kwargs = {}
        instance = builder(context, **runtime_kwargs) if context is not None else builder(**runtime_kwargs)
        if callable(getattr(instance, "compile", None)) and not callable(getattr(instance, "invoke", None)):
            instance = instance.compile()
        if not callable(getattr(instance, "invoke", None)):
            raise ValueError("build_graph 必须返回具备 invoke(...) 的对象，或返回可 compile() 的 StateGraph")
        logger.info("实例化 fresh graph: name=%s source=%s", metadata.name, metadata.source)
        return instance

    def instantiate_from_path(self, path: str | Path, *, context: GraphBuildContext) -> Any:
        """Build an immutable graph source snapshot for a persisted run."""

        snapshot = Path(path).expanduser().resolve()
        content = snapshot.read_text(encoding="utf-8")
        meta = validate_graph_source(content, path=snapshot, local=False)
        builder = self._load_builder(meta)
        instance = builder(context)
        if callable(getattr(instance, "compile", None)) and not callable(getattr(instance, "invoke", None)):
            instance = instance.compile()
        if not callable(getattr(instance, "invoke", None)):
            raise ValueError("build_graph 必须返回具备 invoke(...) 的对象，或返回可 compile() 的 StateGraph")
        return instance

    def _local_path(self, name: str) -> Path:
        return self.local_dir / f"{_normalize_name(name)}.py"

    def manage(self, action: str, *, name: str, content: str | None = None, overwrite: bool = False) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        path = self._local_path(name)
        try:
            if normalized in {"create", "edit"}:
                if normalized == "create" and path.exists() and not overwrite:
                    raise FileExistsError(f"local graph 已存在: {path}")
                if normalized == "edit" and not path.exists():
                    raise FileNotFoundError(f"local graph 不存在: {path}")
                source = str(content or "")
                meta = validate_graph_source(source, path=path, local=True)
                if meta.name != _normalize_name(name):
                    raise ValueError("GRAPH_METADATA.name 必须与文件名一致")
                _atomic_write(path, source)
                return {"success": True, "action": normalized, "path": str(path), "metadata": meta.to_dict()}
            # 没有 delete 分支：manage 只写不删，物理删除交给通用文件/Shell 工具，
            # 与 agent_manage / tool_manage / skill_manage 的约定一致。
            raise ValueError(f"未知 graph_manage action: {action}（仅支持 create/edit）")
        except Exception as exc:
            logger.warning("graph_manage 失败 action=%s name=%s error=%s", normalized, name, exc)
            return {"success": False, "action": normalized, "error": str(exc)}


__all__ = [
    "CompiledPayloadGraph",
    "GraphBuildContext",
    "GraphMetadata",
    "GraphRegistry",
    "validate_graph_source",
]
