"""
统一的子智能体调度工具。

设计目标：
- 对外保持唯一子智能体入口，实际执行语义由 target lifecycle 决定
- functional target 由 AgentManager 创建 fresh 实例，避免直接复用执行现场
- persistent target 由 AgentManager 复用 session/config，并由 AsyncTaskManager 调度
- 配置来源只允许 registry；owner 只保存可调度名称引用
- 返回最小执行结果结构，便于上层 agent 直接消费
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ...runtime.base_tools import ORCHESTRATION_OBSERVATION_CHARS, Tool

logger = logging.getLogger(__name__)


class AgentTool(Tool):
    def execution_policy(self, args: dict[str, Any], context: Any) -> Any:
        del args, context
        from ...runtime.executor import ToolExecutionMode, ToolExecutionPolicy

        return ToolExecutionPolicy(mode=ToolExecutionMode.BACKGROUND)
    name = "agent_tool"
    max_observation_chars = ORCHESTRATION_OBSERVATION_CHARS
    description = "按名称调度子智能体执行任务；functional 子智能体 fresh 运行，persistent worker 复用其 session/config。"
    inputs = {
        "name": {"type": "string", "description": "子智能体名称"},
        "task": {"type": "string", "description": "交给子智能体的任务描述"},
        "task_images": {
            "type": "list",
            "description": "可选任务图片列表",
            "required": False,
        },
        "inherit_session": {
            "type": "boolean",
            "description": "是否复制调用方上下文；省略时 functional 使用空会话，persistent worker 延续自身会话",
            "required": False,
        },
        "max_steps": {
            "type": "integer",
            "description": "可选步数上限覆盖值",
            "required": False,
        },
        "isolation": {
            "type": "string",
            "description": "可选隔离策略：none 或 worktree",
            "required": False,
        },
    }
    outputs = {
        "status": {"type": "string", "description": "launched 或 failed"},
        "async_task_id": {"type": "string", "description": "后台 async task id"},
        "kind": {"type": "string", "description": "固定为 local_agent"},
        "summary": {"type": "string", "description": "agent_tool 生成的执行摘要"},
        "output_dir": {"type": "string", "description": "后台 async task 结果目录"},
        "error": {"type": "string", "description": "失败时的错误说明"},
    }

    def __init__(
        self,
        *,
        owner_agent: Any | None = None,
        agent_config_dir: str | Path | None = None,
        tool_config_dir: str | Path | None = None,
        runtime_config_path: str | Path | None = None,
        model: Any | None = None,
        read_only_only: bool = False,
        allowed_agent_names: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> None:
        super().__init__()
        self.owner_agent = owner_agent
        self.agent_config_dir = None if agent_config_dir is None else Path(agent_config_dir)
        self.tool_config_dir = None if tool_config_dir is None else Path(tool_config_dir)
        # This remains a declarative hint only. Fresh Agent construction is
        # exclusively performed by AgentManager through RunnerContext.
        self.runtime_config_path = None if runtime_config_path is None else Path(runtime_config_path)
        self.model = model
        self.read_only_only = bool(read_only_only)
        self.allowed_agent_names = frozenset(
            str(item).strip()
            for item in (allowed_agent_names or [])
            if str(item).strip()
        )
        if self.read_only_only:
            self.is_read_only = True

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def check_permissions(
        self,
        args: dict[str, Any],
        *,
        permission_mode: str = "default",
        agent_mode: str = "agent",
        **context: Any,
    ) -> "PermissionResult":
        """Apply the bound RunnerConfig's declarative agent allow-list."""
        from juice_agents.core.permissions.policy import PermissionResult

        del permission_mode, agent_mode, context

        agent_name = str(args.get("name") or "").strip()
        if not agent_name:
            return PermissionResult("allow")

        runner = getattr(getattr(self.owner_agent, "runner_context", None), "runner", None)
        policy = getattr(getattr(runner, "config", None), "tool_policy", None)
        allowed = self.allowed_agent_names or frozenset(
            str(item).strip()
            for item in getattr(policy, "allowed_agent_names", ())
            if str(item).strip()
        )
        if not allowed:
            return PermissionResult("passthrough")
        if agent_name not in allowed:
            allowed_str = ", ".join(sorted(allowed)) or "(none)"
            return PermissionResult(
                "deny",
                reason=f"RunnerConfig 只允许调用声明的 Agent ({allowed_str}); got {agent_name!r}",
            )
        return PermissionResult("allow")

    def _available_names(self) -> list[str]:
        names: set[str] = set()
        if self.owner_agent is not None:
            names.update(getattr(self.owner_agent, "available_managed_agent_names", []))
        return sorted(str(item).strip() for item in names if str(item).strip())

    def _agent_config_dir_for(self, name: str) -> Path | None:
        if self.agent_config_dir is not None:
            return self.agent_config_dir
        if self.owner_agent is None:
            return None
        refs = dict(getattr(self.owner_agent, "_runtime_managed_agent_refs", {}) or {})
        ref = dict(refs.get(name) or {})
        raw_dir = ref.get("agent_config_dir")
        return None if raw_dir is None else Path(raw_dir)

    def _enforce_agent_policy(self, agent_name: str) -> None:
        """Repeat the manager policy check at tool-forward time."""

        from juice_agents.core.permissions.policy import PermissionResult

        if not self._has_runner_context() and not self.read_only_only:
            return
        result = self.check_permissions({"name": agent_name})
        if result.behavior == "passthrough" and self.read_only_only:
            # Detached read-only tools have no RunnerConfig.  They may only
            # dispatch their own explicit allow-list, never a default list.
            allowed = self.allowed_agent_names
            result = (
                PermissionResult("allow")
                if agent_name in allowed
                else PermissionResult("deny", reason="detached read-only AgentTool 缺少目标授权")
            )
        if result.behavior == "deny":
            raise ValueError(result.reason)

    def _has_runner_context(self) -> bool:
        if self.owner_agent is None:
            return False
        return getattr(self.owner_agent, "runner_context", None) is not None

    def forward(
        self,
        name: str,
        task: str,
        task_images: list[Any] | None = None,
        inherit_session: bool | None = None,
        max_steps: int | None = None,
        isolation: str | None = None,
    ) -> dict[str, Any]:
        normalized_name = str(name or "").strip()
        normalized_task = str(task or "").strip()
        if not normalized_name:
            return {
                "status": "failed",
                "async_task_id": "",
                "kind": "local_agent",
                "summary": "调用 agent_tool 失败：name 不能为空。",
                "output_dir": "",
                "error": "name 不能为空",
            }
        if not normalized_task:
            return {
                "status": "failed",
                "async_task_id": "",
                "kind": "local_agent",
                "summary": f"调用 agent_tool 失败：name={normalized_name}，task 不能为空。",
                "output_dir": "",
                "error": "task 不能为空",
            }

        try:
            if self.owner_agent is None:
                raise ValueError("agent_tool 尚未绑定 owner_agent，无法创建后台 runtime")
            # The same declarative policy is checked by ToolManager before
            # dispatch; keep this local guard for direct ``forward`` callers.
            self._enforce_agent_policy(normalized_name)
            runner_context = getattr(self.owner_agent, "runner_context", None)
            if runner_context is None:
                raise RuntimeError("agent_tool 必须绑定到 Runner，禁止隐式创建子 Runner")
            available_names = set(self._available_names())
            if normalized_name not in available_names:
                available = ", ".join(sorted(available_names)) or "(none)"
                raise ValueError(f"未找到子智能体 {normalized_name!r}，当前可用名称: {available}")
            agent_config_dir = self._agent_config_dir_for(normalized_name)
            receipt = runner_context.launch_local_agent(
                target_name=normalized_name,
                task=normalized_task,
                task_images=list(task_images or []),
                inherit_session=inherit_session,
                max_steps=max_steps,
                agent_config_dir=None if agent_config_dir is None else str(agent_config_dir),
                tool_config_dir=None if self.tool_config_dir is None else str(self.tool_config_dir),
                model=self.model,
                isolation=isolation,
                max_observation_chars=self.current_max_observation_chars,
            )
        except Exception as exc:  # 返回 failed payload，避免把调度错误升级为协议错误
            logger.exception("agent_tool 调用失败: name=%s", normalized_name)
            return {
                "status": "failed",
                "async_task_id": "",
                "kind": "local_agent",
                "summary": f"调用 agent_tool 失败：name={normalized_name}，原因={exc}。",
                "output_dir": "",
                "error": str(exc),
            }

        receipt["summary"] = f"调用 agent_tool 成功：name={normalized_name}，任务已在后台启动。"
        return dict(receipt)


__all__ = ["AgentTool"]
