"""
会话模块导出入口。

保留原 `juice_agents.core.agent.sessions` 导入路径，同时把实现拆分到更小的模块，
便于独立维护 steps / session / compression / serialization 边界。
"""

from .serialization import deserialize_session, serialize_session
from .compression import CompactCompressStrategy, CompressStrategy, NoCompressStrategy, RemainCompressStrategy
from .content import DEFAULT_COMPACT_CONTINUATION_TEMPLATE, DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER
from .content import normalize_observations, observations_to_text
from .errors import (
    ActionValidationError,
    AgentError,
    ContextWindowExceededError,
    ModelOutputProtocolError,
    SubmitOutputValidationError,
    ToolResolutionError,
    is_recoverable_agent_error,
)
from .session import AgentSession, CompressibleAgentSession
from .steps import ActionStep, SessionStep, SummaryStep, TaskStep

__all__ = [
    "AgentSession",
    "CompressibleAgentSession",
    "CompressStrategy",
    "NoCompressStrategy",
    "RemainCompressStrategy",
    "CompactCompressStrategy",
    "SessionStep",
    "SummaryStep",
    "TaskStep",
    "ActionStep",
    "AgentError",
    "ModelOutputProtocolError",
    "ActionValidationError",
    "ToolResolutionError",
    "SubmitOutputValidationError",
    "ContextWindowExceededError",
    "is_recoverable_agent_error",
    "DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER",
    "DEFAULT_COMPACT_CONTINUATION_TEMPLATE",
    "normalize_observations",
    "observations_to_text",
    "serialize_session",
    "deserialize_session",
]
