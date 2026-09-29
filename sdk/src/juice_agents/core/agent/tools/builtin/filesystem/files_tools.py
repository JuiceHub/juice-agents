"""
文件处理工具模块。

本模块将文件类能力收敛为一组轻量、面向本项目智能体工作流的工具：

- `read`：读取文本文件，并在成功后记录 read-state
- `write`：写文件；覆盖已有文件前必须有新鲜的完整 read-state
- `edit`：按结构化 `edits` 顺序修改文件；修改前也必须有新鲜的完整 read-state
- `glob`：按模式查找文件
- `grep`：按正则在文件内容中搜索

关键设计点：
- 所有路径都必须落在 `root_dir` 沙箱内
- `read-state` 不是单个工具私有，而是同一组文件工具共享
- 当工具被绑定到 agent 时，状态按 agent 隔离；否则按 `root_dir` 隔离
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import logging
import os
import re
import time
from typing import Any, Dict

from ...runtime.base_tools import CONTENT_OBSERVATION_CHARS, LIST_OBSERVATION_CHARS, Tool

logger = logging.getLogger(__name__)

_DEFAULT_RESULT_LIMIT = 100
_DEFAULT_READ_LINE_LIMIT = 200


@dataclass(slots=True)
class _ReadSnapshot:
    """
    单个文件最近一次成功读取后的快照。

    `is_partial=True` 表示本次读取只拿到了局部内容，后续不允许直接用于 write/edit。
    """

    resolved_path: str
    mtime_ns: int
    is_partial: bool
    read_at: str
    content: str = ""
    size_bytes: int = 0
    covered_ranges: list[tuple[int, int]] = field(default_factory=list)
    total_lines: int = 0


_LRU_MAX_ENTRIES = 100
_LRU_MAX_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


@dataclass(slots=True)
class _FileRuntimeState:
    """
    同一组文件工具共享的运行时状态，带 LRU 淘汰。
    """

    snapshots: OrderedDict[str, _ReadSnapshot] = field(default_factory=OrderedDict)
    max_entries: int = field(default=_LRU_MAX_ENTRIES)
    max_size_bytes: int = field(default=_LRU_MAX_SIZE_BYTES)
    current_size_bytes: int = field(default=0, init=False)

    def add_snapshot(self, path: str, snapshot: _ReadSnapshot) -> None:
        if path in self.snapshots:
            old = self.snapshots.pop(path)
            self.current_size_bytes -= old.size_bytes
        self.snapshots[path] = snapshot
        self.current_size_bytes += snapshot.size_bytes
        while (
            len(self.snapshots) > self.max_entries
            or self.current_size_bytes > self.max_size_bytes
        ):
            if not self.snapshots:
                break
            _, evicted = self.snapshots.popitem(last=False)
            self.current_size_bytes -= evicted.size_bytes

    def get_snapshot(self, path: str) -> _ReadSnapshot | None:
        if path not in self.snapshots:
            return None
        self.snapshots.move_to_end(path)
        return self.snapshots[path]


class _BaseFileTool(Tool):
    """
    文件工具基类，统一处理 root_dir 沙箱、路径解析与 read-state 管理。
    """

    max_observation_chars = CONTENT_OBSERVATION_CHARS
    # File reads/searches are safe to batch; mutating subclasses override this
    # to SERIAL below. The path is converted to a resource key so two calls on
    # the same file cannot overlap even when both are otherwise safe.
    _execution_mode = "parallel_safe"
    _STATE_BY_KEY: dict[tuple[str, str], _FileRuntimeState] = {}

    def __init__(self, root_dir: str | Path | None = None) -> None:
        super().__init__()
        resolved_root = Path(root_dir or ".").expanduser().resolve()
        if not resolved_root.exists():
            raise ValueError(f"root_dir 不存在: {resolved_root}")
        if not resolved_root.is_dir():
            raise ValueError(f"root_dir 必须是目录: {resolved_root}")
        self.root_dir = resolved_root
        self.owner_agent: Any | None = None

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def execution_policy(self, args: dict[str, Any], context: Any) -> Any:
        del context
        from ...runtime.executor import ToolExecutionMode, ToolExecutionPolicy

        raw_path = args.get("path") or args.get("file_path") or ""
        try:
            key = f"file:{self._resolve_path(str(raw_path)).resolve()}" if raw_path else ""
        except Exception:
            key = f"file:{raw_path}" if raw_path else ""
        mode = ToolExecutionMode(str(self._execution_mode))
        return ToolExecutionPolicy(mode=mode, resource_keys=(key,) if key else ())

    def reset_runtime_state(self) -> None:
        state = self._get_runtime_state()
        state.snapshots.clear()
        state.current_size_bytes = 0

    def _runtime_key(self) -> tuple[str, str]:
        """
        计算当前工具的状态隔离键。

        - 绑定到 agent 后：按 agent 隔离，避免多个 agent 共享同一个 workspace 时相互污染
        - 未绑定到 agent：按 root_dir 隔离，兼容直接实例化工具进行单测或脚本调用
        """

        if self.owner_agent is not None:
            return ("agent", str(id(self.owner_agent)))
        return ("root_dir", str(self.root_dir))

    def _get_runtime_state(self) -> _FileRuntimeState:
        key = self._runtime_key()
        state = self._STATE_BY_KEY.get(key)
        if state is None:
            state = _FileRuntimeState()
            self._STATE_BY_KEY[key] = state
        return state

    def _resolve_path(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path 必须为非空字符串")

        raw_path = Path(path.strip()).expanduser()
        if not raw_path.is_absolute():
            raw_path = self.root_dir / raw_path
        resolved = raw_path.resolve()

        try:
            resolved.relative_to(self.root_dir)
        except ValueError as exc:
            raise ValueError(
                f"路径超出允许范围: {resolved}（root_dir={self.root_dir}）"
            ) from exc
        return resolved

    def _resolve_search_root(self, path: str | None) -> Path:
        if path is None:
            return self.root_dir
        search_root = self._resolve_path(path)
        if not search_root.exists():
            raise FileNotFoundError(f"路径不存在: {search_root}")
        return search_root

    def _relative_path(self, path: Path) -> str:
        return path.relative_to(self.root_dir).as_posix()

    def _record_read(
        self,
        target: Path,
        content: str,
        *,
        start_offset: int,
        end_offset: int,
        total_lines: int,
    ) -> bool:
        """合并同一文件的分页覆盖；只有覆盖全部行才授予完整 read-state。"""
        state = self._get_runtime_state()
        existing = state.get_snapshot(str(target))
        current_mtime = target.stat().st_mtime_ns
        ranges: list[tuple[int, int]] = []
        if existing is not None and existing.mtime_ns == current_mtime:
            ranges.extend(existing.covered_ranges)
        ranges.append((max(0, start_offset), max(0, end_offset)))
        ranges.sort()
        merged: list[tuple[int, int]] = []
        for start, end in ranges:
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        complete = total_lines == 0 or (
            bool(merged) and merged[0][0] == 0 and merged[0][1] >= total_lines
        )
        stored_content = content if complete else ""
        raw_bytes = stored_content.encode("utf-8", errors="replace")
        snapshot = _ReadSnapshot(
            resolved_path=str(target),
            mtime_ns=current_mtime,
            is_partial=not complete,
            read_at=datetime.now(timezone.utc).isoformat(),
            content=stored_content,
            size_bytes=len(raw_bytes),
            covered_ranges=merged,
            total_lines=total_lines,
        )
        state.add_snapshot(str(target), snapshot)
        logger.info(
            "%s 记录读取状态: path=%s partial=%s",
            self.__class__.__name__,
            target,
            not complete,
        )
        return complete

    def _require_fresh_read(self, target: Path, *, require_full: bool) -> _ReadSnapshot:
        snapshot = self._get_runtime_state().get_snapshot(str(target))
        if snapshot is None:
            raise ValueError(
                f"目标文件必须先使用 read 读取后才能修改: {target}"
            )
        if require_full and snapshot.is_partial:
            raise ValueError(
                f"目标文件必须先完整读取后才能修改，当前只有局部 read-state: {target}"
            )
        current_mtime_ns = target.stat().st_mtime_ns
        if current_mtime_ns != snapshot.mtime_ns:
            # mtime 变化时,对完整读取做内容对比后备(防 Windows 云同步/杀软虚假 mtime)
            if not snapshot.is_partial and snapshot.content:
                try:
                    current_content = target.read_text(encoding="utf-8")
                    if current_content == snapshot.content:
                        return snapshot
                except Exception:
                    pass
            raise ValueError(
                f"文件已在读取后被修改(mtime: {snapshot.mtime_ns} → {current_mtime_ns})，"
                f"请重新执行 read: {target}"
            )
        return snapshot

    def _read_text_file(self, target: Path, *, encoding: str) -> str:
        try:
            return target.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"文件不是可按 {encoding} 读取的文本文件: {target}"
            ) from exc

    def _atomic_write_file(self, target: Path, content: str, *, encoding: str) -> None:
        """原子写入:tmp 文件 + rename,失败时回退到直接写入。"""
        tmp_path = target.with_name(
            f".{target.name}.tmp.{os.getpid()}.{time.time_ns()}"
        )
        original_mode = None
        if target.exists():
            try:
                original_mode = target.stat().st_mode
            except OSError:
                pass

        try:
            tmp_path.write_text(content, encoding=encoding)
            if original_mode is not None:
                os.chmod(tmp_path, original_mode)
            tmp_path.replace(target)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            target.write_text(content, encoding=encoding)
            if original_mode is not None:
                try:
                    os.chmod(target, original_mode)
                except Exception:
                    pass

    def _check_mtime_before_write(
        self, target: Path, snapshot: _ReadSnapshot, *, encoding: str
    ) -> None:
        """写入前二次 mtime 校验,收紧 TOCTOU 窗口。"""
        if not target.exists():
            return
        current_mtime_ns = target.stat().st_mtime_ns
        if current_mtime_ns == snapshot.mtime_ns:
            return
        if not snapshot.is_partial and snapshot.content:
            try:
                current_content = target.read_text(encoding=encoding)
                if current_content == snapshot.content:
                    return
            except Exception:
                pass
        raise ValueError(
            f"文件在写入前被意外修改，请重新执行 read: {target}"
        )


class ReadTool(_BaseFileTool):
    name = "read"
    is_read_only = True
    description = "读取文本文件内容，支持整文件和 offset/limit 分片读取"
    inputs = {
        "path": {"type": "string", "description": "文件路径（相对 root_dir）"},
        "encoding": {"type": "string", "description": "文件编码，默认 utf-8", "required": False},
        "offset": {"type": "integer", "description": "从第几行开始读取（0-based）", "required": False},
        "limit": {"type": "integer", "description": "最多读取多少行", "required": False},
    }
    outputs = {
        "path": {"type": "string", "description": "目标文件绝对路径"},
        "content": {"type": "string", "description": "读取到的文本内容"},
        "encoding": {"type": "string", "description": "实际使用的编码"},
        "line_count": {"type": "integer", "description": "文件总行数"},
        "start_line": {"type": "integer", "description": "实际起始行（1-based）"},
        "end_line": {"type": "integer", "description": "实际结束行（包含）"},
        "is_partial": {"type": "boolean", "description": "是否是局部读取"},
        "has_more": {"type": "boolean", "description": "后续是否还有未返回的行"},
        "next_offset": {"type": "integer", "description": "下一页的 0-based offset；已完成时为 null"},
    }

    def forward(
        self,
        path: str,
        encoding: str = "utf-8",
        offset: int | None = None,
        limit: int | None = None,
    ) -> Dict[str, Any]:
        target = self._resolve_path(path)
        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {target}")
        if not target.is_file():
            raise ValueError(f"path 不是文件: {target}")

        content = self._read_text_file(target, encoding=encoding)
        lines = content.splitlines()
        total_lines = len(lines)
        normalized_offset = 0 if offset is None else offset
        if normalized_offset < 0:
            raise ValueError("offset 必须为非负整数")
        # 未显式给 limit 时，每一页都遵循同一个默认上限；offset 只负责定位，
        # 不能把后续分页悄悄退化成“读取到文件末尾”。
        effective_limit = _DEFAULT_READ_LINE_LIMIT if limit is None else limit
        if effective_limit is not None and effective_limit <= 0:
            raise ValueError("limit 必须为正整数")
        slice_end = (
            total_lines
            if effective_limit is None
            else min(total_lines, normalized_offset + effective_limit)
        )
        sliced = lines[normalized_offset:slice_end]
        actual_start_line = normalized_offset + 1 if sliced else 0
        actual_end_line = normalized_offset + len(sliced) if sliced else 0
        complete = self._record_read(
            target,
            content,
            start_offset=normalized_offset,
            end_offset=slice_end,
            total_lines=total_lines,
        )
        has_more = slice_end < total_lines
        return {
            "path": str(target),
            "content": "\n".join(sliced),
            "encoding": encoding,
            "line_count": total_lines,
            "start_line": actual_start_line,
            "end_line": actual_end_line,
            "is_partial": not complete,
            "has_more": has_more,
            "next_offset": slice_end if has_more else None,
        }


class WriteTool(_BaseFileTool):
    _execution_mode = "serial"
    name = "write"
    description = "整文件写入文本内容；覆盖已有文件前必须先完整读取"
    inputs = {
        "path": {"type": "string", "description": "目标文件路径（相对 root_dir）"},
        "content": {"type": "string", "description": "写入内容"},
        "encoding": {"type": "string", "description": "文件编码，默认 utf-8", "required": False},
        "overwrite": {"type": "boolean", "description": "已存在时是否覆盖", "required": False},
        "create_parents": {"type": "boolean", "description": "是否自动创建父目录", "required": False},
    }
    outputs = {
        "path": {"type": "string", "description": "目标文件绝对路径"},
        "created": {"type": "boolean", "description": "是否新建文件"},
        "overwritten": {"type": "boolean", "description": "是否覆盖已有文件"},
        "bytes_written": {"type": "integer", "description": "写入字节数"},
    }

    def forward(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
        overwrite: bool = True,
        create_parents: bool = True,
    ) -> Dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError("content 必须是字符串")

        target = self._resolve_path(path)
        existed = target.exists()
        if existed and target.is_dir():
            raise ValueError(f"目标路径是目录，不能写入文件: {target}")
        if existed and not overwrite:
            raise FileExistsError(f"文件已存在且 overwrite=False: {target}")
        if existed:
            snapshot = self._require_fresh_read(target, require_full=True)

        if create_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.exists():
            raise FileNotFoundError(f"父目录不存在: {target.parent}")

        if existed:
            self._check_mtime_before_write(target, snapshot, encoding=encoding)

        self._atomic_write_file(target, content, encoding=encoding)
        bytes_written = len(content.encode(encoding))
        logger.info("WriteTool 写入文件: %s (%d bytes)", target, bytes_written)
        return {
            "path": str(target),
            "created": not existed,
            "overwritten": existed,
            "bytes_written": bytes_written,
        }


class EditTool(_BaseFileTool):
    _execution_mode = "serial"
    name = "edit"
    description = "按结构化 edits 顺序修改文本文件，支持 replace 与锚点前后插入"
    inputs = {
        "path": {"type": "string", "description": "目标文件路径（相对 root_dir）"},
        "edits": {
            "type": "list",
            "description": (
                "按顺序执行的编辑列表。"
                "支持 replace(old/new/replace_all)、"
                "insert_before(anchor/content)、insert_after(anchor/content)"
            ),
        },
        "encoding": {"type": "string", "description": "文件编码，默认 utf-8", "required": False},
    }
    outputs = {
        "path": {"type": "string", "description": "目标文件绝对路径"},
        "edit_count": {"type": "integer", "description": "成功应用的 edit 数量或 replace 命中总数"},
        "bytes_written": {"type": "integer", "description": "最终文件字节数"},
    }

    def _validate_edits(self, edits: Any) -> list[dict[str, Any]]:
        if not isinstance(edits, list) or not edits:
            raise ValueError("edits 必须是非空数组")

        normalized: list[dict[str, Any]] = []
        for index, raw_edit in enumerate(edits):
            if not isinstance(raw_edit, dict):
                raise ValueError(f"edits[{index}] 必须是对象")

            edit_type = raw_edit.get("type")
            if edit_type not in {"replace", "insert_before", "insert_after"}:
                raise ValueError(f"不支持的 edit.type: {edit_type!r}")

            if edit_type == "replace":
                old = raw_edit.get("old")
                new = raw_edit.get("new")
                replace_all = raw_edit.get("replace_all", False)
                if not isinstance(old, str) or not old:
                    raise ValueError("replace.old 必须是非空字符串")
                if not isinstance(new, str):
                    raise ValueError("replace.new 必须是字符串")
                if not isinstance(replace_all, bool):
                    raise ValueError("replace.replace_all 必须是布尔值")
                normalized.append(
                    {
                        "type": edit_type,
                        "old": old,
                        "new": new,
                        "replace_all": replace_all,
                    }
                )
                continue

            anchor = raw_edit.get("anchor")
            content = raw_edit.get("content")
            if not isinstance(anchor, str) or not anchor:
                raise ValueError(f"{edit_type}.anchor 必须是非空字符串")
            if not isinstance(content, str) or not content:
                raise ValueError(f"{edit_type}.content 必须是非空字符串")
            normalized.append(
                {
                    "type": edit_type,
                    "anchor": anchor,
                    "content": content,
                }
            )

        return normalized

    def _apply_replace(self, content: str, edit: dict[str, Any]) -> tuple[str, int]:
        old = edit["old"]
        new = edit["new"]
        match_count = content.count(old)
        if match_count == 0:
            raise ValueError(f"未找到要替换的内容: {old!r}")

        if edit["replace_all"]:
            return content.replace(old, new), match_count

        if match_count != 1:
            raise ValueError(
                f"replace.old 必须恰好命中 1 次，当前命中 {match_count} 次: {old!r}"
            )
        return content.replace(old, new, 1), 1

    def _find_unique_anchor(self, content: str, anchor: str) -> int:
        match_count = content.count(anchor)
        if match_count == 0:
            raise ValueError(f"未找到锚点: {anchor!r}")
        if match_count != 1:
            raise ValueError(
                f"锚点必须恰好命中 1 次，当前命中 {match_count} 次: {anchor!r}"
            )
        return content.index(anchor)

    def _apply_insert_before(self, content: str, edit: dict[str, Any]) -> tuple[str, int]:
        anchor = edit["anchor"]
        insert_at = self._find_unique_anchor(content, anchor)
        updated = content[:insert_at] + edit["content"] + content[insert_at:]
        return updated, 1

    def _apply_insert_after(self, content: str, edit: dict[str, Any]) -> tuple[str, int]:
        anchor = edit["anchor"]
        anchor_start = self._find_unique_anchor(content, anchor)
        insert_at = anchor_start + len(anchor)
        updated = content[:insert_at] + edit["content"] + content[insert_at:]
        return updated, 1

    def _apply_edits(self, content: str, edits: list[dict[str, Any]]) -> tuple[str, int]:
        updated = content
        edit_count = 0

        # 多个 edit 明确按顺序基于上一次结果连续应用，避免调用方再推测批量语义。
        for edit in edits:
            edit_type = edit["type"]
            if edit_type == "replace":
                updated, current_count = self._apply_replace(updated, edit)
            elif edit_type == "insert_before":
                updated, current_count = self._apply_insert_before(updated, edit)
            else:
                updated, current_count = self._apply_insert_after(updated, edit)
            edit_count += current_count

        return updated, edit_count

    def forward(
        self,
        path: str,
        edits: list[dict[str, Any]],
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        target = self._resolve_path(path)
        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {target}")
        if not target.is_file():
            raise ValueError(f"path 不是文件: {target}")
        snapshot = self._require_fresh_read(target, require_full=True)

        content = self._read_text_file(target, encoding=encoding)
        normalized_edits = self._validate_edits(edits)
        updated, edit_count = self._apply_edits(content, normalized_edits)

        self._check_mtime_before_write(target, snapshot, encoding=encoding)
        self._atomic_write_file(target, updated, encoding=encoding)
        bytes_written = len(updated.encode(encoding))
        logger.info("EditTool 编辑文件: %s (%d edits)", target, edit_count)
        return {
            "path": str(target),
            "edit_count": edit_count,
            "bytes_written": bytes_written,
        }


class GlobTool(_BaseFileTool):
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "glob"
    is_read_only = True
    description = "按 glob 模式查找文件"
    inputs = {
        "pattern": {"type": "string", "description": "glob 模式，例如 **/*.py"},
        "path": {"type": "string", "description": "可选搜索目录（相对 root_dir）", "required": False},
        "limit": {"type": "integer", "description": "最多返回多少个结果", "required": False},
    }
    outputs = {
        "filenames": {"type": "list", "description": "命中的相对文件路径列表"},
        "num_files": {"type": "integer", "description": "返回的文件数量"},
        "truncated": {"type": "boolean", "description": "结果是否被截断"},
    }

    def forward(
        self,
        pattern: str,
        path: str | None = None,
        limit: int = _DEFAULT_RESULT_LIMIT,
    ) -> Dict[str, Any]:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("pattern 必须是非空字符串")
        if limit <= 0:
            raise ValueError("limit 必须为正整数")

        search_root = self._resolve_search_root(path)
        if not search_root.is_dir():
            raise ValueError(f"glob 搜索路径必须是目录: {search_root}")

        matched = sorted(
            self._relative_path(item)
            for item in search_root.glob(pattern)
            if item.is_file()
        )
        truncated = len(matched) > limit
        filenames = matched[:limit]
        return {
            "filenames": filenames,
            "num_files": len(filenames),
            "truncated": truncated,
        }


class GrepTool(_BaseFileTool):
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "grep"
    is_read_only = True
    description = "按正则在文本文件内容中搜索"
    inputs = {
        "pattern": {"type": "string", "description": "正则表达式"},
        "path": {"type": "string", "description": "可选搜索路径（文件或目录，相对 root_dir）", "required": False},
        "glob": {"type": "string", "description": "可选 glob 过滤，例如 *.py", "required": False},
        "output_mode": {
            "type": "string",
            "description": "输出模式：files_with_matches/content/count",
            "required": False,
        },
        "head_limit": {"type": "integer", "description": "最多返回多少条结果", "required": False},
        "offset": {"type": "integer", "description": "跳过前多少条结果", "required": False},
        "encoding": {"type": "string", "description": "文件编码，默认 utf-8", "required": False},
    }
    outputs = {
        "mode": {"type": "string", "description": "实际输出模式"},
        "filenames": {"type": "list", "description": "涉及到的相对文件路径列表"},
        "num_files": {"type": "integer", "description": "涉及到的文件数"},
        "num_matches": {"type": "integer", "description": "命中的总数量"},
        "content": {"type": "string", "description": "content 模式下的文本结果", "required": False},
        "counts": {"type": "dict", "description": "count 模式下每个文件的计数", "required": False},
        "truncated": {"type": "boolean", "description": "结果是否被截断"},
    }

    def _iter_search_files(self, search_root: Path, *, glob_pattern: str | None) -> list[Path]:
        if search_root.is_file():
            candidates = [search_root]
        else:
            candidates = sorted(item for item in search_root.rglob("*") if item.is_file())

        if not glob_pattern:
            return candidates

        matched: list[Path] = []
        for item in candidates:
            relative_path = self._relative_path(item)
            if PurePosixPath(relative_path).match(glob_pattern):
                matched.append(item)
        return matched

    def _paginate(self, items: list[Any], *, offset: int, head_limit: int) -> tuple[list[Any], bool]:
        paged = items[offset: offset + head_limit]
        truncated = offset + head_limit < len(items)
        return paged, truncated

    def forward(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        output_mode: str = "files_with_matches",
        head_limit: int = _DEFAULT_RESULT_LIMIT,
        offset: int = 0,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("pattern 必须是非空字符串")
        if output_mode not in {"files_with_matches", "content", "count"}:
            raise ValueError("output_mode 只能是 files_with_matches/content/count")
        if head_limit <= 0:
            raise ValueError("head_limit 必须为正整数")
        if offset < 0:
            raise ValueError("offset 必须为非负整数")

        search_root = self._resolve_search_root(path)
        regex = re.compile(pattern)
        files = self._iter_search_files(search_root, glob_pattern=glob)

        file_hits: dict[str, int] = {}
        line_hits: list[str] = []
        total_matches = 0

        for file_path in files:
            try:
                content = self._read_text_file(file_path, encoding=encoding)
            except ValueError:
                # grep 只在文本文件上工作；遇到二进制或不可解码文件时静默跳过即可。
                continue

            current_file_hits = 0
            for line_no, line in enumerate(content.splitlines(), start=1):
                line_match_count = len(regex.findall(line))
                if line_match_count <= 0:
                    continue
                current_file_hits += line_match_count
                line_hits.append(f"{self._relative_path(file_path)}:{line_no}:{line}")

            if current_file_hits > 0:
                relative_path = self._relative_path(file_path)
                file_hits[relative_path] = current_file_hits
                total_matches += current_file_hits

        if output_mode == "files_with_matches":
            filenames = sorted(file_hits.keys())
            paged_files, truncated = self._paginate(filenames, offset=offset, head_limit=head_limit)
            return {
                "mode": output_mode,
                "filenames": paged_files,
                "num_files": len(paged_files),
                "num_matches": sum(file_hits[name] for name in paged_files),
                "truncated": truncated,
            }

        if output_mode == "content":
            paged_lines, truncated = self._paginate(line_hits, offset=offset, head_limit=head_limit)
            filenames = sorted({item.split(":", 1)[0] for item in paged_lines})
            return {
                "mode": output_mode,
                "filenames": filenames,
                "num_files": len(filenames),
                "num_matches": len(paged_lines),
                "content": "\n".join(paged_lines),
                "truncated": truncated,
            }

        count_items = sorted(file_hits.items())
        paged_counts, truncated = self._paginate(count_items, offset=offset, head_limit=head_limit)
        counts = {name: count for name, count in paged_counts}
        return {
            "mode": output_mode,
            "filenames": [name for name, _ in paged_counts],
            "num_files": len(paged_counts),
            "num_matches": sum(counts.values()),
            "counts": counts,
            "truncated": truncated,
        }


FILE_TOOLS = {
    "read": ReadTool(),
    "write": WriteTool(),
    "edit": EditTool(),
    "glob": GlobTool(),
    "grep": GrepTool(),
}


__all__ = [
    "ReadTool",
    "WriteTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    "FILE_TOOLS",
]
