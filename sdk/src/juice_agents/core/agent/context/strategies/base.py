"""探索策略基类和接口定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Protocol

from ..tree import ContextTree


class ExplorationStrategy(Protocol):
    """探索策略接口

    负责：
    1. 如何从当前节点探索（单次/多次/并行）
    2. 如何控制模型采样参数
    3. 如何评估和选择路径
    """

    def rollout(
        self,
        tree: ContextTree,
        current_node_id: str,
        model: Any,
        *,
        temperature: float = 1.0,
        top_p: float = 1.0,
        **kwargs
    ) -> List[str]:
        """执行一次 rollout，返回新创建的子节点 IDs

        Args:
            tree: 上下文树
            current_node_id: 当前节点ID
            model: 聊天模型（实现 generate 接口）
            temperature: 采样温度
            top_p: nucleus sampling 参数
            **kwargs: 其他参数

        Returns:
            新创建的子节点 ID 列表
        """
        ...


__all__ = ["ExplorationStrategy"]
