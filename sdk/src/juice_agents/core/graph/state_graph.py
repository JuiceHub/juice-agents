"""
StateGraph：构图 API（LangGraph 风格）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .constants import END, START
from .state import StateSpec

if TYPE_CHECKING:
    from .runtime import CompiledGraph

logger = logging.getLogger(__name__)

NodeFn = Callable[..., Any]
RouterFn = Callable[..., Any]


@dataclass(frozen=True)
class NodeSpec:
    """单节点规格：名称、节点函数、是否延迟执行（defer=True 时等所有非 defer 节点完成后再执行）。"""
    name: str
    fn: NodeFn
    defer: bool = False


@dataclass(frozen=True)
class ConditionalEdges:
    """条件边：router(state) 返回值映射到下一跳节点名或 END。"""
    router: RouterFn
    mapping: Dict[Any, Any]


class StateGraph:
    """
    LangGraph 风格的 StateGraph builder。

    常用流程：
        g = StateGraph(StateTypedDict)
        g.add_node("A", fnA)
        g.add_node("B", fnB)
        g.set_entry_point("A")
        g.add_edge("A", "B")
        g.add_edge("B", END)
        app = g.compile()
        out_state = app.invoke({"x": 1})
    """

    def __init__(self, state_type: type | None = None) -> None:
        self.state_type = state_type
        self.state_spec = StateSpec.from_state_type(state_type)
        self._nodes: Dict[str, NodeSpec] = {}
        self._edges: Dict[str, List[str]] = {}
        self._conditional: Dict[str, ConditionalEdges] = {}
        self._entry_point: Optional[str] = None
        self._finish_point: Optional[str] = None

    @property
    def nodes(self) -> Dict[str, NodeSpec]:
        return dict(self._nodes)

    @property
    def edges(self) -> Dict[str, List[str]]:
        return {k: list(v) for k, v in self._edges.items()}

    @property
    def conditional_edges(self) -> Dict[str, ConditionalEdges]:
        return dict(self._conditional)

    def add_node(self, name: str, fn: NodeFn, *, defer: bool = False) -> "StateGraph":
        if not isinstance(name, str) or not name.strip():
            raise ValueError("node name 必须为非空字符串")
        if name in (START, END):
            raise ValueError(f"node name 不能为保留字: {name}")
        if not callable(fn):
            raise TypeError("fn 必须可调用")
        if name in self._nodes:
            raise ValueError(f"node 重名: {name}")
        self._nodes[name] = NodeSpec(name=name, fn=fn, defer=bool(defer))
        logger.debug("添加节点: %s defer=%s", name, defer)
        return self

    def add_edge(self, src: str, dst: str | List[str]) -> "StateGraph":
        if not isinstance(src, str) or not src.strip():
            raise ValueError("src 必须为非空字符串")
        if src != START and src not in self._nodes:
            raise ValueError(f"src 节点不存在: {src}")

        dsts: List[str]
        if isinstance(dst, str):
            dsts = [dst]
        elif isinstance(dst, list) and all(isinstance(x, str) and x.strip() for x in dst):
            dsts = list(dst)
        else:
            raise TypeError("dst 必须为 str 或 List[str]")

        for d in dsts:
            if d != END and d not in self._nodes:
                raise ValueError(f"dst 节点不存在: {d}")

        self._edges.setdefault(src, [])
        for d in dsts:
            if d not in self._edges[src]:
                self._edges[src].append(d)
        logger.debug("添加边: %s -> %s", src, dsts)
        return self

    def add_sequence(self, nodes: List[str]) -> "StateGraph":
        """按顺序添加边：nodes[0] -> nodes[1] -> ... -> nodes[-1]。"""
        if not isinstance(nodes, list) or len(nodes) < 2:
            raise ValueError("sequence 至少包含 2 个节点名")
        for n in nodes:
            if not isinstance(n, str) or not n.strip():
                raise ValueError("sequence 节点名必须为非空字符串")
            if n not in self._nodes:
                raise ValueError(f"sequence 节点不存在: {n}")
        for a, b in zip(nodes, nodes[1:]):
            self.add_edge(a, b)
        return self

    def add_conditional_edges(self, src: str, router: RouterFn, mapping: Dict[Any, Any]) -> "StateGraph":
        if not isinstance(src, str) or not src.strip():
            raise ValueError("src 必须为非空字符串")
        if src != START and src not in self._nodes:
            raise ValueError(f"src 节点不存在: {src}")
        if not callable(router):
            raise TypeError("router 必须可调用")
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("mapping 必须为非空 dict")

        # mapping 的 value 可以是节点名或 END
        for k, v in mapping.items():
            if not isinstance(k, (str, int, float, bool, tuple)) and k is not None:
                # 允许一些常见可哈希类型；更复杂类型也可用，但这里做温和校验
                logger.debug("mapping key 类型较少见: %s (%s)", k, type(k).__name__)
            if v != END and (not isinstance(v, str) or v not in self._nodes):
                raise ValueError(f"conditional mapping 目标无效: {k} -> {v}")

        if src in self._conditional:
            raise ValueError(f"src 已存在 conditional edges: {src}")
        self._conditional[src] = ConditionalEdges(router=router, mapping=dict(mapping))
        logger.debug("添加 conditional edges: %s keys=%s", src, list(mapping.keys()))
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        if not isinstance(name, str) or not name.strip():
            raise ValueError("entry point 必须为非空字符串")
        if name not in self._nodes:
            raise ValueError(f"entry point 节点不存在: {name}")
        self._entry_point = name
        # 与 LangGraph 约定一致：START -> entry
        self.add_edge(START, name)
        return self

    def set_finish_point(self, name: str) -> "StateGraph":
        if name != END and name not in self._nodes:
            raise ValueError(f"finish point 节点不存在: {name}")
        self._finish_point = name
        if name != END:
            self.add_edge(name, END)
        return self

    def _validate(self) -> None:
        if self._entry_point is None:
            raise ValueError("必须设置 entry point（set_entry_point）")
        if self._entry_point not in self._nodes:
            raise ValueError("entry point 节点不存在（可能被删除）")

    def compile(self) -> "CompiledGraph":
        from .runtime import CompiledGraph

        self._validate()
        return CompiledGraph(
            state_spec=self.state_spec,
            nodes=self._nodes,
            edges=self._edges,
            conditional=self._conditional,
            entry_point=self._entry_point,
            finish_point=self._finish_point,
        )


__all__ = ["StateGraph"]
