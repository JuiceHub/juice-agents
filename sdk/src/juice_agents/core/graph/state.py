"""
State 规格（reducers）与更新合并。

参考 LangGraph 的思想：每个 key 可声明 reducer（如 operator.add），用于并行更新合并。
若同一 superstep 多节点更新同一 key 且未声明 reducer，则抛错，避免 silent last-write。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, get_args, get_origin, get_type_hints

try:
    from typing import Annotated  # Python 3.10+
except Exception:  # pragma: no cover
    Annotated = None  # type: ignore

from .types import InvalidConcurrentGraphUpdate

logger = logging.getLogger(__name__)

Reducer = Callable[[Any, Any], Any]


def _extract_reducer_from_annotated(anno: Any) -> Optional[Reducer]:
    """
    从 typing.Annotated[T, reducer, ...] 提取 reducer。

    LangGraph 常见写法：Annotated[list[str], operator.add]
    这里取 metadata 中第一个可调用对象作为 reducer。
    """

    origin = get_origin(anno)
    if origin is None:
        return None
    # get_origin(Annotated[T, ...]) 在 py3.10+ 通常返回 typing.Annotated
    if Annotated is not None and origin is not Annotated:
        return None

    args = get_args(anno)
    if not args:
        return None
    # args[0] 是底层类型，后面是 metadata
    for meta in args[1:]:
        if callable(meta):
            return meta  # type: ignore[return-value]
    return None


def _get_typed_dict_annotations(state_type: type) -> Dict[str, Any]:
    """
    统一获取类型注解（包含 Annotated metadata）。

    - 优先使用 get_type_hints(..., include_extras=True)，以支持：
      - from __future__ import annotations（注解为字符串）
      - Annotated[..., reducer] 的 metadata
    - 若解析失败则退化为直接读取 __annotations__。
    """

    try:
        # include_extras=True 才会保留 Annotated 的 metadata（reducer）
        return dict(get_type_hints(state_type, include_extras=True))
    except Exception:
        ann = getattr(state_type, "__annotations__", None)
        if not isinstance(ann, dict):
            return {}
        return dict(ann)


@dataclass(frozen=True)
class StateSpec:
    """
    State 规格：reducers 映射与合并逻辑。

    - reducers: key -> reducer(current_value, update_value) -> merged_value
    """

    reducers: Dict[str, Reducer]

    @classmethod
    def from_state_type(cls, state_type: type | None) -> "StateSpec":
        if state_type is None:
            return cls(reducers={})
        reducers: Dict[str, Reducer] = {}
        for key, anno in _get_typed_dict_annotations(state_type).items():
            reducer = _extract_reducer_from_annotated(anno)
            if reducer is not None:
                reducers[key] = reducer
        return cls(reducers=reducers)

    def apply_updates(
        self,
        state: Dict[str, Any],
        ordered_updates: list[Dict[str, Any]],
        *,
        strict_concurrency: bool = True,
    ) -> Dict[str, Any]:
        """
        将同一 superstep 内的多个 update（按确定性顺序）合并进 state。

        Args:
            state: 当前 state（会复制后返回）
            ordered_updates: update dict 列表（建议按执行顺序排序）
            strict_concurrency: True 时，对未声明 reducer 的并发同 key 更新抛错

        Returns:
            new_state: 合并后的 state（新 dict）
        """

        if not isinstance(state, dict):
            raise TypeError("state 必须为 dict")
        if not isinstance(ordered_updates, list) or any(not isinstance(u, dict) for u in ordered_updates):
            raise TypeError("ordered_updates 必须为 List[dict]")

        new_state = dict(state)
        # 收集 key -> list[values]
        by_key: Dict[str, list[Any]] = {}
        for upd in ordered_updates:
            for k, v in upd.items():
                by_key.setdefault(k, []).append(v)

        for k, values in by_key.items():
            if not values:
                continue
            reducer = self.reducers.get(k)
            if reducer is None and strict_concurrency and len(values) > 1:
                raise InvalidConcurrentGraphUpdate(
                    f"并发更新冲突：key='{k}' 在同一轮被更新 {len(values)} 次，但未声明 reducer"
                )

            if reducer is None:
                # 覆盖：取最后一次（即 ordered_updates 的最后出现）
                new_state[k] = values[-1]
                continue

            # reducer 合并：从当前值开始折叠；若当前不存在则以第一项为初值
            if k in new_state:
                acc = new_state.get(k)
                for v in values:
                    acc = reducer(acc, v)
            else:
                acc = values[0]
                for v in values[1:]:
                    acc = reducer(acc, v)
            new_state[k] = acc

        return new_state


__all__ = ["StateSpec", "Reducer"]
