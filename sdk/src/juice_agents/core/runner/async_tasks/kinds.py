"""Async task kind 的公开名与内部名映射（单一真相）。

Runner 对外统一使用 `AsyncTaskType`（`local_agent` / `local_bash` / ...），
内部 store 与 executor 使用 `AsyncTaskKind`（`agent_dispatch` / `shell_command` / ...）。

这两套命名的对应关系此前散落在 6 个模块里各写一份（其中两处还在函数内每次调用
重建 dict），任何一处漏改都会让某个 kind 静默退化成 `local_agent`。这里收敛为
唯一一张表，反向表由正向表推导，保证两个方向永远自洽。
"""

from __future__ import annotations

from typing import Any

from ..types.definitions import AsyncTaskType
from .internal_types import AsyncTaskKind

# 唯一需要维护的表：内部 kind -> 公开 type。新增后台任务类型只改这里。
ASYNC_TASK_KIND_TO_PUBLIC: dict[AsyncTaskKind, AsyncTaskType] = {
    "agent_dispatch": "local_agent",
    "shell_command": "local_bash",
    "graph_run": "local_graph",
    "teammate": "teammate",
}

# 反向表从正向表推导，不手写第二份，避免两个方向漂移。
PUBLIC_TO_ASYNC_TASK_KIND: dict[AsyncTaskType, AsyncTaskKind] = {
    public: internal for internal, public in ASYNC_TASK_KIND_TO_PUBLIC.items()
}

# store 校验用的合法内部 kind 集合，同样由正向表推导。
ASYNC_TASK_KINDS: frozenset[str] = frozenset(ASYNC_TASK_KIND_TO_PUBLIC)

# 无法识别时的兜底。历史行为是退化成 agent 派发，这里保持不变。
_DEFAULT_PUBLIC: AsyncTaskType = "local_agent"
_DEFAULT_INTERNAL: AsyncTaskKind = "agent_dispatch"


def to_public_kind(kind: Any) -> AsyncTaskType:
    """把内部 kind 转成公开 type；无法识别时回退到 `local_agent`。"""

    return ASYNC_TASK_KIND_TO_PUBLIC.get(str(kind or "").strip(), _DEFAULT_PUBLIC)  # type: ignore[arg-type]


def to_internal_kind(public: Any) -> AsyncTaskKind:
    """把公开 type 转成内部 kind；无法识别时回退到 `agent_dispatch`。

    已经是内部 kind 的输入按原值返回，方便调用方对混合来源做一次归一化。
    """

    normalized = str(public or "").strip()
    if normalized in ASYNC_TASK_KINDS:
        return normalized  # type: ignore[return-value]
    return PUBLIC_TO_ASYNC_TASK_KIND.get(normalized, _DEFAULT_INTERNAL)  # type: ignore[arg-type]


__all__ = [
    "ASYNC_TASK_KINDS",
    "ASYNC_TASK_KIND_TO_PUBLIC",
    "PUBLIC_TO_ASYNC_TASK_KIND",
    "to_internal_kind",
    "to_public_kind",
]
