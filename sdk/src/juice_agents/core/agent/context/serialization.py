"""ContextTree 序列化/反序列化。"""

from __future__ import annotations

from typing import Any

from ..agent_type import ObservationImage
from .errors import AgentError
from .nodes import ContextNode, ExplorationPath, NodeStatus
from .steps import ActionStep, SummaryStep, TaskStep
from .tree import ContextTree

def _serialize_image(value: Any) -> dict[str, Any]:
    if isinstance(value, ObservationImage):
        return {
            "image_url": value.ensure_image_url(),
            "description": str(value.description or ""),
        }
    return {
        "image_url": str(value or ""),
        "description": "",
    }


def _deserialize_image(payload: dict[str, Any]) -> Any:
    image_url = str(payload.get("image_url") or "").strip()
    description = str(payload.get("description") or "").strip()
    if not image_url:
        return ""
    try:
        return ObservationImage(image_url=image_url, description=description)
    except Exception:  # pragma: no cover - 运行时保护
        return image_url


def _serialize_step(step: Any) -> dict[str, Any]:
    """序列化单个 step"""
    if isinstance(step, TaskStep):
        return {
            "type": "task",
            "task": step.task,
            "round_id": step.round_id,
            "task_images": [_serialize_image(item) for item in list(step.task_images or [])],
            "attachments": [dict(item) for item in list(step.attachments or [])],
        }
    elif isinstance(step, ActionStep):
        return {
            "type": "action",
            "step_num": int(step.step_num),
            "round_id": step.round_id,
            "step_id": step.step_id,
            "model_output": step.model_output,
            "thought": step.thought,
            "tool_calls": [dict(item) for item in list(step.tool_calls or []) if isinstance(item, dict)],
            "code_action": step.code_action,
            "reasoning_content": step.reasoning_content,
            "observations": list(step.observations or []),
            "observation_images": [_serialize_image(item) for item in list(step.observations_images or [])],
            "attachments": [dict(item) for item in list(step.attachments or [])],
            "error": "" if step.error is None else str(step.error),
            "observation_limits": list(step.observation_limits or []),
            "request_bytes": int(step.request_bytes or 0),
            "usage": None if step.usage is None else dict(step.usage),
            "round_outcome": step.round_outcome,
            "output": step.output,
        }
    elif isinstance(step, SummaryStep):
        return {"type": "summary", "content": step.content}
    else:
        return {"type": "unknown"}


