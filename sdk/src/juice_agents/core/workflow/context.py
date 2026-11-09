"""WorkflowContext：workflow 脚本可用的全部编排原语。

workflow 按「函数」使用：一次调用 = 加载脚本 → ``run(ctx, args)`` → 返回值。
没有 run 实体、没有缓存与续跑；中途失败就整体重跑。

脚本形态::

    WORKFLOW_METADATA = {...}

    def run(ctx, args):
        brief = ctx.agent(args, instructions=BRIEF, schema=BRIEF_SCHEMA, phase="Brief")
        found = ctx.agent(task, agent="explore")                       # 已注册 agent
        notes = ctx.parallel([lambda t=t: ctx.agent(t, agent="explore", instructions=R) for t in topics])
        ctx.artifact("report.md", text)
        return {...}

``ctx.agent`` 三种用法（统一按 functional 生命周期、不持久化）：

1. ``agent="explore"``：直接用已注册 agent
2. ``agent="explore", instructions=..., tools=...``：在已注册 agent 上临时重写
3. 不传 ``agent``：以内置 general subagent 为基底，完全内联（必须给 instructions）

取消在每次 ``agent`` / ``parallel`` / ``pipeline`` 边界检查；线程不能被强杀。
"""

from __future__ import annotations

import asyncio

from ..runner.execution.async_io import blocking, invoke
from ..runner.execution.async_bridge import run_async
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from uuid import uuid4

from juice_agents.core.agent.builtin.configs import build_builtin_agent_config
from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.utils import parse_model_json

from .types import (
    WorkflowBuildContext,
    WorkflowCancelledError,
    WorkflowConfig,
    WorkflowTimeoutError,
    is_forbidden_inner_tool,
)

logger = logging.getLogger(__name__)

# parallel/pipeline 等待子任务时的取消轮询间隔。
_POLL_SECONDS = 0.1

LifecycleCallback = Callable[[dict[str, Any]], None]


class _WorkflowCancellation(threading.Event):
    """A private stop token linked to the caller, without cancelling its round."""

    def __init__(self, parent: threading.Event) -> None:
        super().__init__()
        self.parent = parent

    def is_set(self) -> bool:
        return super().is_set() or self.parent.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return self.is_set()
            super().wait(_POLL_SECONDS if remaining is None else min(_POLL_SECONDS, remaining))
        return True


