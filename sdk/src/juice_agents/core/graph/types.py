"""
Graph 运行时类型：Command / Send / Config / Errors。

尽量对齐 LangGraph 的核心语义，但实现保持轻量、无外部依赖。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol

from juice_agents.core.config.context import ConfigurationContext
from .constants import END

logger = logging.getLogger(__name__)


class GraphError(Exception):
    """Graph 相关异常基类。"""


class InvalidConcurrentGraphUpdate(GraphError):
    """
    同一 superstep 内多个并发节点更新同一 state key，但该 key 未声明 reducer。
    """


class GraphIncompleteError(GraphError):
    """Graph 在完成前命中运行限制。"""


class GraphCancelledError(GraphError):
    """Graph 被调用方协作取消。"""


class GraphPausedError(GraphError):
    """Graph 在 superstep 边界暂停，checkpoint 已由调用方保存。"""


@dataclass(frozen=True)
class Send:
    """
    动态路由/并行分发原语。

    - node: 目标节点
    - arg: 发送给目标节点的输入 payload（通常是局部 state patch）
    """

    node: str
    arg: Any = None


@dataclass(frozen=True)
class Command:
    """
    控制流原语：同时表达 state 更新与下一跳 goto。

    goto 可以是：
    - 节点名（str）
    - Send
    - List[Send]
    - END
    """

    update: Optional[Dict[str, Any]] = None
    goto: Any = None

    def __post_init__(self) -> None:
        if self.goto is None:
            return
        if self.goto == END:
            return
        if isinstance(self.goto, str):
            return
        if isinstance(self.goto, Send):
            return
        if isinstance(self.goto, list) and all(isinstance(x, Send) for x in self.goto):
            return
        raise TypeError("Command.goto 类型无效，应为 str/Send/List[Send]/END/None")


@dataclass
class GraphConfig:
    """
    运行时配置。

    - max_steps: 最大 superstep 数
    - max_workers: 并行线程数
    - allow_sleep: 预留开关（当前无延时调度，保持兼容）
    - debug: 是否输出更详细日志
    - workspace_dir: 可选工作目录；提供时 graph 相关运行文件可收敛到该目录
    - max_scheduled_tasks: 单次运行允许调度的最大节点任务数
    - timeout_seconds: 单次运行总超时；None 表示不限制
    - cancel_event / pause_event: 在 superstep 边界协作停止
    - configurable: 自定义配置，透传给 node 函数（可选）
    """

    max_steps: int = 100
    max_workers: int = 8
    allow_sleep: bool = False
    debug: bool = False
    workspace_dir: str | None = None
    max_scheduled_tasks: int = 1000
    timeout_seconds: float | None = None
    cancel_event: threading.Event | None = None
    pause_event: threading.Event | None = None
    configurable: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_any(cls, config: Any) -> "GraphConfig":
        if config is None:
            return cls()
        if isinstance(config, cls):
            return config
        if isinstance(config, dict):
            known: Dict[str, Any] = {}
            for k in [
                "max_steps",
                "max_workers",
                "allow_sleep",
                "debug",
                "workspace_dir",
                "max_scheduled_tasks",
                "timeout_seconds",
                "cancel_event",
                "pause_event",
                "configurable",
            ]:
                if k in config:
                    known[k] = config[k]
            cfg = cls(**known)
            # 兼容一些常见写法：直接把自定义字段也放在 config 里
            for k, v in config.items():
                if k not in known and k not in cfg.configurable:
                    cfg.configurable[k] = v
            return cfg
        raise TypeError("config 必须为 dict/GraphConfig/None")

    def validate(self) -> None:
        if not isinstance(self.max_steps, int) or self.max_steps <= 0:
            raise ValueError("max_steps 必须为正整数")
        if not isinstance(self.max_workers, int) or self.max_workers <= 0:
            raise ValueError("max_workers 必须为正整数")
        if not isinstance(self.allow_sleep, bool):
            raise TypeError("allow_sleep 必须为 bool")
        if not isinstance(self.debug, bool):
            raise TypeError("debug 必须为 bool")
        if self.workspace_dir is not None:
            if not isinstance(self.workspace_dir, str):
                raise TypeError("workspace_dir 必须为 string 或 None")
            if not self.workspace_dir.strip():
                raise ValueError("workspace_dir 不能为空字符串")
        if not isinstance(self.max_scheduled_tasks, int) or self.max_scheduled_tasks <= 0:
            raise ValueError("max_scheduled_tasks 必须为正整数")
        if self.timeout_seconds is not None:
            if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
                raise ValueError("timeout_seconds 必须为正数或 None")
        if self.cancel_event is not None and not hasattr(self.cancel_event, "is_set"):
            raise TypeError("cancel_event 必须提供 is_set()")
        if self.pause_event is not None and not hasattr(self.pause_event, "is_set"):
            raise TypeError("pause_event 必须提供 is_set()")
        if not isinstance(self.configurable, dict):
            raise TypeError("configurable 必须为 dict")


class GraphAgentDispatcher(Protocol):
    """Narrow Agent capability available to one graph runtime.

    A graph may request a fresh managed Agent for a structured subtask, but it
    must not receive or retain the full Runner object.  Runner composition
    supplies this bridge and delegates the actual work to AgentManager.
    """

    @property
    def runner_id(self) -> str: ...

    @property
    def root_agent_name(self) -> str: ...

    def invoke_agent(self, spec: dict[str, Any]) -> dict[str, Any]: ...

    def agent_type_for(self, agent_name: str) -> str | None: ...


@dataclass(slots=True)
class GraphBuildContext:
    """Runtime services explicitly available to one graph build."""

    config_context: ConfigurationContext
    workspace_dir: Path | None
    runner_id: str = ""
    graph_run_id: str = ""
    artifacts_dir: Path | None = None
    model: Any | None = None
    model_name: str | None = None
    model_effort: str | None = None
    agent_dispatcher: GraphAgentDispatcher | None = None
    owner_agent_name: str = ""
    configurable: dict[str, Any] = field(default_factory=dict)
    callbacks: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CompiledPayloadGraph:
    """Adapt a native CompiledGraph state contract to a business payload."""

    name: str
    compiled_graph: Any
    payload_to_state: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]
    state_to_result: Callable[[dict[str, Any]], Any]

    def invoke(self, payload: dict[str, Any], config: dict[str, Any] | None = None) -> Any:
        initial_state = self.payload_to_state(dict(payload or {}), None if config is None else dict(config))
        final_state = self.compiled_graph.invoke(initial_state, config=config)
        return self.state_to_result(dict(final_state or {}))

    def stream(self, payload: dict[str, Any], config: dict[str, Any] | None = None):
        initial_state = self.payload_to_state(dict(payload or {}), None if config is None else dict(config))
        yield from self.compiled_graph.stream(initial_state, config=config)

    def result_from_state(self, state: dict[str, Any]) -> Any:
        return self.state_to_result(dict(state or {}))

    def to_mermaid(self) -> str:
        return self.compiled_graph.to_mermaid()


__all__ = [
    "GraphError",
    "GraphCancelledError",
    "GraphIncompleteError",
    "GraphPausedError",
    "InvalidConcurrentGraphUpdate",
    "Send",
    "Command",
    "GraphConfig",
    "GraphAgentDispatcher",
    "GraphBuildContext",
    "CompiledPayloadGraph",
]
