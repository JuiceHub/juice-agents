"""
CompiledGraph：Pregel/BSP 执行器。

- 每个 superstep 并行执行所有 ready 节点（ThreadPoolExecutor）
- 收集每个节点返回的 update，再按 reducer 合并到全局 state
- 计算下一轮要执行的节点（edges/conditional/Command/Send）
"""

from __future__ import annotations

import concurrent.futures
import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .constants import END, START
from .state import StateSpec
from .state_graph import ConditionalEdges, NodeSpec
from .types import Command, GraphCancelledError, GraphConfig, GraphIncompleteError, GraphPausedError, Send

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Scheduled:
    """单次调度任务：在某一 superstep 中待执行的节点及其可选 Send payload。"""

    idx: int
    node: str
    send_args: Tuple[Dict[str, Any], ...] = ()


_CALL_WITH_CONFIG_CACHE: Dict[Any, Optional[bool]] = {}


def _inspect_accepts_config(fn: Any) -> Optional[bool]:
    try:
        sig = inspect.signature(fn)
        return len(sig.parameters) >= 2
    except (TypeError, ValueError):  # pragma: no cover
        return None


def _accepts_config_cached(fn: Any) -> Optional[bool]:
    try:
        if fn in _CALL_WITH_CONFIG_CACHE:
            return _CALL_WITH_CONFIG_CACHE[fn]
        accepts = _inspect_accepts_config(fn)
        _CALL_WITH_CONFIG_CACHE[fn] = accepts
        return accepts
    except TypeError:  # unhashable callable
        return _inspect_accepts_config(fn)


def _call_with_config(fn: Any, state: Dict[str, Any], config: GraphConfig) -> Any:
    """
    支持两种 node/router 签名：
    - fn(state)
    - fn(state, config)
    """

    accepts_config = _accepts_config_cached(fn)
    if accepts_config is True:
        return fn(state, config)
    if accepts_config is False:
        return fn(state)

    # 一些内建/可调用对象可能无法 introspect
    try:
        return fn(state, config)
    except Exception:  # pragma: no cover
        # 仅在无法判定签名时回退
        return fn(state)


def _is_send_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, Send) for x in value)


def _freeze_for_dedupe(value: Any) -> Any:
    """将任意 payload 转为可哈希结构，用于 Send 去重键。"""
    if isinstance(value, dict):
        items = [(_freeze_for_dedupe(k), _freeze_for_dedupe(v)) for k, v in value.items()]
        items.sort(key=lambda x: repr(x[0]))
        return ("dict", tuple(items))
    if isinstance(value, (list, tuple)):
        return ("seq", tuple(_freeze_for_dedupe(v) for v in value))
    if isinstance(value, set):
        return ("set", tuple(sorted((_freeze_for_dedupe(v) for v in value), key=repr)))
    try:
        hash(value)
        return ("scalar", value)
    except TypeError:
        return ("repr", repr(value))


def _normalize_send_arg(arg: Any) -> Optional[Dict[str, Any]]:
    if arg is None:
        return None
    if not isinstance(arg, dict):
        raise TypeError("Send.arg 必须为 dict 或 None")
    return dict(arg)


def _merge_send_args(task: _Scheduled, arg: Optional[Dict[str, Any]]) -> _Scheduled:
    if not arg:
        return task
    return _Scheduled(idx=task.idx, node=task.node, send_args=task.send_args + (arg,))


def _normalize_sends(goto: Any) -> Tuple[bool, List[Send]]:
    """
    将节点返回值中的 goto（str/Send/List[Send]/END/None）统一为 (ended, sends)。

    Returns:
        ended: 是否显式跳到 END
        sends: 下一跳 Send 列表
    """

    if goto is None:
        return False, []
    if goto == END:
        return True, []
    if isinstance(goto, str):
        return False, [Send(goto)]
    if isinstance(goto, Send):
        return False, [goto]
    if _is_send_list(goto):
        return False, list(goto)
    raise TypeError("goto 类型无效，应为 str/Send/List[Send]/END/None")


def _dedupe_sends(sends: List[Send]) -> List[Send]:
    seen = set()
    deduped: List[Send] = []
    for send in sends:
        key = (send.node, _freeze_for_dedupe(send.arg))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(send)
    return deduped


def _release_deferred(
    pending: List[_Scheduled], deferred: Dict[str, _Scheduled], cfg: GraphConfig
) -> List[_Scheduled]:
    if pending:
        return pending
    if not deferred:
        return pending
    if cfg.debug:
        logger.debug("释放 defer 节点: %s", list(deferred.keys()))
    pending = list(deferred.values())
    deferred.clear()
    return pending


