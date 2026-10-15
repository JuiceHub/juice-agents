"""Agent 上下文管理模块。

核心概念：
- ContextTree: 上下文探索树
- ContextNode: 树状结构的节点
- ExplorationPath: 探索路径
- Steps: TaskStep, ActionStep, SummaryStep
"""

from .agent_errors import (
    ActionValidationError,
    AgentError,
    ContextWindowExceededError,
    ModelOutputProtocolError,
    SubmitOutputValidationError,
    ToolResolutionError,
    is_recoverable_agent_error,
)
from .compression import (
    CompactCompressStrategy,
    CompressStrategy,
    NoCompressStrategy,
    RemainCompressStrategy,
)
from .content import (
    CACHE_BOUNDARY_KEY,
    CODEACT_OBSERVATION_PROJECTION_CHARS,
    DEFAULT_COMPACT_CONTINUATION_TEMPLATE,
    DEFAULT_OBSERVATION_PROJECTION_CHARS,
    DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER,
    ImageType,
    attachments_content_blocks,
    encode_image,
    image_block,
    message_content_to_transcript_parts,
    normalize_observations,
    observations_to_text,
    system_content_blocks,
    text_block,
    truncate_text,
)
from .nodes import ContextNode, ExplorationPath, NodeStatus
from .serialization import (
    deserialize_context_node,
    deserialize_context_tree,
    deserialize_exploration_path,
    serialize_context_node,
    serialize_context_tree,
    serialize_exploration_path,
)
from .steps import ActionStep, AgentStep, SummaryStep, TaskStep, ActionEvent
from .tree import ContextTree

__all__ = [
    # 核心类
    "ContextTree",
    "ContextNode",
    "ExplorationPath",
    "NodeStatus",
    # Steps
    "AgentStep",
    "TaskStep",
    "ActionStep",
    "SummaryStep",
    "ActionEvent",
    # 序列化
    "serialize_context_tree",
    "deserialize_context_tree",
    "serialize_context_node",
    "deserialize_context_node",
    "serialize_exploration_path",
    "deserialize_exploration_path",
    # 压缩策略
    "CompressStrategy",
    "NoCompressStrategy",
    "RemainCompressStrategy",
    "CompactCompressStrategy",
    # 错误
    "AgentError",
    "ActionValidationError",
    "ContextWindowExceededError",
    "ModelOutputProtocolError",
    "SubmitOutputValidationError",
    "ToolResolutionError",
    "is_recoverable_agent_error",
    # 内容处理
    "ImageType",
    "CACHE_BOUNDARY_KEY",
    "DEFAULT_OBSERVATION_PROJECTION_CHARS",
    "CODEACT_OBSERVATION_PROJECTION_CHARS",
    "DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER",
    "DEFAULT_COMPACT_CONTINUATION_TEMPLATE",
    "normalize_observations",
    "observations_to_text",
    "truncate_text",
    "encode_image",
    "text_block",
    "system_content_blocks",
    "image_block",
    "attachments_content_blocks",
    "message_content_to_transcript_parts",
]
