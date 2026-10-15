"""Workflow 的只读发现与执行工具。

list/view 是调用面的发现入口（找到 workflow → ``workflow_run`` 执行），受
``workflows.enabled`` 约束；写入侧 ``workflow_manage`` 在 ``builtin/evolution/``，
它继承这里的 ``WorkflowToolBase``，并额外受 ``self_evolution.enabled`` 约束。

``workflow_run`` 的权限按目标 workflow 的 metadata 决定：
- ``allowed_modes`` 不含当前 mode → 直接拒绝
- ``read_only=True`` → 视为只读工具，启动免审批，plan mode 可用
- ``read_only=False`` → 走现有审批（「始终允许」规则绑定源码哈希）
"""

from __future__ import annotations

import logging
from typing import Any

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.managers.workflows import WorkflowRunManager
from juice_agents.core.registry.workflows import WorkflowMetadata, WorkflowRegistry

from ...runtime.base_tools import ORCHESTRATION_OBSERVATION_CHARS, Tool
from ...runtime.owner_context import resolve_owner_context

logger = logging.getLogger(__name__)


class WorkflowToolBase(Tool):
    max_observation_chars = ORCHESTRATION_OBSERVATION_CHARS

    def __init__(self, *, workflow_registry: WorkflowRegistry | None = None) -> None:
        super().__init__()
        self.owner_agent: Any | None = None
        self._explicit_registry = workflow_registry

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _config_context(self) -> ConfigurationContext:
        return resolve_owner_context(self.owner_agent, explicit_workspace_dir=None)

    def _registry(self) -> WorkflowRegistry:
        return self._explicit_registry or WorkflowRegistry(config_context=self._config_context())

    def _runner(self) -> Any | None:
        return getattr(getattr(self.owner_agent, "runner_context", None), "runner", None)

    def _manager(self) -> WorkflowRunManager:
        manager = getattr(self._runner(), "workflow_manager", None)
        if not isinstance(manager, WorkflowRunManager):
            raise RuntimeError("workflow_run 必须绑定 Runner 的 WorkflowRunManager")
        return manager


class WorkflowListTool(WorkflowToolBase):
    _execution_mode = "parallel_safe"
    name = "workflow_list"
    is_read_only = True
    description = "列出 builtin 和 workspace-local workflows"
    inputs = {}
    outputs = {"workflows": {"type": "list", "description": "workflow 元数据列表"}}

    def forward(self) -> dict[str, Any]:
        items = [item.to_dict() for item in self._registry().list_metadata()]
        return {"success": True, "workflows": items, "count": len(items)}


class WorkflowViewTool(WorkflowToolBase):
    _execution_mode = "parallel_safe"
    name = "workflow_view"
    is_read_only = True
    description = "查看 workflow metadata 和单文件源码"
    inputs = {"name": {"type": "string", "description": "workflow 名称"}}
    outputs = {"content": {"type": "string", "description": "workflow 源码"}}

    def forward(self, name: str) -> dict[str, Any]:
        try:
            return self._registry().view(name)
        except Exception as exc:
            return {"success": False, "error": str(exc), "name": str(name or "")}