class CompiledGraph:
    def __init__(
        self,
        *,
        state_spec: StateSpec,
        nodes: Dict[str, NodeSpec],
        edges: Dict[str, List[str]],
        conditional: Dict[str, ConditionalEdges],
        entry_point: str | None,
        finish_point: str | None,
    ) -> None:
        self.state_spec = state_spec
        self.nodes = dict(nodes)
        self.edges = {k: list(v) for k, v in edges.items()}
        self.conditional = dict(conditional)
        self.entry_point = entry_point
        self.finish_point = finish_point

    def invoke(self, initial_state: Dict[str, Any], config: Any = None) -> Dict[str, Any]:
        """执行图至结束，返回最终 state（等价于 stream 最后一事件的 state）。"""
        final_state: Dict[str, Any] = {}
        for event in self.stream(initial_state, config=config):
            final_state = event.get("state", final_state)
        return final_state

    def to_mermaid(self, *, kind: str = "flowchart") -> str:
        from .visualize import to_mermaid

        return to_mermaid(self, kind=kind)

    def draw_mermaid_png(self, output_path: str, *, kind: str = "flowchart") -> Any:
        """
        使用 mermaid.ink 渲染为 PNG 并写入 output_path。

        注意：需要运行环境可联网。
        """

        from .visualize import draw_mermaid_ink

        mermaid_text = self.to_mermaid(kind=kind)
        return draw_mermaid_ink(mermaid_text, output_path=output_path, format="png")

    def stream(self, initial_state: Dict[str, Any], config: Any = None) -> Iterable[Dict[str, Any]]:
        cfg = GraphConfig.from_any(config)
        cfg.validate()

        if not isinstance(initial_state, dict):
            raise TypeError("initial_state 必须为 dict")
        if self.entry_point is None:
            raise ValueError("Graph 未设置 entry point")

        checkpoint = cfg.configurable.get("checkpoint")
        if checkpoint is not None and not isinstance(checkpoint, dict):
            raise TypeError("configurable.checkpoint 必须为 object")
        checkpoint = dict(checkpoint or {})
        state: Dict[str, Any] = dict(checkpoint.get("state") or initial_state)
        deferred: Dict[str, _Scheduled] = {
            str(item["node"]): _Scheduled(
                idx=int(item["idx"]),
                node=str(item["node"]),
                send_args=tuple(dict(arg) for arg in list(item.get("send_args") or [])),
            )
            for item in list(checkpoint.get("deferred") or [])
        }

        # 初始 active：START 的静态边（通常只有 entry_point）
        pending: List[_Scheduled] = [
            _Scheduled(
                idx=int(item["idx"]),
                node=str(item["node"]),
                send_args=tuple(dict(arg) for arg in list(item.get("send_args") or [])),
            )
            for item in list(checkpoint.get("pending") or [])
        ]
        if not checkpoint:
            init_dsts = list(self.edges.get(START, []))
            if not init_dsts:
                init_dsts = [self.entry_point]
            for i, d in enumerate(init_dsts):
                if d != END:
                    pending.append(_Scheduled(idx=i, node=d))

        next_idx = int(checkpoint.get("next_idx") or len(pending))
        first_step = int(checkpoint.get("next_step") or 1)
        scheduled_count = int(checkpoint.get("scheduled_count") or len(pending))
        started_at = time.monotonic()
        checkpoint_callback = cfg.configurable.get("checkpoint_callback")
        event_callback = cfg.configurable.get("event_callback")
        control_callback = cfg.configurable.get("control_callback")
        lifecycle_callback = cfg.configurable.get("lifecycle_callback")

        def _scheduled_payload(task: _Scheduled) -> dict[str, Any]:
            return {"idx": task.idx, "node": task.node, "send_args": [dict(arg) for arg in task.send_args]}

        def _save_checkpoint(
            next_step: int,
            *,
            pending_override: List[_Scheduled] | None = None,
        ) -> None:
            if not callable(checkpoint_callback):
                return
            checkpoint_callback(
                {
                    "state": dict(state),
                    "pending": [
                        _scheduled_payload(task)
                        for task in (pending if pending_override is None else pending_override)
                    ],
                    "deferred": [_scheduled_payload(task) for task in deferred.values()],
                    "next_idx": next_idx,
                    "next_step": next_step,
                    "scheduled_count": scheduled_count,
                }
            )

        def _check_control(
            next_step: int,
            *,
            pending_override: List[_Scheduled] | None = None,
        ) -> None:
            _save_checkpoint(next_step, pending_override=pending_override)
            requested = str(control_callback() or "") if callable(control_callback) else ""
            if requested == "stop":
                raise GraphCancelledError("Graph 已停止")
            if requested == "pause":
                raise GraphPausedError("Graph 已暂停")
            if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                raise GraphCancelledError("Graph 已停止")
            if cfg.pause_event is not None and cfg.pause_event.is_set():
                raise GraphPausedError("Graph 已暂停")
            if cfg.timeout_seconds is not None and time.monotonic() - started_at >= float(cfg.timeout_seconds):
                raise GraphIncompleteError(f"Graph 运行超过 timeout_seconds={cfg.timeout_seconds}")

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=cfg.max_workers)
        wait_for_workers = True
        try:
            for step_idx in range(first_step, cfg.max_steps + 1):
                _check_control(step_idx)
                pending = _release_deferred(pending, deferred, cfg)
                if not pending:
                    logger.info("Graph 结束：无待执行节点")
                    break

                ready = list(pending)
                pending = []

                if cfg.debug:
                    logger.debug(
                        "superstep=%s ready=%s deferred=%s",
                        step_idx,
                        [t.node for t in ready],
                        list(deferred.keys()),
                    )

                # 该 superstep 的所有节点看到相同的 state 快照
                state_snapshot = dict(state)

                def run_one(task: _Scheduled) -> Tuple[int, str, Dict[str, Any], List[Send], bool]:
                    node_name = task.node
                    if node_name not in self.nodes:
                        raise ValueError(f"节点不存在: {node_name}")
                    node_spec = self.nodes[node_name]

                    local_state = dict(state_snapshot)
                    if task.send_args:
                        local_state = self.state_spec.apply_updates(
                            local_state, list(task.send_args), strict_concurrency=False
                        )

                    result = _call_with_config(node_spec.fn, local_state, cfg)

                    update: Dict[str, Any] = {}
                    ended = False
                    sends: List[Send] = []

                    if isinstance(result, Command):
                        update = dict(result.update or {})
                        ended, sends = _normalize_sends(result.goto)
                    elif isinstance(result, Send) or _is_send_list(result):
                        ended, sends = _normalize_sends(result)
                    elif isinstance(result, dict) or result is None:
                        update = dict(result or {})
                    else:
                        raise TypeError(
                            f"节点返回值类型不支持: {type(result).__name__}（应为 dict/Command/Send/List[Send]/None）"
                        )

                    # Command(goto=...) 和节点直接返回 Send/List[Send] 都是
                    # 显式动态路由，不能再被静态 edges/conditional 覆盖。
                    if (
                        isinstance(result, Command)
                        and result.goto is not None
                    ) or isinstance(result, Send) or _is_send_list(result):
                        return task.idx, node_name, update, _dedupe_sends(sends), ended

                    # finish_point 命中则收敛当前分支
                    if self.finish_point is not None and node_name == self.finish_point:
                        return task.idx, node_name, update, [], True

                    # 默认 routing：unconditional edges + conditional edges
                    next_sends: List[Send] = [Send(d) for d in self.edges.get(node_name, []) if d != END]

                    if node_name in self.conditional:
                        cond = self.conditional[node_name]
                        # router 基于“本次节点执行后”的 state（只包含本节点 update + send_args）
                        routed_state = self.state_spec.apply_updates(
                            local_state, [update], strict_concurrency=False
                        )
                        route_key = _call_with_config(cond.router, routed_state, cfg)
                        # router 也允许直接返回 Send / List[Send]
                        if isinstance(route_key, Send) or _is_send_list(route_key):
                            _, routed_sends = _normalize_sends(route_key)
                            next_sends.extend(routed_sends)
                        else:
                            if route_key not in cond.mapping:
                                raise ValueError(f"conditional router 返回值不在 mapping 中: {route_key}")
                            dest = cond.mapping[route_key]
                            if dest == END:
                                ended = True
                            else:
                                next_sends.append(Send(dest))

                    return task.idx, node_name, update, _dedupe_sends(next_sends), ended

                # 并行执行所有 ready tasks
                results: List[Tuple[int, str, Dict[str, Any], List[Send], bool]] = []
                if callable(lifecycle_callback):
                    for index, task in enumerate(ready):
                        lifecycle_callback(
                            {
                                "event": "node_started",
                                "node": task.node,
                                "completed": 0,
                                "total": len(ready),
                                "detail": f"Running {task.node}",
                            }
                        )
                future_to_task = {
                    executor.submit(run_one, task): task
                    for task in ready
                }
                remaining = set(future_to_task)
                completed_in_step = 0
                try:
                    while remaining:
                        done, remaining = concurrent.futures.wait(
                            remaining,
                            timeout=0.1,
                            return_when=concurrent.futures.FIRST_COMPLETED,
                        )
                        if not done:
                            _check_control(step_idx, pending_override=ready)
                            continue
                        for future in done:
                            result = future.result()
                            results.append(result)
                            completed_in_step += 1
                            if callable(lifecycle_callback):
                                lifecycle_callback(
                                    {
                                        "event": "node_completed",
                                        "node": result[1],
                                        "completed": completed_in_step,
                                        "total": len(ready),
                                        "detail": f"Completed {result[1]}",
                                    }
                                )
                except BaseException:
                    wait_for_workers = False
                    for future in remaining:
                        future.cancel()
                    # Most agent workers observe the shared cancel event and exit
                    # quickly. Give them a bounded grace period to run their
                    # cleanup hooks so Agent snapshots are idle before the Graph
                    # reports stopped. External calls that ignore cancellation
                    # cannot block Graph shutdown indefinitely.
                    concurrent.futures.wait(remaining, timeout=1.0)
                    raise

                # 按 idx 排序，保证确定性合并与 stream 输出
                results.sort(key=lambda x: x[0])

                ordered_updates = [u for _, __, u, _, _ in results if u]
                state = self.state_spec.apply_updates(state, ordered_updates, strict_concurrency=True)

                # 产出事件（本轮的所有节点执行结果）
                for task_idx, node_name, upd, sends, ended in results:
                    event = {
                        "step": step_idx,
                        "task_idx": task_idx,
                        "node": node_name,
                        "update": dict(upd),
                        "goto": [s.node for s in sends] if sends else ([END] if ended else []),
                        "state": dict(state),
                    }
                    if callable(event_callback):
                        event_callback(dict(event))
                    yield event

                # 将 sends 入队，同时处理 defer（全局 pending barrier）
                new_pending: List[_Scheduled] = []
                for _, __, ___, sends, ended in results:
                    if ended:
                        # 该分支显式结束；但其他分支仍可能继续
                        continue
                    for s in sends:
                        if s.node == END:
                            continue

                        send_arg = _normalize_send_arg(s.arg)

                        # defer 节点：延后到“所有其他 pending 任务完成后”才执行（map-reduce 语义）
                        if s.node in self.nodes and bool(self.nodes[s.node].defer):
                            if s.node not in deferred:
                                deferred[s.node] = _Scheduled(
                                    idx=next_idx,
                                    node=s.node,
                                    send_args=((send_arg,) if send_arg else ()),
                                )
                                next_idx += 1
                                scheduled_count += 1
                                if scheduled_count > cfg.max_scheduled_tasks:
                                    _save_checkpoint(step_idx + 1)
                                    raise GraphIncompleteError(
                                        f"Graph 调度任务数超过 max_scheduled_tasks={cfg.max_scheduled_tasks}"
                                    )
                            else:
                                deferred[s.node] = _merge_send_args(deferred[s.node], send_arg)
                            continue

                        new_pending.append(
                            _Scheduled(
                                idx=next_idx,
                                node=s.node,
                                send_args=((send_arg,) if send_arg else ()),
                            )
                        )
                        next_idx += 1
                        scheduled_count += 1
                        if scheduled_count > cfg.max_scheduled_tasks:
                            _save_checkpoint(step_idx + 1)
                            raise GraphIncompleteError(
                                f"Graph 调度任务数超过 max_scheduled_tasks={cfg.max_scheduled_tasks}"
                            )

                pending.extend(new_pending)

                # 若此时没有任何非-defer 任务待执行，则释放 defer 任务进入下一轮
                pending = _release_deferred(pending, deferred, cfg)
                _save_checkpoint(step_idx + 1)

            else:
                raise GraphIncompleteError(f"Graph 达到 max_steps={cfg.max_steps} 仍未完成")
        finally:
            executor.shutdown(wait=wait_for_workers, cancel_futures=not wait_for_workers)

        # stream 结束时仍返回最终 state
        yield {"step": None, "task_idx": None, "node": None, "update": {}, "goto": [], "state": dict(state)}


__all__ = ["CompiledGraph"]