class WorkflowContext:
    """一次 workflow 调用的运行时上下文。"""

    def __init__(
        self,
        build: WorkflowBuildContext,
        *,
        name: str,
        declared_tools: Iterable[str],
        read_only: bool,
        config: WorkflowConfig | None = None,
        lifecycle_callback: LifecycleCallback | None = None,
    ) -> None:
        self.build = build
        self.name = name
        self.declared_tools = frozenset(str(item) for item in declared_tools)
        self.read_only = bool(read_only)
        self.config = config or WorkflowConfig()
        self.config.validate()
        self._lifecycle_callback = lifecycle_callback
        self._cancel_event = _WorkflowCancellation(build.cancel_event)
        self._started_at = time.monotonic()
        # 完成计数与其事件快照共用事务；_emit 会在同一线程再次读取快照。
        self._lock = threading.RLock()
        self._phase = ""
        # 进度计数：当前 phase 内已发起 / 已完成的 agent 调用数，供 CLI 显示 x/y。
        self._phase_started = 0
        self._phase_completed = 0
        self.agent_calls = 0

    # -- 控制信号 ---------------------------------------------------------------------

    def cancel(self) -> None:
        """取消当前 workflow 的子工作，保留调用方 round 的独立控制权。"""
        self._cancel_event.set()

    def check_control(self) -> None:
        """在调用边界检查取消与超时。"""

        if self._cancel_event.is_set():
            raise WorkflowCancelledError(f"workflow {self.name} 已取消")
        timeout = self.config.timeout_seconds
        if timeout is not None and time.monotonic() - self._started_at > timeout:
            self._cancel_event.set()
            raise WorkflowTimeoutError(f"workflow {self.name} 超时（{timeout}s）")

    # -- 进度 -------------------------------------------------------------------------

    def phase(self, title: str) -> None:
        """切换当前 phase；``agent(phase=...)`` 是它的简写。"""

        normalized = str(title or "").strip()
        with self._lock:
            if normalized == self._phase:
                return
            self._phase = normalized
            self._phase_started = 0
            self._phase_completed = 0
        self._emit("phase_started", detail=normalized)

    def _emit(self, event: str, **details: Any) -> None:
        callback = self._lifecycle_callback
        if callback is None:
            return
        with self._lock:
            payload = {
                "event": event,
                "workflow_call_id": self.build.workflow_call_id,
                "call_id": self.build.workflow_call_id,
                "owner_agent_id": self.build.owner_agent_id,
                "owner_agent_name": self.build.owner_agent_name,
                "round_id": self.build.round_id,
                "step_num": self.build.step_num,
                "phase": self._phase,
                "completed": self._phase_completed,
                "total": self._phase_started,
                **details,
            }
        try:
            callback(payload)
        except Exception:
            # 进度显示失败不能中断编排。
            logger.exception("workflow lifecycle callback failed: event=%s", event)

    # -- agent ------------------------------------------------------------------------

    def _agent_config(
        self,
        *,
        agent: str | None,
        instructions: str | None,
        tools: Sequence[str] | None,
        schema: dict[str, Any] | None,
        max_steps: int | None,
    ) -> AgentConfig:
        """组装内部 agent 的最终声明：基底 + 临时覆盖 + 工具约束。

        内部 agent 以 accept 运行（不逐次询问），所以工具集必须在调用前一次性定死：
        - 硬禁止工具静默剔除（已注册 agent 可能自带 agent_tool 等，与 self_evolution
          关闭时 registry 剔除工具的做法一致）；
        - 其余工具必须 ⊆ ``WORKFLOW_METADATA.tools``，否则报错；
        - 只读 workflow 只能用只读工具。
        """

        dispatcher = self.build.agent_dispatcher
        if dispatcher is None:
            raise RuntimeError("workflow 调用 agent 需要绑定 Runner（agent_dispatcher 缺失）")
        # 内联 agent 用独立名字：既便于日志区分，也不会被误当作已注册的 general。
        base = (
            dispatcher.resolve_agent(agent)
            if agent
            else build_builtin_agent_config("general_subagent", name="workflow_agent")
        )
        overrides: dict[str, Any] = {}
        if instructions is not None:
            overrides["instructions"] = instructions
        if tools is not None:
            overrides["tools"] = list(tools)
        if schema is not None:
            overrides["output_schema"] = schema
        if max_steps is not None:
            overrides["max_steps"] = max_steps
        config = base.copy_with(**overrides) if overrides else base

        kept = [tool for tool in config.tools if not is_forbidden_inner_tool(tool.name)]
        names = [tool.name for tool in kept if tool.name != "submit_output"]
        undeclared = sorted(set(names) - self.declared_tools)
        if undeclared:
            raise PermissionError(
                f"workflow {self.name} 的 agent 使用了 WORKFLOW_METADATA.tools 未声明的工具: {undeclared}；"
                "请在 metadata 中声明，或用 ctx.agent(tools=...) 覆盖"
            )
        if self.read_only:
            writable = sorted(name for name in names if not dispatcher.tool_is_read_only(name))
            if writable:
                raise PermissionError(f"只读 workflow {self.name} 的 agent 不能使用非只读工具: {writable}")
        final_instructions = str(config.instructions or "")
        if config.output_schema is not None and "submit_output" not in final_instructions:
            # 结构化输出走标准 submit_output 协议，由 Agent 运行时做 schema 校验。
            final_instructions += (
                "\n\nFinish by calling submit_output exactly once with a JSON object "
                "that conforms to the output_schema."
            )
        return config.copy_with(
            tools=[tool.to_dict() for tool in kept],
            instructions=final_instructions,
            enable_skill_tools=False,
            enable_workflow_tools=False,
            lifecycle="functional",
        )

    def agent(self, task: str | dict[str, Any], **kwargs: Any) -> Any:
        return run_async(self.aagent(task, **kwargs))

    async def aagent(
        self,
        task: str | dict[str, Any],
        *,
        agent: str | None = None,
        instructions: str | None = None,
        tools: Sequence[str] | None = None,
        schema: dict[str, Any] | None = None,
        max_steps: int | None = None,
        phase: str | None = None,
        label: str = "",
    ) -> Any:
        """调用一个 functional agent 并返回其输出（有 schema 时为 dict）。

        ``instructions`` / ``tools`` / ``schema`` / ``max_steps`` 传入即覆盖基底声明，
        不传则沿用；不传 ``agent`` 时以内置 general subagent 为基底，必须给 instructions。
        """

        if phase is not None:
            await blocking(self.phase, phase)
        self.check_control()
        if agent is None and not str(instructions or "").strip():
            raise ValueError("ctx.agent 未指定 agent 时必须提供 instructions")
        if max_steps is not None and (isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0):
            raise ValueError("ctx.agent max_steps 必须为正整数")
        config = await blocking(self._agent_config,
            agent=None if agent is None else str(agent).strip(),
            instructions=None if instructions is None else str(instructions).strip(),
            tools=None if tools is None else [str(item).strip() for item in tools if str(item).strip()],
            schema=schema,
            max_steps=max_steps,
        )
        display = str(label or "").strip() or (agent or "agent")
        with self._lock:
            self._phase_started += 1
            self.agent_calls += 1
        agent_call_id = uuid4().hex
        await blocking(self._emit, "node_started", node=display, detail=display, agent_call_id=agent_call_id)
        logger.info(
            "workflow agent start: workflow=%s label=%s base=%s tools=%s",
            self.name, display, agent or "general_subagent", [tool.name for tool in config.tools],
        )
        assert self.build.agent_dispatcher is not None  # _agent_config 已校验
        try:
            dispatcher = self.build.agent_dispatcher
            response = await invoke(getattr(dispatcher, "ainvoke_agent", dispatcher.invoke_agent), {
                "agent_ref": config,
                "task": task if isinstance(task, str) else json.dumps(task, ensure_ascii=False, default=str),
                "owner_agent_name": self.build.owner_agent_name,
                "cancel_event": self._cancel_event,
                "metadata": {"workflow_name": self.name, "workflow_phase": self._phase,
                             "workflow_label": display, "workflow_call_id": self.build.workflow_call_id,
                             "workflow_agent_call_id": agent_call_id},
            })
            raw = response["output"]
        except BaseException as exc:
            await blocking(self._emit, "node_failed", node=display, detail=str(exc), agent_call_id=agent_call_id)
            raise
        def complete() -> None:
            # 计数与事件快照在同一个短事务内，避免并发完成者看到相同计数。
            with self._lock:
                self._phase_completed += 1
                self._emit("node_completed", node=display, detail=display, agent_call_id=agent_call_id,
                           execution_agent_id=response.get("agent_id"))
        await blocking(complete)
        logger.info("workflow agent completed: workflow=%s label=%s", self.name, display)
        if config.output_schema is None:
            return raw
        # 模型的结构化输出可能是 JSON 字符串或已解析对象；统一修复为 dict。
        return dict(parse_model_json(raw, field_name=f"{display} output", expected_type=dict))

    # -- 并发 -------------------------------------------------------------------------

    def parallel(self, fns: Sequence[Callable[[], Any]]) -> list[Any]:
        return run_async(self.aparallel(fns))

    async def aparallel(self, fns: Sequence[Callable[[], Any]]) -> list[Any]:
        """协程并发有统一上限；任一失败/取消都等待其他子任务收尾。"""
        self.check_control()
        slots = asyncio.Semaphore(self.config.max_workers)

        async def execute(fn):
            async with slots:
                self.check_control()
                return await invoke(fn)

        tasks = [asyncio.create_task(execute(fn)) for fn in fns]
        try:
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(pending, timeout=_POLL_SECONDS,
                                                   return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                self.check_control()
            return [task.result() for task in tasks]
        except BaseException:
            self._cancel_event.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    def pipeline(self, items: Sequence[Any], *stages: Callable[[Any], Any]) -> list[Any]:
        return run_async(self.apipeline(items, *stages))

    async def apipeline(self, items: Sequence[Any], *stages: Callable[[Any], Any]) -> list[Any]:
        if not stages:
            raise ValueError("pipeline 至少需要一个 stage")

        async def flow(item):
            for stage in stages:
                self.check_control()
                item = await invoke(stage, item)
            return item

        return await self.aparallel([lambda item=item: flow(item) for item in items])

    # -- 产物 -------------------------------------------------------------------------

    def artifact(self, name: str, content: str | bytes | dict | list) -> Path:
        """写入 ``artifacts/<name>``；dict/list 自动序列化为 JSON。返回绝对路径。"""

        root = self.build.artifacts_dir
        if root is None:
            raise RuntimeError("workflow 未配置 artifacts_dir")
        filename = Path(str(name or "")).name
        if not filename or filename != str(name):
            raise ValueError(f"artifact 名称必须是单个文件名: {name!r}")
        root.mkdir(parents=True, exist_ok=True)
        path = root / filename
        if isinstance(content, bytes):
            path.write_bytes(content)
        elif isinstance(content, (dict, list)):
            path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            path.write_text(str(content), encoding="utf-8")
        logger.info("workflow artifact written: workflow=%s path=%s", self.name, path)
        return path

    async def aartifact(self, name: str, content: str | bytes | dict | list) -> Path:
        return await blocking(self.artifact, name, content)


__all__ = ["LifecycleCallback", "WorkflowContext"]