class WorkflowRunTool(WorkflowToolBase):
    """同步或后台调用一个 workflow。"""

    name = "workflow_run"
    description = (
        "像函数一样调用 workflow 并返回结果。只读 workflow 免审批；非只读 workflow "
        "启动时审批一次，之后内部 agent 不再逐次询问。产物写入 artifacts_dir。"
    )
    inputs = {
        "name": {"type": "string", "description": "workflow 名称"},
        "args": {"type": "object", "description": "workflow 输入（按 input_schema 构造）"},
        "config": {"type": "object", "description": "可选：max_workers / timeout_seconds", "required": False},
        "background": {"type": "boolean", "description": "是否后台运行", "required": False},
    }
    outputs = {
        "status": {"type": "string", "description": "completed/pending/failed"},
        "result": {"type": "object", "description": "同步结果（run 的返回值）"},
        "artifacts_dir": {"type": "string", "description": "workflow artifacts 目录"},
        "error": {"type": "string", "description": "错误"},
    }

    def __init__(self, *, workflow_registry: WorkflowRegistry | None = None) -> None:
        super().__init__(workflow_registry=workflow_registry)
        # ``is_read_only`` 是 PermissionEngine 读取的类属性，但它取决于本次调用的
        # 目标 workflow。check_permissions 在同一次调用的执行之前运行，这里记下
        # 校验时的 metadata，forward 执行的正是这一份（源码哈希绑定）。
        self._approved: WorkflowMetadata | None = None

    def execution_policy(self, args: dict[str, Any], context: Any) -> Any:
        del context
        from ...runtime.executor import ToolExecutionMode, ToolExecutionPolicy

        background = bool(args.get("background"))
        return ToolExecutionPolicy(
            mode=ToolExecutionMode.BACKGROUND if background else ToolExecutionMode.BARRIER,
            thread_affinity="any" if background else "main",
        )

    def _metadata(self, name: Any) -> WorkflowMetadata:
        return self._registry().validate(str(name or "").strip())

    def is_read_only_for(self, args: dict[str, Any]) -> bool:
        """PermissionEngine 用：按目标 workflow 的声明判断本次调用是否只读。"""

        try:
            return self._metadata(args.get("name")).read_only
        except Exception:
            return False

    def check_permissions(
        self,
        args: dict[str, Any],
        *,
        permission_mode: str = "default",
        agent_mode: str = "agent",
        **context: Any,
    ) -> Any:
        from juice_agents.core.permissions.policy import PermissionResult

        del permission_mode, context
        try:
            meta = self._metadata(args.get("name"))
        except Exception as exc:
            return PermissionResult("deny", reason=str(exc))
        if not meta.allows_mode(agent_mode):
            return PermissionResult("deny", reason=f"workflow {meta.name} 不允许在 {agent_mode!r} mode 运行")
        self._approved = meta
        if meta.read_only:
            return PermissionResult("allow")
        if agent_mode == "plan":
            return PermissionResult("deny", reason=f"Plan mode 不允许非只读 workflow {meta.name!r}")
        # 非只读：交给 PermissionEngine 的规则与审批流程。
        return PermissionResult("passthrough")

    def _failed(self, *, name: str, error: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "kind": "local_workflow",
            "async_task_id": "",
            "workflow_name": name,
            "summary": f"workflow 执行失败：{error}",
            "result": {},
            "artifacts_dir": "",
            "error": error,
        }

    def forward(
        self,
        name: str,
        args: dict[str, Any],
        config: dict[str, Any] | None = None,
        background: bool = False,
    ) -> dict[str, Any]:
        workflow_name = str(name or "").strip()
        if not workflow_name:
            return self._failed(name="", error="name 不能为空")
        if not isinstance(args, dict):
            return self._failed(name=workflow_name, error="args 必须为 object")
        if config is not None and not isinstance(config, dict):
            return self._failed(name=workflow_name, error="config 必须为 object 或 None")
        # 只执行通过 check_permissions 的那一份 metadata；没有经过权限检查
        # （例如直接调用 forward）时现场校验一次。
        approved = self._approved if self._approved is not None and self._approved.name == workflow_name else None
        self._approved = None
        try:
            meta = approved or self._metadata(workflow_name)
            if background:
                runner_context = getattr(self.owner_agent, "runner_context", None)
                if runner_context is None:
                    raise ValueError("workflow_run(background=True) 需要绑定 Runner")
                receipt = runner_context.launch_local_workflow(
                    workflow=meta,
                    args=dict(args),
                    config=None if config is None else dict(config),
                    max_observation_chars=self.current_max_observation_chars,
                )
                return {**dict(receipt), "workflow_name": workflow_name}
            runner_context = getattr(self.owner_agent, "runner_context", None)
            called = self._manager().call(
                meta,
                dict(args),
                config,
                owner_agent_name=str(getattr(self.owner_agent, "name", "") or ""),
                # 前台调用继承当前 stream 的取消信号（Esc / Ctrl+C）。
                cancel_event=getattr(runner_context, "cancel_event", None),
            )
            return {
                "status": "completed",
                "kind": "local_workflow",
                "async_task_id": "",
                "workflow_name": workflow_name,
                "summary": f"workflow completed: {workflow_name}",
                "result": called["result"],
                "artifacts_dir": called["artifacts_dir"],
                "error": "",
            }
        except Exception as exc:
            logger.exception("workflow_run 调用失败: workflow=%s", workflow_name)
            return self._failed(name=workflow_name, error=str(exc))


__all__ = ["WorkflowListTool", "WorkflowRunTool", "WorkflowToolBase", "WorkflowViewTool"]
