"""Execution results and public events shared by SDK and transports.

Instance lifetime and request outcome are deliberately separate. A failed
request leaves a persistent instance idle and a functional instance released.
Only AgentManager commits results; observers merely consume these values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Generic, Literal, TypeVar, TypedDict

RoundStatus = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class RoundResult:
    """The immutable outcome saved with a round's final checkpoint."""

    round_id: str
    status: RoundStatus
    output: Any = None
    reason: str = ""
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.round_id or self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("RoundResult requires a round_id and a terminal status")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RoundResult:
        return cls(**{name: value[name] for name in cls.__dataclass_fields__ if name in value})

    def unwrap(self) -> Any:
        """Return successful output or raise a typed error retaining partial output."""
        if self.status == "cancelled":
            raise RoundCancelledError(self)
        if self.status == "failed":
            raise RoundFailedError(self)
        return self.output


class RoundExecutionError(RuntimeError):
    """Base error for callers that require successful execution."""

    def __init__(self, result: RoundResult) -> None:
        self.result = result
        super().__init__(result.error or result.reason or result.status)


class RoundFailedError(RoundExecutionError):
    """A round finished without submitting a successful result."""


class RoundCancelledError(RoundExecutionError):
    """A round stopped after its cancellation intent was persisted."""


class AgentStateData(TypedDict):
    status: Literal["idle", "running", "released"]
    lifecycle: Literal["persistent", "functional"]
    is_root: bool
    round_checkpoint: dict[str, Any]


class RoundResultData(TypedDict):
    round_id: str
    status: RoundStatus
    output: Any
    reason: str
    error: str | None


class RoundEndData(TypedDict):
    result: RoundResultData


class RoundStartData(TypedDict):
    resumed: bool
    message_ids: list[str]
    input: dict[str, Any]


class MessageReceivedData(TypedDict):
    """Durably queued input; this does not claim the Agent consumed it yet."""

    attachments: list[dict[str, Any]]


class StepStartData(TypedDict):
    step_id: str


class StepEndData(TypedDict):
    step: dict[str, Any]
    message_ids: list[str]


class ModelBoundaryData(TypedDict, total=False):
    model_call_id: str
    purpose: Literal["generation", "scoring", "compression"]
    status: Literal["completed", "failed", "cancelled"]
    error: str | None


class ToolBoundaryData(TypedDict, total=False):
    call_id: str
    tool_name: str
    status: str
    tool_args: dict[str, Any]
    tool_index: int
    execution_mode: str
    observation: Any
    error: str | None


PublicEventKind = Literal[
    "agent_state", "round_start", "round_end", "step_start", "step_end",
    "model_start", "model_end", "thinking", "tool_start", "tool_end",
    "workflow_progress", "team_update", "message_deferred", "message_received",
]


_Kind = TypeVar("_Kind", bound=str)
_Data = TypeVar("_Data")


class _PublicEvent(TypedDict, Generic[_Kind, _Data]):
    """Every public payload has one discriminant and one data object.

    Round and step identities are nullable for instance/business events that
    occur outside execution. Model/tool call identities live in their data.
    """

    kind: _Kind
    data: _Data
    event_id: str
    timestamp: float
    runner_id: str
    agent_id: str
    agent_name: str
    agent_role: str
    round_id: str | None
    step_num: int | None


# Each discriminant constrains its own payload. In particular round_end cannot
# accidentally expose an ActionStep or an untyped string instead of a result.
PublicEvent = (
    _PublicEvent[Literal["agent_state"], AgentStateData]
    | _PublicEvent[Literal["round_start"], RoundStartData]
    | _PublicEvent[Literal["message_received"], MessageReceivedData]
    | _PublicEvent[Literal["round_end"], RoundEndData]
    | _PublicEvent[Literal["step_start"], StepStartData]
    | _PublicEvent[Literal["step_end"], StepEndData]
    | _PublicEvent[Literal["model_start", "model_end"], ModelBoundaryData]
    | _PublicEvent[Literal["tool_start", "tool_end"], ToolBoundaryData]
    | _PublicEvent[Literal["thinking", "workflow_progress", "team_update", "message_deferred"], dict[str, Any]]
)
