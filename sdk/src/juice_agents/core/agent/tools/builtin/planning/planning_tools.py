"""
内部工具模块，用于扩展智能体的能力。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from juice_agents.core.runner.types.ask import normalize_ask_response

from ...runtime.base_tools import Tool

logger = logging.getLogger(__name__)


class _BaseMarkdownInitTool(Tool):
    """
    Markdown 初始化工具基类。

    `todos` 和 `plan` 的职责相同，只有语义和默认路径不同，因此共用同一套
    覆盖写入逻辑，避免两个工具的行为继续分叉。
    """

    def __init__(self, file_path: str) -> None:
        super().__init__()
        self.file_path = Path(file_path)
        # Construction can be triggered from an editable declaration.  Do not
        # create even a directory until ``forward`` has passed the owning
        # tool's authorization checks (PlanTool verifies trusted root there).
        logger.info("%s initialized for path: %s", self.__class__.__name__, self.file_path)

    def forward(self, content: str) -> Dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError("content 必须是字符串")

        existed = self.file_path.exists()
        if existed and self.file_path.is_dir():
            raise ValueError(f"目标路径是目录，不能写入 Markdown 文件: {self.file_path}")

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(content, encoding="utf-8")
        bytes_written = len(content.encode("utf-8"))
        logger.info("%s 已写入 %s (%d bytes)", self.name, self.file_path, bytes_written)
        return {
            "content": content,
            "file_path": str(self.file_path),
            "bytes_written": bytes_written,
            "overwritten": existed,
        }


class TodosTool(_BaseMarkdownInitTool):
    """初始化或重置短期引导型待办 Markdown 文件。"""

    name = "todos"
    description = "初始化或重置短期引导型待办 Markdown 文件"
    inputs = {
        "content": {"type": "string", "description": "完整的 Markdown 待办内容"},
    }
    outputs = {
        "content": {"type": "string", "description": "写入的 Markdown 内容"},
        "file_path": {"type": "string", "description": "Markdown 文件路径"},
        "bytes_written": {"type": "integer", "description": "写入字节数"},
        "overwritten": {"type": "boolean", "description": "是否覆盖已有文件"},
    }


class PlanTool(_BaseMarkdownInitTool):
    """初始化或重置详细计划书 Markdown 文件。"""

    name = "plan"
    description = "初始化或重置详细计划书 Markdown 文件"
    # Plan mode treats runner-owned plan state as project-read-only: this tool
    # never edits repository files, executes code, or mutates external systems.
    is_read_only = True
    inputs = {
        "content": {"type": "string", "description": "完整的 Markdown 计划书内容"},
    }
    outputs = {
        "content": {"type": "string", "description": "写入的 Markdown 内容"},
        "file_path": {"type": "string", "description": "Markdown 文件路径"},
        "bytes_written": {"type": "integer", "description": "写入字节数"},
        "overwritten": {"type": "boolean", "description": "是否覆盖已有文件"},
    }

    def _trusted_root_context(self) -> Any | None:
        """Return the manager-verified Plan Mode root context.

        Tool declarations are user-editable data, so neither a tool name nor a
        claimed Agent role is authorization.  The live ``ManagedAgent`` record
        is the authority: it must be the Runner's root and point at this
        tool's owner instance.
        """

        owner = getattr(self, "owner_agent", None)
        context = getattr(owner, "runner_context", None)
        runner = getattr(context, "runner", None)
        if runner is None or owner is None:
            return None
        try:
            from juice_agents.core.runner.config import Capability

            managed = runner.agent_manager.get(str(getattr(context, "agent_id", "") or ""))
            if (
                not bool(getattr(managed, "is_root", False))
                or managed.instance is not owner
                or str(getattr(managed, "agent_id", "")) != str(getattr(runner, "root_agent_id", ""))
                or str(getattr(runner, "mode_id", "")) != "plan"
                or not bool(runner.has_capability(Capability.PLANNING))
            ):
                return None
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        return context

    def forward(self, content: str) -> Dict[str, Any]:
        """Write only the current Runner's formal ``plans/root.md`` artifact."""

        context = self._trusted_root_context()
        if context is None:
            raise PermissionError("plan 仅允许由当前 Plan Mode 的可信 root 使用")
        expected_path = Path(context.get_plan_file_path()).resolve()
        if self.file_path.resolve() != expected_path:
            raise PermissionError("plan 工具路径必须是当前 Runner 的 plans/root.md")
        return super().forward(content)


