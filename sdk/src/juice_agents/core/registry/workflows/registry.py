"""Built-in 与 workspace-local workflow 的发现、校验和加载。

Registry 只负责「找到脚本 → 校验 → 加载出 run 函数」，从不执行 workflow，
也不持有运行态；执行、缓存和控制全部归 ``WorkflowRunManager``。

关于 AST 校验
-------------
local workflow 禁止直接 import 副作用模块、禁止 eval/exec/open 等调用。这是**防误用
lint**：模型写错时立即给出可读报错（「请通过 agent 访问文件/网络」）。它不是安全
隔离，Python 无法在同进程内可靠沙箱化。真正的授权边界是：
写脚本需要 ``workflow_manage``（受 self_evolution 开关与审批约束），运行非只读
脚本需要启动审批，内部 agent 的工具受 metadata.tools 与硬禁止列表约束。
"""

from __future__ import annotations

import ast
import hashlib
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from juice_agents.core.config.context import ConfigurationContext

from .types import WorkflowMetadata

logger = logging.getLogger(__name__)

WorkflowRunFn = Callable[[Any, dict[str, Any]], Any]

_WORKFLOW_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_BUILTIN_MODULES = ("deep_research",)
_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "builtins",
        "ctypes",
        "ftplib",
        "http",
        "importlib",
        "os",
        "pathlib",
        "requests",
        "shutil",
        "socket",
        "subprocess",
        "sys",
        "urllib",
    }
)
_FORBIDDEN_CALLS = frozenset({"compile", "eval", "exec", "open", "__import__", "getattr", "globals", "vars"})


def _normalize_name(value: str) -> str:
    name = str(value or "").strip().lower()
    if not _WORKFLOW_NAME_RE.fullmatch(name):
        raise ValueError("workflow name 仅允许小写字母、数字和下划线，必须以字母开头，最长 64 字符")
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


def _string_tuple(payload: dict[str, Any], key: str, *, path: Path) -> tuple[str, ...]:
    raw = payload.get(key, [])
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"WORKFLOW_METADATA.{key} 必须是 string 列表: {path}")
    return tuple(dict.fromkeys(item.strip() for item in raw if item.strip()))


