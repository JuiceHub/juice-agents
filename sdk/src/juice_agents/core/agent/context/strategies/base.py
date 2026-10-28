"""每步候选选择协议；模型生成、动作执行和落树分别由 Agent / Manager 负责。"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from ..steps import ActionStep


class ExplorationStrategy(Protocol):
    """只决定候选数量和选择结果，不调用模型、执行动作或修改上下文树。"""

    @property
    def candidate_count(self) -> int:
        """本步实际生成数；没有评分器时应为 1，避免无意义的模型请求。"""
        ...

    def select(
        self, *, context: list[dict[str, Any]], candidates: list[ActionStep],
        on_model_usage: Callable[..., None] | None = None,
    ) -> tuple[int, list[float | None]]:
        """返回选中下标及各候选分数；协议无效或未评分的候选分数为 None。"""
        ...


__all__ = ["ExplorationStrategy"]
