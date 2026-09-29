"""Runner 包导出入口。"""

from .execution.context import RunnerContext
from .execution.cancellation import StreamCancelled, raise_if_cancelled
from .errors import RunnerBusyError
from .config import (
    AgentBinding,
    Capability,
    ContinuationPolicy,
    ModeRegistry,
    RunnerConfig,
    TeamRunConfig,
    ToolPolicy,
    mode_registry,
    register_mode,
)
from .types.definitions import (
    AsyncTaskState,
    AsyncTaskType,
    Attachment,
    AgentMode,
    PermissionMode,
    RunnerState,
    RunnerStreamEvent,
)


def __getattr__(name: str):
    if name == "Runner":
        from .runner import Runner

        return Runner
    raise AttributeError(name)


__all__ = [
    "AsyncTaskState",
    "AsyncTaskType",
    "Attachment",
    "Runner",
    "RunnerContext",
    "RunnerBusyError",
    "StreamCancelled",
    "raise_if_cancelled",
    "AgentMode",
    "AgentBinding",
    "Capability",
    "ContinuationPolicy",
    "ModeRegistry",
    "RunnerConfig",
    "TeamRunConfig",
    "ToolPolicy",
    "PermissionMode",
    "RunnerState",
    "RunnerStreamEvent",
    "mode_registry",
    "register_mode",
]