def _metadata_literal(tree: ast.Module, *, path: Path, source: str, content: str) -> WorkflowMetadata:
    payload: dict[str, Any] | None = None
    has_run = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            has_run = True
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
            raise ValueError(f"workflow 的 run(ctx, args) 必须是同步函数: {path}")
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == "WORKFLOW_METADATA" for target in targets):
            try:
                parsed = ast.literal_eval(node.value)  # type: ignore[arg-type]
            except Exception as exc:
                raise ValueError(f"WORKFLOW_METADATA 必须是字面量 object: {path}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"WORKFLOW_METADATA 必须是 object: {path}")
            payload = dict(parsed)
    if payload is None:
        raise ValueError(f"workflow 缺少 WORKFLOW_METADATA: {path}")
    if not has_run:
        raise ValueError(f"workflow 缺少 run(ctx, args): {path}")
    raw_modes = payload.get("allowed_modes")
    return WorkflowMetadata(
        name=_normalize_name(str(payload.get("name") or path.stem)),
        description=str(payload.get("description") or "").strip(),
        source=source,  # type: ignore[arg-type]
        path=path.resolve(),
        read_only=bool(payload.get("read_only")),
        allowed_modes=None if raw_modes is None else _string_tuple(payload, "allowed_modes", path=path),
        tools=_string_tuple(payload, "tools", path=path),
        phases=_string_tuple(payload, "phases", path=path),
        input_schema=dict(payload.get("input_schema") or {}),
        output_schema=dict(payload.get("output_schema") or {}),
        # 哈希调用方已持有的源码，不回头读盘：create 时目标文件尚不存在，
        # 且内存内容才是本次真正被校验和写入的那一份（消除 TOCTOU 窗口）。
        source_hash=_source_hash(content),
    )


def validate_workflow_source(content: str, *, path: Path, local: bool) -> WorkflowMetadata:
    """在 import 前校验脚本结构；local 脚本额外做副作用 lint。"""

    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"workflow Python 语法错误: {exc}") from exc
    if local:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                blocked = {alias.name.split(".", 1)[0] for alias in node.names} & _FORBIDDEN_IMPORT_ROOTS
                if blocked:
                    raise ValueError(f"local workflow 禁止直接导入副作用模块: {sorted(blocked)}；请通过 ctx.agent")
            elif isinstance(node, ast.ImportFrom):
                root = str(node.module or "").split(".", 1)[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    raise ValueError(f"local workflow 禁止直接导入副作用模块: {root}；请通过 ctx.agent")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                raise ValueError(f"local workflow 禁止直接调用 {node.func.id}；请通过 ctx.agent 或 ctx.artifact")
            elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
                raise ValueError(f"local workflow 禁止访问 dunder 属性 {node.attr}")
    return _metadata_literal(tree, path=path, source="local" if local else "builtin", content=content)


def _load_module(path: Path, *, content: str, source_hash: str) -> ModuleType:
    """执行**已校验的那份源码字符串**，而不是重新读盘。

    校验与执行之间如果再读一次文件，中途被改写的脚本就会绕过校验（TOCTOU）。
    模块按内容哈希命名：同一源码得到同一模块名，改动后自动得到新模块。
    """

    module_name = f"juice_workflow_{path.stem}_{source_hash[:12]}"
    module = ModuleType(module_name)
    module.__file__ = str(path)
    # get_type_hints() 通过 sys.modules 解析延迟注解，执行前先注册。
    sys.modules[module_name] = module
    try:
        exec(compile(content, str(path), "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


class WorkflowRegistry:
    """发现、校验并加载 workflow 定义；local 同名覆盖 builtin。"""

    def __init__(
        self,
        *,
        config_context: ConfigurationContext | None = None,
        local_dir: str | Path | None = None,
        builtin_dir: str | Path | None = None,
    ) -> None:
        self.config_context = config_context or ConfigurationContext.from_workspace(Path.cwd())
        self.local_dir = (
            Path(local_dir).resolve() if local_dir is not None else self.config_context.juice_root / "workflows"
        )
        self.builtin_dir = (
            Path(builtin_dir).resolve()
            if builtin_dir is not None
            else Path(__file__).resolve().parents[2] / "workflow" / "builtins"
        )

    def _scan(self, root: Path, *, source: str) -> list[WorkflowMetadata]:
        if not root.exists():
            return []
        metadata: list[WorkflowMetadata] = []
        paths = [root / f"{name}.py" for name in _BUILTIN_MODULES] if source == "builtin" else sorted(root.glob("*.py"))
        for path in paths:
            if not path.exists() or path.name == "__init__.py":
                continue
            try:
                metadata.append(
                    validate_workflow_source(path.read_text(encoding="utf-8"), path=path, local=source == "local")
                )
            except Exception as exc:
                logger.warning("跳过非法 workflow %s: %s", path, exc)
        return metadata

    def list_metadata(self) -> list[WorkflowMetadata]:
        selected: dict[str, WorkflowMetadata] = {}
        for meta in self._scan(self.builtin_dir, source="builtin"):
            selected[meta.name] = meta
        for meta in self._scan(self.local_dir, source="local"):
            selected[meta.name] = meta
        return sorted(selected.values(), key=lambda item: item.name)

    def list(self) -> list[str]:
        return [item.name for item in self.list_metadata()]

    def get_metadata(self, name: str) -> WorkflowMetadata:
        normalized = _normalize_name(name)
        for meta in self.list_metadata():
            if meta.name == normalized:
                return meta
        raise FileNotFoundError(f"workflow 不存在: {normalized}")

    def view(self, name: str) -> dict[str, Any]:
        meta = self.get_metadata(name)
        assert meta.path is not None
        return {"success": True, "metadata": meta.to_dict(), "content": meta.path.read_text(encoding="utf-8")}

    # -- BaseRegistry 三段式协议 ---------------------------------------------------

    def resolve(self, raw: str | WorkflowMetadata) -> WorkflowMetadata:
        """把名称归一化为声明，不 import 脚本代码。"""

        if isinstance(raw, WorkflowMetadata):
            return raw
        if not isinstance(raw, str):
            raise TypeError("workflow 只支持通过 name 或 WorkflowMetadata 解析")
        return self.get_metadata(raw)

    def _read_validated(self, raw: str | WorkflowMetadata) -> tuple[WorkflowMetadata, str]:
        meta = self.resolve(raw)
        assert meta.path is not None
        content = meta.path.read_text(encoding="utf-8")
        validated = validate_workflow_source(content, path=meta.path, local=meta.source == "local")
        if validated.name != meta.name:
            raise ValueError("workflow 元数据名称与源文件不一致")
        # 调用方传入的是之前拿到的 metadata（已据此做过权限判断）时，
        # 源码必须仍是同一份，否则权限判断依据与将要执行的代码不一致。
        if isinstance(raw, WorkflowMetadata) and raw.source_hash and raw.source_hash != validated.source_hash:
            raise ValueError(f"workflow {meta.name} 源码在校验后被修改，请重新运行")
        return validated, content

    def validate(self, raw: str | WorkflowMetadata) -> WorkflowMetadata:
        """重新读盘校验；返回的 metadata 与此刻磁盘上的源码严格对应。"""

        return self._read_validated(raw)[0]

    def instantiate(self, raw: str | WorkflowMetadata) -> WorkflowRunFn:
        """加载并返回 ``run(ctx, args)``；执行的正是本次校验通过的源码。"""

        meta, content = self._read_validated(raw)
        assert meta.path is not None
        module = _load_module(meta.path, content=content, source_hash=meta.source_hash)
        run_fn = getattr(module, "run", None)
        if not callable(run_fn):
            raise ValueError(f"workflow 缺少 run(ctx, args): {meta.path}")
        logger.info("加载 workflow: name=%s source=%s", meta.name, meta.source)
        return run_fn

    def _local_path(self, name: str) -> Path:
        return self.local_dir / f"{_normalize_name(name)}.py"

    def manage(self, action: str, *, name: str, content: str | None = None, overwrite: bool = False) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        try:
            # 名称校验也在 try 内：非法名称同样以 success=False 返回给 agent，而不是抛出。
            path = self._local_path(name)
            if normalized in {"create", "edit"}:
                if normalized == "create" and path.exists() and not overwrite:
                    raise FileExistsError(f"local workflow 已存在: {path}")
                if normalized == "edit" and not path.exists():
                    # 编辑针对 Registry 的有效定义：首次编辑内置脚本时，只把通过
                    # local 校验的完整源码保存为覆盖，不向只读包目录写入任何内容。
                    # 无本地文件时先查内置存在性，避免 edit 意外变成 create；
                    # 已有本地文件即使内容损坏，也应允许用户通过 edit 修复。
                    self.get_metadata(name)
                source = str(content or "")
                meta = validate_workflow_source(source, path=path, local=True)
                if meta.name != _normalize_name(name):
                    raise ValueError("WORKFLOW_METADATA.name 必须与文件名一致")
                _atomic_write(path, source)
                logger.info("workflow_manage 写入: action=%s path=%s", normalized, path)
                return {"success": True, "action": normalized, "path": str(path), "metadata": meta.to_dict()}
            # 没有 delete 分支：manage 只写不删，物理删除交给通用文件/Shell 工具，
            # 与 agent_manage / tool_manage / skill_manage 的约定一致。
            raise ValueError(f"未知 workflow_manage action: {action}（仅支持 create/edit）")
        except Exception as exc:
            logger.warning("workflow_manage 失败 action=%s name=%s error=%s", normalized, name, exc)
            return {"success": False, "action": normalized, "error": str(exc)}


__all__ = ["WorkflowRegistry", "WorkflowRunFn", "validate_workflow_source"]
