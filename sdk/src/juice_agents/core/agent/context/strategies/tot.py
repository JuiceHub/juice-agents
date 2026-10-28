"""每步多候选、择一执行；保留 TreeOfThoughtsStrategy 公开类名。"""

from __future__ import annotations

import logging
import math
from numbers import Real
from typing import TYPE_CHECKING, Any, Callable

from ..steps import ActionStep

if TYPE_CHECKING:
    from ....models.reward_models import BaseRewardModel

logger = logging.getLogger(__name__)


class TreeOfThoughtsStrategy:
    """同一上下文生成 breadth 个输出，只给协议有效的候选评分。

    不展开后续路径、不回溯，也不改变模型采样参数。reward_model 仅是当前
    进程注入的评分器，不进入会话快照；缺少它时自动保持线性执行。
    """

    def __init__(self, breadth: int = 3, reward_model: BaseRewardModel | None = None):
        if isinstance(breadth, bool) or not isinstance(breadth, int) or breadth <= 0:
            raise ValueError("breadth 必须为正整数")
        self.breadth = breadth
        self.reward_model = reward_model

    @property
    def candidate_count(self) -> int:
        return self.breadth if self.reward_model is not None else 1

    def select(
        self, *, context: list[dict[str, Any]], candidates: list[ActionStep],
        on_model_usage: Callable[..., None] | None = None,
    ) -> tuple[int, list[float | None]]:
        scores: list[float | None] = [None] * len(candidates)
        valid_indices = [i for i, candidate in enumerate(candidates) if candidate.error is None]
        # 全部无效时保留首个输出，让原有协议错误恢复流程把错误反馈给下一步。
        if len(candidates) == 1 or self.reward_model is None or not valid_indices:
            return 0, scores

        try:
            result = self.reward_model.evaluate_batch(
                context=context,
                actions=[
                    {"role": "assistant", "content": candidates[i].model_output}
                    for i in valid_indices
                ],
            )
        except Exception as exc:
            if on_model_usage is not None:
                on_model_usage(kind="reward", model=self.reward_model, error=exc)
            raise RuntimeError(f"候选评分失败: {exc}") from exc
        # 先传递请求用量，再校验分数；模型成功响应也可能包含非法评分。
        if on_model_usage is not None:
            on_model_usage(kind="reward", model=self.reward_model, response=result)
        evaluated = result.get("scores") if isinstance(result, dict) else None
        if not isinstance(evaluated, list):
            raise ValueError("候选评分结果必须包含 scores 列表")
        if len(evaluated) != len(valid_indices):
            raise ValueError(
                f"候选评分数量不匹配: expected {len(valid_indices)}, got {len(evaluated)}"
            )
        for index, raw_score in zip(valid_indices, evaluated):
            if isinstance(raw_score, bool) or not isinstance(raw_score, Real):
                raise ValueError(f"候选评分必须是有限数值: {raw_score!r}")
            try:
                score = float(raw_score)
            except (OverflowError, ValueError) as exc:
                raise ValueError(f"候选评分必须是有限数值: {raw_score!r}") from exc
            if not math.isfinite(score):
                raise ValueError(f"候选评分必须是有限数值: {raw_score!r}")
            scores[index] = score

        # max 在同分时保留首次出现的下标；负分也正常参与比较。
        selected = max(valid_indices, key=lambda index: scores[index])
        logger.info("候选选择完成: candidates=%s valid=%s selected=%s scores=%s",
                    len(candidates), len(valid_indices), selected, scores)
        return selected, scores


__all__ = ["TreeOfThoughtsStrategy"]
