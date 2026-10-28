"""默认线性策略：模型采样配置仍由模型自身管理。"""

from __future__ import annotations

from typing import Any, Callable

from ..steps import ActionStep


class GreedyStrategy:
    """每步只生成、执行一个输出，不增加评分请求。"""

    @property
    def candidate_count(self) -> int:
        return 1

    def select(
        self, *, context: list[dict[str, Any]], candidates: list[ActionStep],
        on_model_usage: Callable[..., None] | None = None,
    ) -> tuple[int, list[float | None]]:
        return 0, [None] * len(candidates)


__all__ = ["GreedyStrategy"]
