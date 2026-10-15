"""Agent 错误类型定义。"""

from __future__ import annotations


class AgentError(Exception):
    """Agent 执行错误基类"""
    pass


class ActionValidationError(AgentError):
    """Action 验证错误"""
    pass


class ContextWindowExceededError(AgentError):
    """上下文窗口超限错误"""
    pass


class ModelOutputProtocolError(AgentError):
    """模型输出协议错误"""
    pass


class SubmitOutputValidationError(AgentError):
    """输出提交验证错误"""
    pass


class ToolResolutionError(AgentError):
    """工具解析错误"""
    pass


def is_recoverable_agent_error(error: Exception) -> bool:
    """判断错误是否可恢复"""
    # 可恢复的错误类型
    recoverable_types = (
        ActionValidationError,
        ToolResolutionError,
        ModelOutputProtocolError,
    )
    return isinstance(error, recoverable_types)


__all__ = [
    "AgentError",
    "ActionValidationError",
    "ContextWindowExceededError",
    "ModelOutputProtocolError",
    "SubmitOutputValidationError",
    "ToolResolutionError",
    "is_recoverable_agent_error",
]