class ExitPlanTool(Tool):
    """提交 plan 文件并请求用户批准退出 plan mode。"""

    name = "exit_plan"
    description = (
        "在 plan mode 中提交已写入 plan 文件的实施计划并请求用户审批；"
        "仅在计划完整、无未决问题时使用。"
    )
    is_terminal = True
    # Approval only reads runner-owned plan content and records plan-mode state;
    # it does not modify project files.
    is_read_only = True
    inputs: Dict[str, Dict[str, Any]] = {}
    outputs = {
        "approved": {"type": "boolean", "description": "用户是否批准退出 plan mode"},
        "plan": {"type": "string", "description": "已审批的计划内容"},
        "plan_file": {"type": "string", "description": "计划文件路径"},
        "feedback": {"type": "string", "description": "拒绝或补充反馈"},
        "error": {"type": "string", "description": "无法请求审批的原因"},
    }

    def __init__(self) -> None:
        super().__init__()
        self.owner_agent: Any | None = None

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _runner_context(self) -> Any | None:
        direct_context = getattr(self, "runner_context", None)
        if direct_context is not None:
            return direct_context
        if self.owner_agent is None:
            return None
        return getattr(self.owner_agent, "runner_context", None)

    def _approval_request(self, *, plan: str, plan_file: Path) -> dict[str, Any]:
        preview = plan.strip()
        question = (
            "Approve this implementation plan and exit plan mode?\n\n"
            f"Plan file: {plan_file}\n\n"
            f"{preview}"
        )
        return {
            "request_id": f"exit-plan-{uuid4().hex[:12]}",
            "question": question,
            "options": [
                {"label": "Approve", "value": "approve", "description": "Exit plan mode and continue with implementation."},
                {"label": "Reject", "value": "reject", "description": "Stay in plan mode and revise the plan."},
            ],
            "multiple": False,
            "allow_custom": True,
            "timeout_seconds": None,
        }

    def forward(self) -> Dict[str, Any]:
        context = self._runner_context()
        if context is None:
            return {"approved": False, "plan": "", "plan_file": "", "feedback": "", "error": "exit_plan requires runner context"}

        runner = getattr(context, "runner", None)
        try:
            from juice_agents.core.runner.config import Capability

            owner = self.owner_agent
            managed = runner.agent_manager.get(str(getattr(context, "agent_id", "") or ""))
            trusted_root = (
                owner is not None
                and bool(getattr(managed, "is_root", False))
                and managed.instance is owner
                and str(getattr(managed, "agent_id", "")) == str(getattr(runner, "root_agent_id", ""))
            )
            planning_enabled = bool(runner.has_capability(Capability.PLANNING))
        except (AttributeError, KeyError, TypeError, ValueError):
            trusted_root = False
            planning_enabled = False
        if (
            str(getattr(runner, "mode_id", "") or "") != "plan"
            or not planning_enabled
            or not trusted_root
        ):
            return {"approved": False, "plan": "", "plan_file": "", "feedback": "", "error": "exit_plan requires the trusted root in plan mode"}

        get_plan_file_path = getattr(context, "get_plan_file_path", None)
        plan_file = Path(get_plan_file_path() if callable(get_plan_file_path) else "")
        try:
            expected_plan_file = Path(runner.plan_file_path()).resolve()
        except (AttributeError, TypeError, ValueError):
            expected_plan_file = None
        if expected_plan_file is None or plan_file.resolve() != expected_plan_file:
            return {"approved": False, "plan": "", "plan_file": str(plan_file), "feedback": "", "error": "exit_plan must read the current Runner plans/root.md"}
        if not plan_file.exists() or not plan_file.is_file():
            return {"approved": False, "plan": "", "plan_file": str(plan_file), "feedback": "", "error": f"plan file not found: {plan_file}"}
        plan = plan_file.read_text(encoding="utf-8").strip()
        if not plan:
            return {"approved": False, "plan": "", "plan_file": str(plan_file), "feedback": "", "error": f"plan file is empty: {plan_file}"}

        request = self._approval_request(plan=plan, plan_file=plan_file)
        response = normalize_ask_response(context.ask_user(request), request=request)

        # 安全检查：确保response和selected字段存在
        if not response or not isinstance(response, dict):
            return {"approved": False, "plan": plan, "plan_file": str(plan_file), "feedback": "", "error": "Invalid response from ask_user"}

        selected = response.get("selected")
        if not isinstance(selected, list):
            return {"approved": False, "plan": plan, "plan_file": str(plan_file), "feedback": "No valid selection", "error": ""}

        selected_values = {
            str(item.get("value") or item.get("label") or "").strip().lower()
            for item in selected
            if isinstance(item, dict)
        }
        approved = response.get("status") == "answered" and bool(selected_values & {"approve", "approved", "yes"})
        feedback = str(response.get("custom_response") or response.get("error") or "").strip()
        if not approved:
            return {
                "approved": False,
                "plan": plan,
                "plan_file": str(plan_file),
                "feedback": feedback,
                "error": "" if response.get("status") == "answered" else str(response.get("error") or response.get("status") or ""),
            }

        recorder = getattr(context, "record_approved_plan_exit", None)
        if callable(recorder):
            recorder(
                agent_name=str(getattr(context, "agent_name", "") or ""),
                plan=plan,
                plan_file=str(plan_file),
            )
        return {"approved": True, "plan": plan, "plan_file": str(plan_file), "feedback": feedback, "error": ""}

    def should_terminal(self, result: Any) -> bool:
        return bool(isinstance(result, dict) and result.get("approved"))


__all__ = ["TodosTool", "PlanTool", "ExitPlanTool"]
