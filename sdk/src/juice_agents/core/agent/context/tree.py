"""上下文树 - 核心数据结构。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .nodes import ContextNode, ExplorationPath, NodeStatus

logger = logging.getLogger(__name__)


@dataclass
class ContextTree:
    """上下文探索树

    统一管理：
    1. 树状结构（节点、边、路径）
    2. 探索策略
    3. 压缩机制
    4. 数据导出
    """

    # ============================================
    # 基础信息
    # ============================================
    tree_id: str                          # 树的唯一标识
    system_prompt: str                    # 系统提示词
    system_prompt_static: Optional[str] = None   # 静态部分（缓存）
    system_prompt_dynamic: Optional[str] = None  # 动态部分
    include_reasoning_in_context: bool = False   # 是否包含推理过程

    # ============================================
    # 树结构
    # ============================================
    nodes: Dict[str, ContextNode] = field(default_factory=dict)
    # nodes: {node_id -> ContextNode}

    root_id: str = ""                     # 根节点 ID

    # ============================================
    # 路径管理
    # ============================================
    all_paths: List[ExplorationPath] = field(default_factory=list)
    # 所有已完成的路径

    active_leaf_ids: List[str] = field(default_factory=list)
    # 当前活跃的叶子节点（正在探索的前沿）

    selected_path_id: Optional[str] = None
    # 最终选中的最优路径（Best-of-N 选出）

    # 注意：探索策略不存储在 ContextTree 中
    # 策略是 Agent 级别的配置，可以动态切换
    # ContextTree 只负责存储树结构数据

    # ============================================
    # 训练数据标注
    # ============================================
    task_metadata: Dict[str, Any] = field(default_factory=dict)
    # task_metadata 示例：
    # {
    #     "task_id": "task_001",
    #     "task_type": "code_debug",
    #     "difficulty": "hard",
    #     "ground_truth": "...",
    # }

    # ============================================
    # 统计信息
    # ============================================
    stats: Dict[str, Any] = field(default_factory=dict)

    # ============================================
    # 核心方法：树操作
    # ============================================

    def add_node(
        self,
        node: ContextNode,
        parent_id: Optional[str] = None
    ) -> None:
        """添加节点到树中"""
        if node.node_id in self.nodes:
            raise ValueError(f"节点 {node.node_id} 已存在")

        # 设置父子关系
        if parent_id:
            if parent_id not in self.nodes:
                raise ValueError(f"父节点 {parent_id} 不存在")

            parent = self.nodes[parent_id]
            parent.children_ids.append(node.node_id)
            node.parent_id = parent_id
            node.depth = parent.depth + 1
        else:
            # root 节点
            if self.root_id:
                raise ValueError(f"Root 节点已存在: {self.root_id}")
            self.root_id = node.node_id
            node.depth = 0

        self.nodes[node.node_id] = node

        # 更新统计
        self.stats["total_nodes_created"] = self.stats.get("total_nodes_created", 0) + 1

    def get_node(self, node_id: str) -> ContextNode:
        """获取节点"""
        if node_id not in self.nodes:
            raise KeyError(f"节点 {node_id} 不存在")
        return self.nodes[node_id]

    def get_path_to_node(self, node_id: str) -> List[str]:
        """获取从 root 到指定节点的路径（节点 ID 列表）"""
        if node_id not in self.nodes:
            return []

        path = []
        current_id = node_id
        while current_id:
            path.append(current_id)
            node = self.nodes[current_id]
            current_id = node.parent_id

        return list(reversed(path))

    def get_path_nodes(self, node_id: str) -> List[ContextNode]:
        """获取从 root 到指定节点的路径（节点对象列表）"""
        node_ids = self.get_path_to_node(node_id)
        return [self.nodes[nid] for nid in node_ids]

    def get_children(self, node_id: str) -> List[ContextNode]:
        """获取节点的所有子节点"""
        node = self.get_node(node_id)
        return [self.nodes[cid] for cid in node.children_ids]

    def get_siblings(self, node_id: str) -> List[ContextNode]:
        """获取节点的所有兄弟节点（同一父节点下的其他子节点）"""
        node = self.get_node(node_id)
        if not node.parent_id:
            return []
        return [
            self.nodes[cid]
            for cid in self.nodes[node.parent_id].children_ids
            if cid != node_id
        ]

    def get_leaf_nodes(self) -> List[ContextNode]:
        """获取所有叶子节点"""
        return [node for node in self.nodes.values() if not node.children_ids]

    def get_active_leaves(self) -> List[ContextNode]:
        """获取当前活跃的叶子节点"""
        return [self.nodes[nid] for nid in self.active_leaf_ids if nid in self.nodes]

    # ============================================
    # 路径操作
    # ============================================

    def create_path_from_node(self, node_id: str) -> ExplorationPath:
        """从节点创建完整路径"""
        node_ids = self.get_path_to_node(node_id)
        nodes = [self.nodes[nid] for nid in node_ids]

        # 计算路径统计
        total_score = sum(n.score or 0.0 for n in nodes)
        final_node = nodes[-1] if nodes else None
        success = final_node.status == NodeStatus.SUCCESS if final_node else False

        path = ExplorationPath(
            path_id=f"path_{node_id}",
            node_ids=node_ids,
            total_score=total_score,
            success=success
        )

        return path

    def finalize_path(self, node_id: str) -> ExplorationPath:
        """完成一条路径（从叶子节点）并添加到 all_paths"""
        path = self.create_path_from_node(node_id)
        self.all_paths.append(path)

        # 从活跃叶子中移除
        if node_id in self.active_leaf_ids:
            self.active_leaf_ids.remove(node_id)

        return path

    def get_best_path(self) -> Optional[ExplorationPath]:
        """获取最优路径"""
        if self.selected_path_id:
            # 显式选中的路径
            return next(
                (p for p in self.all_paths if p.path_id == self.selected_path_id),
                None
            )

        if not self.all_paths:
            return None

        # 自动选择：优先成功路径，按 total_score 排序
        success_paths = [p for p in self.all_paths if p.success]
        if success_paths:
            return max(success_paths, key=lambda p: p.total_score)

        # 没有成功路径，选 score 最高的
        return max(self.all_paths, key=lambda p: p.total_score)

    def rank_paths(self) -> List[ExplorationPath]:
        """给所有路径排序并分配 rank"""
        sorted_paths = sorted(
            self.all_paths,
            key=lambda p: (p.success, p.total_score),
            reverse=True
        )

        for rank, path in enumerate(sorted_paths, start=1):
            path.rank = rank

        return sorted_paths

    # ============================================
    # 上下文管理
    # ============================================

    def get_context_messages(self, node_id: str) -> List[Dict[str, Any]]:
        """获取到达某个节点的完整上下文消息

        用于传递给模型生成下一步
        """
        messages = []

        # 1. System prompt
        if self.system_prompt_static:
            from .content import system_content_blocks
            system_content = system_content_blocks(
                self.system_prompt_static,
                self.system_prompt_dynamic
            )
            messages.append({"role": "system", "content": system_content})
        else:
            messages.append({"role": "system", "content": self.system_prompt})

        # 2. 路径上的所有 steps
        path_nodes = self.get_path_nodes(node_id)
        for node in path_nodes:
            step_messages = node.step.to_messages(
                include_reasoning_in_context=self.include_reasoning_in_context
            )
            messages.extend(step_messages)

        return messages

    # ============================================
    # 剪枝
    # ============================================

    def prune_low_value_branches(self, threshold: float = 0.3) -> List[str]:
        """剪枝低价值分支"""
        pruned_ids = []

        for node_id, node in self.nodes.items():
            if node.status != NodeStatus.ACTIVE:
                continue

            # 基于 score
            if node.score is not None and node.score < threshold:
                node.status = NodeStatus.REJECTED
                pruned_ids.append(node_id)

                # 从活跃叶子中移除
                if node_id in self.active_leaf_ids:
                    self.active_leaf_ids.remove(node_id)

        self.stats["total_nodes_pruned"] = self.stats.get("total_nodes_pruned", 0) + len(pruned_ids)
        return pruned_ids

    # ============================================
    # 向后兼容：转换为线性格式
    # ============================================

    def to_linear_steps(self, path_id: Optional[str] = None) -> List[Any]:
        """转换为线性 steps 列表（向后兼容）"""
        target_path_id = path_id or self.selected_path_id

        if target_path_id:
            path = next(
                (p for p in self.all_paths if p.path_id == target_path_id),
                None
            )
            if path:
                return [self.nodes[nid].step for nid in path.node_ids]

        # 如果没有指定路径，使用最优路径
        best_path = self.get_best_path()
        if best_path:
            return [self.nodes[nid].step for nid in best_path.node_ids]

        # 如果没有完整路径，使用根节点到某个活跃叶子
        if self.active_leaf_ids:
            node_ids = self.get_path_to_node(self.active_leaf_ids[0])
            return [self.nodes[nid].step for nid in node_ids]

        # 最后的退路：只返回根节点
        if self.root_id:
            return [self.nodes[self.root_id].step]

        return []

    def to_messages(self, path_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """转换为线性消息列表（向后兼容）"""
        steps = self.to_linear_steps(path_id)

        messages = []

        # System prompt
        if self.system_prompt_static:
            from .content import system_content_blocks
            system_content = system_content_blocks(
                self.system_prompt_static,
                self.system_prompt_dynamic
            )
            messages.append({"role": "system", "content": system_content})
        else:
            messages.append({"role": "system", "content": self.system_prompt})

        # Steps
        for step in steps:
            step_messages = step.to_messages(
                include_reasoning_in_context=self.include_reasoning_in_context
            )
            messages.extend(step_messages)

        return messages

    # ============================================
    # 向后兼容属性
    # ============================================

    @property
    def steps(self) -> List[Any]:
        """向后兼容：返回线性步骤列表

        注意：这是计算属性，会遍历树结构生成线性视图。
        如果需要频繁访问，请缓存结果。

        Returns:
            线性步骤列表，基于当前选中的路径或最优路径
        """
        return self.to_linear_steps()

    def append_step(self, step: Any) -> None:
        """向后兼容：追加步骤到当前活跃路径

        将步骤作为新节点添加到树中，连接到当前活跃叶子节点。
        如果没有活跃叶子，则连接到根节点或创建根节点。

        Args:
            step: 要添加的 AgentStep（TaskStep/ActionStep/SummaryStep）
        """
        # 生成唯一的节点ID
        import uuid
        node_id = f"node_{uuid.uuid4().hex[:8]}"
        while node_id in self.nodes:
            node_id = f"node_{uuid.uuid4().hex[:8]}"

        node = ContextNode(
            node_id=node_id,
            step=step,
            status=NodeStatus.ACTIVE,
        )

        # 确定父节点
        if not self.nodes:
            # 第一个节点，作为根节点
            self.add_node(node, parent_id=None)
            self.active_leaf_ids = [node_id]
        elif self.active_leaf_ids:
            # 添加到第一个活跃叶子节点
            parent_id = self.active_leaf_ids[0]
            # 移除旧的活跃叶子
            self.active_leaf_ids.remove(parent_id)
            # 添加新节点
            self.add_node(node, parent_id=parent_id)
            # 新节点成为活跃叶子
            self.active_leaf_ids.append(node_id)
        else:
            # 没有活跃叶子，添加到根节点
            self.add_node(node, parent_id=self.root_id)
            self.active_leaf_ids.append(node_id)

    # ============================================
    # 统计和调试
    # ============================================

    def get_tree_stats(self) -> Dict[str, Any]:
        """获取树的统计信息"""
        leaf_nodes = self.get_leaf_nodes()
        active_nodes = [n for n in self.nodes.values() if n.status == NodeStatus.ACTIVE]

        return {
            **self.stats,
            "total_nodes": len(self.nodes),
            "total_paths": len(self.all_paths),
            "active_leaves": len(self.active_leaf_ids),
            "total_leaves": len(leaf_nodes),
            "active_nodes": len(active_nodes),
            "max_depth": max((n.depth for n in self.nodes.values()), default=0),
            "success_paths": sum(1 for p in self.all_paths if p.success),
        }

    # ============================================
    # 路径管理（用于增量压缩）
    # ============================================

    def switch_to_path(self, path_id: str) -> bool:
        """切换到指定路径

        Args:
            path_id: 目标路径ID

        Returns:
            是否切换成功
        """
        for path in self.all_paths:
            if path.path_id == path_id:
                self.selected_path_id = path_id
                # 更新活跃叶子为该路径的最后节点
                if path.node_ids:
                    last_node_id = path.node_ids[-1]
                    if last_node_id in self.nodes:
                        self.active_leaf_ids = [last_node_id]
                logger.info("切换到路径: path_id=%s", path_id)
                return True
        logger.warning("路径不存在: path_id=%s", path_id)
        return False

    def switch_to_original_path(self) -> bool:
        """切换回未压缩的原始路径

        Returns:
            是否切换成功
        """
        # 找到没有压缩标记的路径（最早的路径）
        for path in self.all_paths:
            if not path.metadata.get("compression_type"):
                return self.switch_to_path(path.path_id)
        logger.warning("未找到原始路径")
        return False

    def get_compression_history(self) -> List[Dict[str, Any]]:
        """获取压缩历史

        Returns:
            压缩历史列表，每项包含节点ID、时间、压缩节点数等信息
        """
        from .steps import SummaryStep

        compressions = []
        for node in self.nodes.values():
            if isinstance(node.step, SummaryStep):
                if node.annotations.get("compression_type") == "compact":
                    compressions.append({
                        "node_id": node.node_id,
                        "compressed_at": node.annotations.get("compressed_at"),
                        "compressed_node_count": len(node.annotations.get("compressed_node_ids", [])),
                        "summary_preview": node.step.content[:100] if node.step.content else "",
                    })
        return sorted(compressions, key=lambda x: x.get("compressed_at", 0))

    def build_path_from_leaf(self, leaf_node_id: str) -> Optional[ExplorationPath]:
        """从叶子节点回溯构建路径

        Args:
            leaf_node_id: 叶子节点ID

        Returns:
            构建的探索路径，如果节点不存在则返回 None
        """
        if leaf_node_id not in self.nodes:
            return None

        node_ids = []
        current_id = leaf_node_id

        while current_id:
            node_ids.insert(0, current_id)
            node = self.nodes.get(current_id)
            if not node or not node.parent_id:
                break
            current_id = node.parent_id

        import time
        import uuid
        return ExplorationPath(
            path_id=f"auto_path_{int(time.time())}_{uuid.uuid4().hex[:6]}",
            node_ids=node_ids,
            success=True,
        )


__all__ = [
    "ContextTree",
]
