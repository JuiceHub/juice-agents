"""上下文树的节点和路径数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class NodeStatus(Enum):
    """节点状态"""
    PENDING = "pending"      # 已创建但未执行
    ACTIVE = "active"        # 当前活跃路径上的节点
    SUCCESS = "success"      # 执行成功
    FAILED = "failed"        # 执行失败（有错误）
    REJECTED = "rejected"    # 被拒绝（低价值/未选中/被剪枝）


@dataclass
class ContextNode:
    """上下文树的单个节点

    每个节点代表一个状态：
    - root 节点通常是 TaskStep（初始任务）
    - 其他节点是 ActionStep（执行的动作）
    """

    # === 节点标识和结构 ===
    node_id: str                          # 唯一标识
    parent_id: Optional[str] = None       # 父节点 ID
    children_ids: List[str] = field(default_factory=list)  # 子节点 IDs

    # === 核心内容 ===
    step: Any = None  # TaskStep | ActionStep | SummaryStep
    depth: int = 0    # 从 root 开始的深度（root=0）

    # === 状态 ===
    status: NodeStatus = NodeStatus.PENDING

    # === 评估（用于路径选择和训练数据）===
    score: Optional[float] = None
    # score 可以来自：
    # - 奖励模型评估
    # - 执行结果（测试通过=1.0, 失败=0.0）
    # - 人工标注
    # - 其他启发式

    # === 生成信息（用于分析和复现）===
    generated_by: str = ""                    # 模型名称
    generation_strategy: str = ""             # 生成时的候选选择策略类名
    generation_params: Dict[str, Any] = field(default_factory=dict)
    # 例如：{"breadth": 3, "candidate_index": 2}；模型采样配置不属于探索策略。

    # === 自由标注（可选，用于训练数据标注）===
    annotations: Dict[str, Any] = field(default_factory=dict)
    # 用户可以添加任何标注，如：
    # - "human_preference": 1
    # - "execution_time": 1.23
    # - "test_passed": true
    # - "expert_rating": 4.5


@dataclass
class ExplorationPath:
    """一条完整的探索路径（从 root 到某个叶子节点）

    代表一次完整的任务执行轨迹，用于：
    - Best-of-N 路径选择
    - RL 训练数据（trajectory）
    - 路径对比分析
    """

    # === 路径标识 ===
    path_id: str                          # 唯一标识
    node_ids: List[str]                   # 节点序列（从 root 到叶子）

    # === 路径评估 ===
    total_score: float = 0.0              # 路径总分（所有节点 score 之和）
    success: bool = False                 # 是否成功完成任务

    # === Best-of-N 排名 ===
    rank: Optional[int] = None            # 在所有路径中的排名（1=最优）

    # === 自由元数据 ===
    metadata: Dict[str, Any] = field(default_factory=dict)
    # 可以包含：
    # - "total_steps": 5
    # - "total_time": 12.34
    # - "total_tokens": 1500


__all__ = [
    "NodeStatus",
    "ContextNode",
    "ExplorationPath",
]
