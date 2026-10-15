"""贪心策略 - 每步生成一个确定性的 action。"""

from __future__ import annotations

import logging
from typing import Any, List

from ..nodes import ContextNode, NodeStatus
from ..steps import ActionStep
from ..tree import ContextTree

logger = logging.getLogger(__name__)


class GreedyStrategy:
    """贪心策略（默认策略，等价于现有线性行为）

    特点：
    - 每步只生成一个 action
    - temperature=0 (确定性)
    - 不进行多候选采样
    """

    def __init__(self, temperature: float = 0.0):
        self.temperature = temperature

    def rollout(
        self,
        tree: ContextTree,
        current_node_id: str,
        model: Any,
        **kwargs
    ) -> List[str]:
        """贪心 rollout：生成一个确定性的 action

        Args:
            tree: 上下文树
            current_node_id: 当前节点ID
            model: 聊天模型
            **kwargs: 其他参数

        Returns:
            新创建的节点 ID 列表（只有一个）
        """
        # 获取当前上下文
        context_messages = tree.get_context_messages(current_node_id)

        # 生成单个 action（确定性）
        response = model.generate(
            messages=context_messages,
            temperature=self.temperature,
            **kwargs
        )

        # 创建新节点
        current_node = tree.nodes[current_node_id]
        new_node_id = f"{current_node_id}_next"

        # 这里需要将 response 转换为 ActionStep
        # 实际实现需要与 Agent 的 step 逻辑集成
        # 暂时创建一个简单的占位符
        action_step = self._response_to_action_step(response, current_node.depth + 1)

        new_node = ContextNode(
            node_id=new_node_id,
            parent_id=current_node_id,
            step=action_step,
            depth=current_node.depth + 1,
            status=NodeStatus.ACTIVE,
            generation_strategy="greedy",
            generation_params={"temperature": self.temperature}
        )

        tree.add_node(new_node, parent_id=current_node_id)
        tree.active_leaf_ids = [new_node_id]

        logger.debug(f"贪心策略创建节点: {new_node_id}")

        return [new_node_id]

    def _response_to_action_step(self, response: dict, step_num: int) -> ActionStep:
        """将模型响应转换为 ActionStep

        注：实际实现需要与 MultiStepAgent 的逻辑集成
        这里提供一个简化版本
        """
        content = response.get("content", "")
        usage = response.get("usage")

        return ActionStep(
            step_num=step_num,
            model_output=content,
            tool_calls=[],
            observations=[],
            usage=usage,
            round_outcome="continue"
        )


__all__ = ["GreedyStrategy"]
