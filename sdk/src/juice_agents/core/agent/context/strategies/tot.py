"""Tree of Thoughts 策略（统一实现 ToT、Best-of-N、Beam Search）。"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from ..nodes import ContextNode, NodeStatus
from ..steps import ActionStep
from ..tree import ContextTree

logger = logging.getLogger(__name__)


class TreeOfThoughtsStrategy:
    """Tree of Thoughts 策略

    统一实现：
    - Best-of-N = ToT(depth=1, keep_top_k=1, breadth=n)
    - Beam Search = ToT(keep_top_k=beam_width)
    - Standard ToT = 多层多分支探索

    参数：
    - breadth: 每步生成多少个候选
    - depth_limit: 最大深度
    - keep_top_k: 每步保留多少个（k=1 就是 Best-of-N）
    - temperature: 采样温度
    - reward_model: 可选的奖励模型用于评估
    """

    def __init__(
        self,
        breadth: int = 3,
        depth_limit: int = 5,
        keep_top_k: int = 1,
        temperature: float = 0.7,
        reward_model: Optional[Any] = None
    ):
        self.breadth = breadth
        self.depth_limit = depth_limit
        self.keep_top_k = keep_top_k
        self.temperature = temperature
        self.reward_model = reward_model

    def rollout(
        self,
        tree: ContextTree,
        current_node_id: str,
        model: Any,
        **kwargs
    ) -> List[str]:
        """ToT rollout：生成多个候选，评估，选择 top-k

        Args:
            tree: 上下文树
            current_node_id: 当前节点ID
            model: 聊天模型
            **kwargs: 其他参数

        Returns:
            新创建并选中的节点 ID 列表（最多 keep_top_k 个）
        """
        current_node = tree.nodes[current_node_id]

        # 检查深度限制
        if current_node.depth >= self.depth_limit:
            logger.info(f"达到深度限制 {self.depth_limit}，停止探索")
            return []

        # 获取上下文
        context_messages = tree.get_context_messages(current_node_id)

        # Generate 阶段：生成 breadth 个候选
        logger.info(f"ToT 生成 {self.breadth} 个候选 (depth={current_node.depth})")

        thought_nodes = []
        for i in range(self.breadth):
            # 使用 generate_batch 或多次调用 generate
            if hasattr(model, 'generate_batch'):
                # 批量生成（更高效）
                responses = model.generate_batch(
                    messages=context_messages,
                    n=self.breadth,
                    temperature=self.temperature,
                    **kwargs
                )
                break  # 只需一次批量调用
            else:
                # 单次生成
                response = model.generate(
                    messages=context_messages,
                    temperature=self.temperature,
                    **kwargs
                )
                responses = [response]

        # 如果没有 batch，responses 只有一个，需要循环生成
        if not hasattr(model, 'generate_batch'):
            responses = []
            for i in range(self.breadth):
                response = model.generate(
                    messages=context_messages,
                    temperature=self.temperature,
                    **kwargs
                )
                responses.append(response)

        # 创建节点
        for i, response in enumerate(responses[:self.breadth]):
            thought_id = f"{current_node_id}_thought_{i}"
            action_step = self._response_to_action_step(response, current_node.depth + 1)

            thought_node = ContextNode(
                node_id=thought_id,
                parent_id=current_node_id,
                step=action_step,
                depth=current_node.depth + 1,
                status=NodeStatus.PENDING,
                generation_strategy="tot",
                generation_params={
                    "breadth": self.breadth,
                    "keep_top_k": self.keep_top_k,
                    "thought_index": i,
                    "temperature": self.temperature
                }
            )

            tree.add_node(thought_node, parent_id=current_node_id)
            thought_nodes.append(thought_node)

        # Evaluate 阶段：评估所有 thoughts
        logger.info(f"评估 {len(thought_nodes)} 个候选")

        if self.reward_model:
            # 使用 RewardModel 评估
            scores = self.reward_model.evaluate_batch(
                context=context_messages,
                actions=[self._node_to_action(n) for n in thought_nodes],
                **kwargs
            )
            for node, score in zip(thought_nodes, scores):
                node.score = score
        else:
            # 使用启发式：简单的随机评分或固定分数
            import random
            for node in thought_nodes:
                node.score = random.random()

        # Select 阶段：选择 top-k
        sorted_thoughts = sorted(
            thought_nodes,
            key=lambda n: n.score or 0.0,
            reverse=True
        )

        selected = sorted_thoughts[:self.keep_top_k]
        rejected = sorted_thoughts[self.keep_top_k:]

        # 标记状态
        for node in selected:
            node.status = NodeStatus.ACTIVE
        for node in rejected:
            node.status = NodeStatus.REJECTED

        # 更新父节点的 children
        tree.nodes[current_node_id].children_ids = [n.node_id for n in selected]

        # 更新活跃叶子
        tree.active_leaf_ids = [n.node_id for n in selected]

        logger.info(f"选择 top-{self.keep_top_k}: {[n.node_id for n in selected]}")

        return [n.node_id for n in selected]

    def _response_to_action_step(self, response: dict, step_num: int) -> ActionStep:
        """将模型响应转换为 ActionStep"""
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

    def _node_to_action(self, node: ContextNode) -> dict:
        """将节点转换为 action dict（用于 RewardModel）"""
        return {
            "role": "assistant",
            "content": getattr(node.step, "model_output", "")
        }


__all__ = [
    "TreeOfThoughtsStrategy",
]
