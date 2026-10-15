"""WorkflowRunManager：把 workflow 当函数调用。

一次 ``call`` = 校验并加载脚本 → 构造 WorkflowContext → ``run(ctx, args)`` → 返回值。
不登记 run、不缓存、不支持 pause/resume；后台运行由 AsyncTaskManager 承担
（``local_workflow`` 任务），取消沿用调用方的取消信号。

唯一落盘的是脚本通过 ``ctx.artifact`` 写出的产物：
``<runner_root>/workflows/<workflow_name>-<call_id>/``。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.workflows import WorkflowMetadata, WorkflowRegistry
from juice_agents.core.workflow.context import WorkflowContext
from juice_agents.core.workflow.types import (
    WorkflowAgentDispatcher,
    WorkflowBuildContext,
    WorkflowConfig,
)

logger = logging.getLogger(__name__)

WorkflowLifecycleCallback = Callable[[dict[str, Any]], None]


class WorkflowRunManager:
    """无状态的 workflow 调用入口；只持有组合期注入的依赖，不持有任何运行态。

    Manager 不持有 Runner / AgentManager / 活 Agent；内部 agent 一律通过
    ``agent_dispatcher`` 这座窄桥请求。
    """

    def __init__(
        self,
        *,
        runner_root: str | Path,
        registry: WorkflowRegistry,
        config_context: ConfigurationContext,
        runner_id: str = "",
        agent_dispatcher: WorkflowAgentDispatcher | None = None,
        owner_agent_name: str = "",
        lifecycle_callback: WorkflowLifecycleCallback | None = None,
    ) -> None:
        self.workflows_dir = Path(runner_root).expanduser().resolve() / "workflows"
        self.registry = registry
        self.config_context = config_context
        self.runner_id = str(runner_id or "")
        self.agent_dispatcher = agent_dispatcher
        self.owner_agent_name = str(owner_agent_name or "")
        self._lifecycle_callback = lifecycle_callback

    def call(
        self,
        workflow: str | WorkflowMetadata,
        args: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        owner_agent_name: str = "",
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """同步执行一次 workflow，返回 ``{"result", "artifacts_dir", "stats"}``。

        传入 metadata（例如工具权限检查时拿到的那一份）会绑定其源码哈希：
        脚本在审批后被改写则拒绝执行。异常原样抛出，由调用方决定如何呈现。
        """

        if not isinstance(args, dict):
            raise ValueError("workflow args 必须为 object")
        meta = self.registry.validate(workflow)
        run_config = WorkflowConfig.from_any(config)
        run_fn = self.registry.instantiate(meta)
        artifacts_dir = self.workflows_dir / f"{meta.name}-{uuid4().hex[:12]}"
        build = WorkflowBuildContext(
            config_context=self.config_context,
            workspace_dir=self.config_context.workspace_dir,
            runner_id=self.runner_id,
            artifacts_dir=artifacts_dir,
            agent_dispatcher=self.agent_dispatcher,
            owner_agent_name=str(owner_agent_name or self.owner_agent_name),
            cancel_event=cancel_event or threading.Event(),
        )
        ctx = WorkflowContext(
            build,
            name=meta.name,
            declared_tools=meta.tools,
            read_only=meta.read_only,
            config=run_config,
            lifecycle_callback=lambda event: self._emit(
                str(event.get("event") or ""),
                workflow_name=meta.name,
                **{key: value for key, value in event.items() if key != "event"},
            ),
        )
        self._emit("workflow_started", workflow_name=meta.name)
        logger.info("workflow call started: workflow=%s", meta.name)
        try:
            result = run_fn(ctx, dict(args))
        except BaseException as exc:
            self._emit("workflow_failed", workflow_name=meta.name, detail=str(exc))
            logger.warning("workflow call failed: workflow=%s error=%s", meta.name, exc)
            raise
        self._emit("workflow_completed", workflow_name=meta.name)
        logger.info("workflow call completed: workflow=%s agent_calls=%d", meta.name, ctx.agent_calls)
        return {
            "result": result,
            "artifacts_dir": str(artifacts_dir) if artifacts_dir.exists() else "",
            "stats": {"agent_calls": ctx.agent_calls},
        }

    def _emit(self, event: str, **details: Any) -> None:
        callback = self._lifecycle_callback
        if callback is None:
            return
        try:
            callback({"scope": "workflow", "event": str(event), **details})
        except Exception:
            logger.exception("workflow lifecycle callback failed: event=%s", event)


__all__ = ["WorkflowLifecycleCallback", "WorkflowRunManager"]
