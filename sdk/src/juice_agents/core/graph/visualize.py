"""
图可视化：导出 mermaid 文本，并可选在线渲染为图片（mermaid.ink）。
"""

from __future__ import annotations

import base64
import logging
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .constants import END, START

logger = logging.getLogger(__name__)


def _make_mermaid_ids(names: Iterable[str]) -> Dict[str, str]:
    """
    将任意节点名映射为合法且唯一的 mermaid node id。
    """

    used: Dict[str, int] = {}
    mapping: Dict[str, str] = {}
    for name in names:
        base = re.sub(r"[^a-zA-Z0-9_]", "_", str(name))
        if not base:
            base = "node"
        if base[0].isdigit():
            base = f"n_{base}"
        idx = used.get(base, 0)
        used[base] = idx + 1
        mid = base if idx == 0 else f"{base}_{idx}"
        mapping[name] = mid
    return mapping


def to_mermaid(compiled_graph: Any, *, kind: str = "flowchart") -> str:
    """
    导出 mermaid 文本。

    Args:
        compiled_graph: `CompiledGraph` 或具备 nodes/edges/conditional 属性的对象
        kind: "flowchart" | "mindmap"
    """

    nodes_dict: Dict[str, Any] = getattr(compiled_graph, "nodes", {}) or {}
    edges: Dict[str, List[str]] = getattr(compiled_graph, "edges", {}) or {}
    conditional: Dict[str, Any] = getattr(compiled_graph, "conditional", {}) or {}

    node_names = [START, END] + list(nodes_dict.keys())
    ids = _make_mermaid_ids(node_names)

    if kind not in {"flowchart", "mindmap"}:
        raise ValueError('kind 必须为 "flowchart" 或 "mindmap"')

    if kind == "mindmap":
        # mindmap 对一般有向图表达能力有限，这里做一个“尽力而为”的树化输出（可能重复节点）
        lines: List[str] = ["mindmap", f"  {ids[START]}(({START}))"]
        visited: set[Tuple[str, str]] = set()

        def emit(parent: str, child: str, depth: int) -> None:
            key = (parent, child)
            if key in visited:
                return
            visited.add(key)
            indent = "  " * depth
            label = child
            if child in (START, END):
                lines.append(f"{indent}{ids[child]}(({child}))")
            else:
                lines.append(f"{indent}{ids[child]}[{label}]")

        # START 的 children
        for d in edges.get(START, []):
            emit(START, d, 2)

        # 其余边平铺（避免复杂 DFS 引入环处理）
        for src, dsts in edges.items():
            if src == START:
                continue
            for d in dsts:
                emit(src, d, 2)

        # conditional edges 也平铺展示
        for src, cond in conditional.items():
            mapping = getattr(cond, "mapping", {}) or {}
            for label, d in mapping.items():
                emit(src, d, 2)

        return "\n".join(lines).strip() + "\n"

    # flowchart
    lines = ["flowchart TD"]
    lines.append(f'  {ids[START]}["{START}"]')
    lines.append(f'  {ids[END]}["{END}"]')
    for n in nodes_dict.keys():
        lines.append(f'  {ids[n]}["{n}"]')

    def add_edge(src: str, dst: str, label: Optional[str] = None) -> None:
        if label is None:
            lines.append(f"  {ids[src]} --> {ids[dst]}")
        else:
            safe = str(label).replace('"', "'")
            lines.append(f'  {ids[src]} -->|"{safe}"| {ids[dst]}')

    for src, dsts in edges.items():
        for d in dsts:
            if d == END:
                add_edge(src, END)
            else:
                add_edge(src, d)

    for src, cond in conditional.items():
        mapping = getattr(cond, "mapping", {}) or {}
        for k, d in mapping.items():
            if d == END:
                add_edge(src, END, label=str(k))
            else:
                add_edge(src, d, label=str(k))

    return "\n".join(lines).strip() + "\n"


def _encode_mermaid_for_ink(mermaid_text: str) -> str:
    raw = mermaid_text.encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii")
    return b64.rstrip("=")


def draw_mermaid_ink(
    mermaid_text: str,
    *,
    output_path: str | Path,
    format: str = "png",
) -> Path:
    """
    使用 mermaid.ink 在线渲染 mermaid 文本为图片文件。

    注意：需要运行环境可联网。
    """

    fmt = format.lower().strip()
    if fmt not in {"png", "svg"}:
        raise ValueError('format 必须为 "png" 或 "svg"')

    code = _encode_mermaid_for_ink(mermaid_text)
    if fmt == "png":
        url = f"https://mermaid.ink/img/{code}?type=png"
    else:
        url = f"https://mermaid.ink/svg/{code}"

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    logger.info("渲染 mermaid.ink: format=%s output=%s", fmt, output)
    req = urllib.request.Request(url, headers={"User-Agent": "juice-agents/graph"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()

    output.write_bytes(data)
    return output


__all__ = ["to_mermaid", "draw_mermaid_ink"]
