"""Graph 的只读发现、执行与运行控制工具。

list/view 是调用面的发现入口（找到 graph → `graph_tool` 执行），受
`graphs.enabled` 约束；写入侧 `graph_manage` 在 `builtin/evolution/graph_manage.py`，
它继承这里的 `GraphToolBase`，并额外受 `self_evolution.enabled` 约束。

Graph 不进入 Agent 能力集（`graph_tool` 每次按名从 registry fresh 读取），
所以没有绑定态可解，既不需要 load/unload，也不走 reconciler 的 step 边界刷新。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.graph.runs import GraphRunStore, GraphRunManager
from juice_agents.core.registry.agents.types import normalize_agent_type
from juice_agents.core.registry.graphs import GraphRegistry

from ...runtime.base_tools import ORCHESTRATION_OBSERVATION_CHARS, Tool
from ...runtime.owner_context import resolve_owner_context

logger = logging.getLogger(__name__)


def _agent_type_from_agent(agent: Any) -> str:
    resolved_type = getattr(agent, "_resolved_agent_type", None)
    if resolved_type:
        return normalize_agent_type(resolved_type)
    declared = getattr(agent, "_declared_agent_config", None)
    declared_type = getattr(declared, "agent_type", None)
    if declared_type and declared_type != "default":
        return normalize_agent_type(declared_type)
    class_name = type(agent).__name__.lower()
    return "codeact" if "codeact" in class_name else "react"


class GraphToolBase(Tool):
    max_observation_chars = ORCHESTRATION_OBSERVATION_CHARS

    def __init__(
        self,
        *,
        graph_registry: GraphRegistry | None = None,
        graph_factory: GraphRegistry | None = None,
    ) -> None:
        super().__init__()
        self.owner_agent: Any | None = None
        self._explicit_registry = graph_registry or graph_factory

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _config_context(self) -> ConfigurationContext:
        return resolve_owner_context(self.owner_agent, explicit_workspace_dir=None)

    def _registry(self) -> GraphRegistry:
        return self._explicit_registry or GraphRegistry(config_context=self._config_context())

    def _runner_root(self) -> Path:
        runner_context = getattr(self.owner_agent, "runner_context", None)
        layout = getattr(runner_context, "layout", None)
        root_dir = getattr(layout, "root_dir", None)
        if root_dir is not None:
            return Path(root_dir)
        return self._config_context().juice_root / "runners" / "standalone"

    def _runtime(self) -> GraphRunManager:
        runner_context = getattr(self.owner_agent, "runner_context", None)
        runner = getattr(runner_context, "runner", None)
        runtime = getattr(runner, "graph_manager", None)
        if not isinstance(runtime, GraphRunManager):
            raise RuntimeError("graph_tool 必须绑定 Runner 的 GraphRunManager")
        return runtime

    def _with_default_agent_type(self, config: dict[str, Any] | None) -> dict[str, Any]:
        next_config = dict(config or {})
        configurable = dict(next_config.get("configurable") or {})
        configurable.setdefault("agent_type", _agent_type_from_agent(self.owner_agent))
        next_config["configurable"] = configurable
        return next_config


class GraphListTool(GraphToolBase):
    _execution_mode = "parallel_safe"
    name = "graph_list"
    is_read_only = True
    description = "列出 builtin 和 workspace-local graphs"
    inputs = {}
    outputs = {"graphs": {"type": "list", "description": "graph 元数据列表"}}

    def forward(self) -> dict[str, Any]:
        items = [item.to_dict() for item in self._registry().list_metadata()]
        return {"success": True, "graphs": items, "count": len(items)}


class GraphViewTool(GraphToolBase):
    _execution_mode = "parallel_safe"
    name = "graph_view"
    is_read_only = True
    description = "查看 graph metadata 和单文件源码"
    inputs = {"name": {"type": "string", "description": "graph 名称"}}
    outputs = {"content": {"type": "string", "description": "graph 源码"}}

    def forward(self, name: str) -> dict[str, Any]:
        try:
            return self._registry().view(name)
        except Exception as exc:
            return {"success": False, "error": str(exc), "name": str(name or "")}


class GraphTool(GraphToolBase):
    """Execute a fresh graph synchronously or through Runner async tasks."""

    def execution_policy(self, args: dict[str, Any], context: Any) -> Any:
        del context
        from ...runtime.executor import ToolExecutionMode, ToolExecutionPolicy

        background = bool(args.get("background"))
        mode = ToolExecutionMode.BACKGROUND if background else ToolExecutionMode.BARRIER
        return ToolExecutionPolicy(
            mode=mode,
            thread_affinity="any" if background else "main",
        )

    name = "graph_tool"
    description = "执行 graph；所有运行均写入持久化 GraphRunStore"
    inputs = {
        "name": {"type": "string", "description": "graph 名称"},
        "payload": {"type": "object", "description": "graph 业务输入"},
        "config": {"type": "object", "description": "可选运行配置", "required": False},
        "background": {"type": "boolean", "description": "是否后台运行", "required": False},
    }
    outputs = {
        "status": {"type": "string", "description": "completed/launched/failed/paused/stopped"},
        "kind": {"type": "string", "description": "固定 local_graph"},
        "graph_run_id": {"type": "string", "description": "持久化 graph run id"},
        "result": {"type": "object", "description": "同步结果"},
        "run_dir": {"type": "string", "description": "GraphRun 调试目录"},
        "events_path": {"type": "string", "description": "Graph 节点事件 JSONL"},
        "source_path": {"type": "string", "description": "本次运行的 graph 源码快照"},
        "artifacts_dir": {"type": "string", "description": "Graph artifacts 目录"},
        "error": {"type": "string", "description": "错误"},
    }

    @staticmethod
    def _debug_paths(store: GraphRunStore, graph_run_id: str) -> dict[str, str]:
        run_dir = store.run_dir(graph_run_id)
        return {
            "run_dir": str(run_dir),
            "events_path": str(run_dir / "events.jsonl"),
            "source_path": str(run_dir / "source.py"),
            "artifacts_dir": str(run_dir / "artifacts"),
        }

    def _failed(self, *, graph_name: str, error: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "kind": "local_graph",
            "async_task_id": "",
            "graph_run_id": "",
            "graph_name": graph_name,
            "summary": f"graph 执行失败：{error}",
            "result": {},
            "output_path": "",
            "run_dir": "",
            "events_path": "",
            "source_path": "",
            "artifacts_dir": "",
            "error": error,
        }

    def forward(
        self,
        name: str,
        payload: dict[str, Any],
        config: dict[str, Any] | None = None,
        background: bool = False,
    ) -> dict[str, Any]:
        graph_name = str(name or "").strip()
        if not graph_name:
            return self._failed(graph_name="", error="name 不能为空")
        if not isinstance(payload, dict):
            return self._failed(graph_name=graph_name, error="payload 必须为 object")
        if config is not None and not isinstance(config, dict):
            return self._failed(graph_name=graph_name, error="config 必须为 object 或 None")
        try:
            if background:
                runner_context = getattr(self.owner_agent, "runner_context", None)
                if runner_context is None:
                    raise ValueError("graph_tool(background=True) 需要绑定 Runner")
                receipt = runner_context.launch_local_graph(
                    graph_name=graph_name,
                    payload=dict(payload),
                    config=self._with_default_agent_type(config),
                    max_observation_chars=self.current_max_observation_chars,
                )
                receipt["graph_name"] = graph_name
                return dict(receipt)
            runtime = self._runtime()
            manifest = runtime.run(
                graph_name,
                dict(payload),
                self._with_default_agent_type(config),
                owner_agent_name=str(getattr(self.owner_agent, "name", "") or ""),
            )
            run_id = str(manifest["graph_run_id"])
            result_path = runtime.store.run_dir(run_id) / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            debug_paths = self._debug_paths(runtime.store, run_id)
            return {
                "status": str(manifest.get("status") or "failed"),
                "kind": "local_graph",
                "async_task_id": "",
                "graph_run_id": run_id,
                "graph_name": graph_name,
                "summary": f"graph run {manifest.get('status')}: {graph_name}",
                "result": result,
                "output_path": str(result_path),
                **debug_paths,
                "error": str(manifest.get("error") or ""),
            }
        except Exception as exc:
            logger.exception("graph_tool 调用失败: graph=%s", graph_name)
            return self._failed(graph_name=graph_name, error=str(exc))


__all__ = ["GraphListTool", "GraphTool", "GraphToolBase", "GraphViewTool"]