def _deserialize_step(raw: dict[str, Any]) -> Any:
    """反序列化单个 step"""
    step_type = str(raw.get("type") or "").strip()

    if step_type == "task":
        return TaskStep(
            task=str(raw.get("task") or ""),
            round_id=str(raw.get("round_id") or ""),
            task_images=[
                _deserialize_image(dict(item))
                for item in list(raw.get("task_images") or [])
                if isinstance(item, dict)
            ],
            attachments=[dict(item) for item in list(raw.get("attachments") or []) if isinstance(item, dict)],
        )

    elif step_type == "action":
        error_text = str(raw.get("error") or "").strip()
        model_output = str(raw.get("model_output") or "")
        thought = str(raw.get("thought") or "")
        code_action = str(raw.get("code_action") or "")
        tool_calls = [dict(item) for item in raw.get("tool_calls", []) if isinstance(item, dict)]
        return ActionStep(
            step_num=int(raw.get("step_num") or 0),
            round_id=str(raw.get("round_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            model_output=model_output,
            thought=thought,
            tool_calls=tool_calls,
            code_action=code_action,
            reasoning_content=str(raw.get("reasoning_content") or ""),
            observations=list(raw.get("observations") or []),
            observations_images=[
                _deserialize_image(dict(item))
                for item in list(raw.get("observation_images") or [])
                if isinstance(item, dict)
            ],
            attachments=[dict(item) for item in list(raw.get("attachments") or []) if isinstance(item, dict)],
            error=AgentError(error_text) if error_text else None,
            observation_limits=list(raw.get("observation_limits") or []),
            request_bytes=int(raw.get("request_bytes") or 0),
            usage=dict(raw.get("usage")) if isinstance(raw.get("usage"), dict) else None,
            round_outcome=str(raw.get("round_outcome") or "continue"),
            output=raw.get("output"),
        )

    elif step_type == "summary":
        return SummaryStep(content=str(raw.get("content") or ""))

    return None


def serialize_context_node(node: ContextNode) -> dict[str, Any]:
    """序列化 ContextNode"""
    return {
        "node_id": node.node_id,
        "parent_id": node.parent_id,
        "children_ids": list(node.children_ids),
        "step": _serialize_step(node.step),
        "depth": node.depth,
        "status": node.status.value,
        "score": node.score,
        "generated_by": node.generated_by,
        "generation_strategy": node.generation_strategy,
        "generation_params": dict(node.generation_params),
        "annotations": dict(node.annotations),
    }


def deserialize_context_node(data: dict[str, Any]) -> ContextNode:
    """反序列化 ContextNode"""
    return ContextNode(
        node_id=str(data.get("node_id") or ""),
        parent_id=data.get("parent_id"),
        children_ids=list(data.get("children_ids") or []),
        step=_deserialize_step(data.get("step") or {}),
        depth=int(data.get("depth") or 0),
        status=NodeStatus(data.get("status", "pending")),
        score=data.get("score"),
        generated_by=str(data.get("generated_by") or ""),
        generation_strategy=str(data.get("generation_strategy") or ""),
        generation_params=dict(data.get("generation_params") or {}),
        annotations=dict(data.get("annotations") or {}),
    )


def serialize_exploration_path(path: ExplorationPath) -> dict[str, Any]:
    """序列化 ExplorationPath"""
    return {
        "path_id": path.path_id,
        "node_ids": list(path.node_ids),
        "total_score": path.total_score,
        "success": path.success,
        "rank": path.rank,
        "metadata": dict(path.metadata),
    }


def deserialize_exploration_path(data: dict[str, Any]) -> ExplorationPath:
    """反序列化 ExplorationPath"""
    return ExplorationPath(
        path_id=str(data.get("path_id") or ""),
        node_ids=list(data.get("node_ids") or []),
        total_score=float(data.get("total_score") or 0.0),
        success=bool(data.get("success")),
        rank=data.get("rank"),
        metadata=dict(data.get("metadata") or {}),
    )


def serialize_context_transcript(tree: ContextTree) -> list[dict[str, Any]]:
    """导出当前选中分支的完整对话，供历史显示与会话标题共用。

    ``to_linear_steps()`` 服务于模型上下文，会在压缩摘要处裁掉旧步骤；
    历史查看则需要保留这些祖先节点。沿选中叶子的父链读取，既保留压缩前
    的对话，也不会把其它候选分支中未执行的回答混入历史。该操作只读。
    """

    if tree.active_leaf_ids:
        node_ids = tree.get_path_to_node(tree.active_leaf_ids[0])
    else:
        path = tree.get_best_path()
        node_ids = path.node_ids if path else ([tree.root_id] if tree.root_id else [])
    return [_serialize_step(tree.nodes[node_id].step) for node_id in node_ids]


def serialize_context_tree(tree: ContextTree) -> dict[str, Any]:
    """序列化 ContextTree"""
    return {
        "version": "3.0",
        "tree_id": tree.tree_id,
        "system_prompt": tree.system_prompt,
        "system_prompt_static": tree.system_prompt_static,
        "system_prompt_dynamic": tree.system_prompt_dynamic,
        "include_reasoning_in_context": tree.include_reasoning_in_context,
        "root_id": tree.root_id,
        "nodes": {
            node_id: serialize_context_node(node)
            for node_id, node in tree.nodes.items()
        },
        "all_paths": [serialize_exploration_path(p) for p in tree.all_paths],
        "active_leaf_ids": list(tree.active_leaf_ids),
        "selected_path_id": tree.selected_path_id,
        # 注意：不序列化 strategy，策略是 Agent 级别的配置
        "task_metadata": dict(tree.task_metadata),
        "stats": dict(tree.stats),
    }


def deserialize_context_tree(payload: dict[str, Any]) -> ContextTree:
    """反序列化 ContextTree"""
    version = payload.get("version")

    # 只支持新版本格式
    if version != "3.0":
        raise ValueError(f"不支持的序列化版本: {version}；旧会话保留在磁盘，请新建会话（当前格式 3.0）")

    # 新版本直接反序列化
    tree = ContextTree(
        tree_id=str(payload.get("tree_id") or ""),
        system_prompt=str(payload.get("system_prompt") or ""),
        system_prompt_static=payload.get("system_prompt_static"),
        system_prompt_dynamic=payload.get("system_prompt_dynamic"),
        include_reasoning_in_context=bool(payload.get("include_reasoning_in_context")),
        root_id=str(payload.get("root_id") or ""),
        # 注意：不反序列化 strategy，策略由 Agent 管理
        task_metadata=dict(payload.get("task_metadata") or {}),
        stats=dict(payload.get("stats") or {}),
    )

    # 恢复节点
    nodes_data = payload.get("nodes", {})
    for node_id, node_data in nodes_data.items():
        node = deserialize_context_node(node_data)
        tree.nodes[node_id] = node

    # 恢复路径
    for path_data in payload.get("all_paths", []):
        path = deserialize_exploration_path(path_data)
        tree.all_paths.append(path)

    tree.active_leaf_ids = list(payload.get("active_leaf_ids") or [])
    tree.selected_path_id = payload.get("selected_path_id")

    return tree


__all__ = [
    "serialize_context_transcript",
    "serialize_context_tree",
    "deserialize_context_tree",
    "serialize_context_node",
    "deserialize_context_node",
    "serialize_exploration_path",
    "deserialize_exploration_path",
]
