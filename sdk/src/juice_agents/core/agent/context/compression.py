"""会话压缩策略。"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from .content import (
    DEFAULT_COMPACT_CONTINUATION_TEMPLATE,
    DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER,
    message_content_to_transcript_parts,
    text_block,
)
from .steps import ActionStep, AgentStep, SummaryStep, TaskStep
from .tree import ContextTree
from .nodes import ContextNode, ExplorationPath, NodeStatus

logger = logging.getLogger(__name__)

_DEFAULT_SUMMARY_SYSTEM_PROMPT = """You are summarizing a conversation transcript. Produce a concise summary that preserves: the user's task, key decisions, current state, and what to do next. Output must be wrapped in a single <summary>...</summary> block. Do not include any text outside that block."""


def clone_context_tree(tree: ContextTree) -> ContextTree:
    """深拷贝 ContextTree"""
    from .serialization import deserialize_context_tree, serialize_context_tree
    serialized = serialize_context_tree(tree)
    return deserialize_context_tree(serialized)


def count_action_steps(tree: ContextTree) -> int:
    """统计 ContextTree 中的 ActionStep 数量"""
    steps = tree.steps
    return sum(1 for step in steps if isinstance(step, ActionStep))


def clone_step(step: AgentStep) -> AgentStep:
    """复制单个 step"""
    if isinstance(step, TaskStep):
        return TaskStep(
            task=step.task,
            task_images=list(step.task_images),
            attachments=[dict(item) for item in step.attachments if isinstance(item, dict)],
        )
    elif isinstance(step, ActionStep):
        return ActionStep(
            step_num=step.step_num,
            model_output=step.model_output,
            thought=step.thought,
            tool_calls=[dict(item) for item in step.tool_calls if isinstance(item, dict)],
            code_action=step.code_action,
            reasoning_content=step.reasoning_content,
            observations=list(step.observations),
            observations_images=list(step.observations_images),
            attachments=[dict(item) for item in step.attachments if isinstance(item, dict)],
            error=step.error,
            observation_limits=list(step.observation_limits),
            request_bytes=step.request_bytes,
            usage=None if step.usage is None else dict(step.usage),
            round_outcome=step.round_outcome,
            output=step.output,
        )
    elif isinstance(step, SummaryStep):
        return SummaryStep(content=step.content)
    return step


class CompressStrategy(ABC):
    """会话压缩策略抽象基类：各子类维护自身参数，并实现是否压缩与如何压缩。"""

    @abstractmethod
    def should_compress(self, tree: ContextTree) -> bool:
        """判断当前上下文树是否应触发压缩。"""

    @abstractmethod
    def compress(self, tree: ContextTree) -> ContextTree:
        """执行压缩，返回新的 ContextTree（不修改原 tree）。"""


class NoCompressStrategy(CompressStrategy):
    """不压缩：始终不触发，compress 返回原树副本。"""

    def should_compress(self, tree: ContextTree) -> bool:
        return False

    def compress(self, tree: ContextTree) -> ContextTree:
        return clone_context_tree(tree)


class RemainCompressStrategy(CompressStrategy):
    """
    保留最近 k 轮观测的压缩：超出部分用 placeholder 替换观测内容。
    参数：k（保留的 ActionStep 数量）、placeholder（占位文案）。
    """

    def __init__(self, k: int = 5, placeholder: str | None = None):
        if not isinstance(k, int) or k < 0:
            raise ValueError("k 必须为非负整数")
        self.k = k
        self.placeholder = placeholder or DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER

    def should_compress(self, tree: ContextTree) -> bool:
        return count_action_steps(tree) > self.k

    def compress(self, tree: ContextTree) -> ContextTree:
        logger.info("压缩会话: strategy=remain, k=%s", self.k)
        action_step_indices = [
            idx for idx, step in enumerate(tree.steps) if isinstance(step, ActionStep)
        ]
        keep_indices = set(action_step_indices[-self.k:]) if self.k > 0 else set()
        compressed = ContextTree(
            tree_id=f"{tree.tree_id}_compressed",
            system_prompt=tree.system_prompt,
            include_reasoning_in_context=tree.include_reasoning_in_context,
            system_prompt_static=tree.system_prompt_static,
            system_prompt_dynamic=tree.system_prompt_dynamic,
        )
        for idx, step in enumerate(tree.steps):
            new_step = clone_step(step)
            if isinstance(new_step, ActionStep) and idx not in keep_indices:
                if new_step.observations or new_step.observations_images:
                    new_step.observations = [self.placeholder]
                    new_step.observations_images = []
            compressed.append_step(new_step)
        return compressed


class CompactCompressStrategy(CompressStrategy):
    """
    参考 Claude 风格的 compact：摘要较老前缀，同时保留最近若干 ActionStep 原文。

    参数：
    - step_threshold：达到该 ActionStep 数时允许触发压缩
    - compression_model：必须具备 `generate(messages)` 接口
    - compact_instructions：额外摘要指令
    - continuation_template：压缩后续写提示模板
    - preserve_recent_actions：默认保留最近 1 个 ActionStep；尾部含 error/update/attachments 时自动扩到 2 个
    - incremental：是否使用增量压缩（在树上添加分支），默认 True
    """

    def __init__(
        self,
        step_threshold: int,
        compression_model: Any,
        compact_instructions: str | None = None,
        continuation_template: str | None = None,
        preserve_recent_actions: int = 1,
        incremental: bool = True,
    ):
        if not isinstance(step_threshold, int) or step_threshold < 1:
            raise ValueError("step_threshold 必须为正整数")
        if compression_model is None or not callable(getattr(compression_model, "generate", None)):
            raise ValueError("compact 策略必须提供具备 generate(messages) 的 compression_model")
        if not isinstance(preserve_recent_actions, int) or preserve_recent_actions < 0:
            raise ValueError("preserve_recent_actions 必须为非负整数")
        self.step_threshold = step_threshold
        self.compression_model = compression_model
        self.compact_instructions = compact_instructions or ""
        self.continuation_template = continuation_template or DEFAULT_COMPACT_CONTINUATION_TEMPLATE
        self.preserve_recent_actions = preserve_recent_actions
        self.incremental = incremental

    def should_compress(self, tree: ContextTree) -> bool:
        if count_action_steps(tree) < self.step_threshold:
            return False
        prefix_steps, _suffix_steps = _split_steps_for_compaction(
            tree, preserve_recent_actions=self.preserve_recent_actions
        )
        if not prefix_steps:
            return False
        # 已经 compact 成 `SummaryStep + preserved suffix` 且没有新增历史时，不重复压缩。
        if len(prefix_steps) == 1 and isinstance(prefix_steps[0], SummaryStep):
            return False
        return True

    def compress(self, tree: ContextTree) -> ContextTree:
        """执行压缩"""
        if self.incremental:
            return self._incremental_compress(tree)
        else:
            return self._replace_compress(tree)

    def _replace_compress(self, tree: ContextTree) -> ContextTree:
        """替换式压缩：创建新树（原有行为）"""
        logger.info(
            "压缩会话(替换模式): strategy=compact, step_threshold=%s, preserve_recent_actions=%s",
            self.step_threshold,
            self.preserve_recent_actions,
        )
        prefix_steps, suffix_steps = _split_steps_for_compaction(
            tree, preserve_recent_actions=self.preserve_recent_actions
        )
        summary_text = _summarize_steps(
            tree=tree,
            steps=prefix_steps,
            compression_model=self.compression_model,
            compact_instructions=self.compact_instructions,
        )
        continuation_text = self.continuation_template.format(summary=summary_text)
        new_tree = ContextTree(
            tree_id=f"{tree.tree_id}_compacted",
            system_prompt=tree.system_prompt,
            include_reasoning_in_context=tree.include_reasoning_in_context,
            system_prompt_static=tree.system_prompt_static,
            system_prompt_dynamic=tree.system_prompt_dynamic,
        )
        new_tree.append_step(SummaryStep(content=continuation_text))
        for step in suffix_steps:
            new_tree.append_step(step)
        return new_tree

    def _incremental_compress(self, tree: ContextTree) -> ContextTree:
        """增量压缩：在树上添加摘要分支，保留原始历史"""
        import time
        import uuid

        logger.info(
            "压缩会话(增量模式): strategy=compact, step_threshold=%s, preserve_recent_actions=%s",
            self.step_threshold,
            self.preserve_recent_actions,
        )

        # 1. 获取当前活跃路径
        active_path = self._get_active_path(tree)
        if not active_path:
            logger.warning("没有活跃路径，回退到替换模式")
            return self._replace_compress(tree)

        # 2. 分割步骤
        prefix_steps, suffix_steps = _split_steps_for_compaction(
            tree, preserve_recent_actions=self.preserve_recent_actions
        )

        if not prefix_steps:
            logger.warning("没有可压缩的步骤，跳过压缩")
            return tree

        # 3. 找到分割点节点（prefix 的最后一个节点）
        split_node_id = self._find_split_node_id(tree, active_path, len(prefix_steps))
        if not split_node_id:
            logger.warning("无法找到分割点，回退到替换模式")
            return self._replace_compress(tree)

        # 4. 生成摘要
        summary_text = _summarize_steps(
            tree=tree,
            steps=prefix_steps,
            compression_model=self.compression_model,
            compact_instructions=self.compact_instructions,
        )
        continuation_text = self.continuation_template.format(summary=summary_text)

        # 5. 创建摘要节点
        summary_node_id = f"summary_{uuid.uuid4().hex[:8]}"
        summary_node = ContextNode(
            node_id=summary_node_id,
            parent_id=split_node_id,
            step=SummaryStep(content=continuation_text),
            status=NodeStatus.SUCCESS,
            depth=tree.nodes[split_node_id].depth + 1 if split_node_id in tree.nodes else 0,
            annotations={
                "compression_type": "compact",
                "compressed_step_count": len(prefix_steps),
                "compressed_at": time.time(),
            }
        )
        tree.add_node(summary_node, parent_id=split_node_id)

        # 6. 在摘要节点后重建保留的步骤
        current_parent = summary_node_id
        preserved_node_ids = []

        for step in suffix_steps:
            new_node_id = f"node_{uuid.uuid4().hex[:8]}"
            new_node = ContextNode(
                node_id=new_node_id,
                parent_id=current_parent,
                step=step,
                status=NodeStatus.SUCCESS,
                depth=tree.nodes[current_parent].depth + 1,
            )
            tree.add_node(new_node, parent_id=current_parent)
            preserved_node_ids.append(new_node_id)
            current_parent = new_node_id

        # 7. 创建新的压缩路径
        compressed_path = self._create_compressed_path(
            tree,
            active_path,
            split_node_id,
            summary_node_id,
            preserved_node_ids,
        )
        tree.all_paths.append(compressed_path)

        # 8. 设置为活跃路径
        tree.selected_path_id = compressed_path.path_id
        tree.active_leaf_ids = [current_parent] if preserved_node_ids else [summary_node_id]

        # 9. 更新统计
        tree.stats["total_compressions"] = tree.stats.get("total_compressions", 0) + 1

        logger.info(
            "增量压缩完成: tree_id=%s, 压缩步骤数=%d, 保留步骤数=%d, 摘要节点=%s",
            tree.tree_id,
            len(prefix_steps),
            len(suffix_steps),
            summary_node_id,
        )

        return tree

    def _get_active_path(self, tree: ContextTree) -> ExplorationPath | None:
        """获取当前活跃路径"""
        if tree.selected_path_id:
            for path in tree.all_paths:
                if path.path_id == tree.selected_path_id:
                    return path

        # 回退：使用 active_leaf_ids 构建路径
        if tree.active_leaf_ids:
            return tree.build_path_from_leaf(tree.active_leaf_ids[0])

        return None

    def _find_split_node_id(self, tree: ContextTree, path: ExplorationPath, prefix_step_count: int) -> str | None:
        """找到分割点节点ID（prefix 的最后一个节点）"""
        # 从路径中收集步骤节点
        step_count = 0
        for node_id in path.node_ids:
            node = tree.nodes.get(node_id)
            if not node:
                continue
            step_count += 1
            if step_count == prefix_step_count:
                return node_id
        return None

    def _create_compressed_path(
        self,
        tree: ContextTree,
        original_path: ExplorationPath,
        split_node_id: str,
        summary_node_id: str,
        preserved_node_ids: list[str],
    ) -> ExplorationPath:
        """创建压缩后的新路径"""
        import time
        import uuid

        new_path_ids = []

        # 1. 添加到 split_node 的所有节点
        for node_id in original_path.node_ids:
            new_path_ids.append(node_id)
            if node_id == split_node_id:
                break

        # 2. 添加摘要节点
        new_path_ids.append(summary_node_id)

        # 3. 添加保留的节点
        new_path_ids.extend(preserved_node_ids)

        return ExplorationPath(
            path_id=f"compressed_{int(time.time())}_{uuid.uuid4().hex[:6]}",
            node_ids=new_path_ids,
            success=True,
            metadata={
                "compression_type": "incremental_compact",
                "original_path_id": original_path.path_id,
                "created_at": time.time(),
            }
        )


def _split_steps_for_compaction(
    tree: ContextTree,
    *,
    preserve_recent_actions: int,
) -> tuple[list[AgentStep], list[AgentStep]]:
    """
    按 ActionStep 粒度切分前缀与 recent suffix。

    这里不按单条 message 切，避免把一个 ActionStep 内部 assistant/user 成对消息拆开。
    """
    action_indices = [idx for idx, step in enumerate(tree.steps) if isinstance(step, ActionStep)]
    if not action_indices:
        return [clone_step(step) for step in tree.steps], []

    suffix_action_count = min(
        len(action_indices),
        _determine_suffix_action_count(tree, preserve_recent_actions),
    )
    keep_indices = set(action_indices[-suffix_action_count:]) if suffix_action_count > 0 else set()

    prefix_steps: list[AgentStep] = []
    suffix_steps: list[AgentStep] = []
    for idx, step in enumerate(tree.steps):
        cloned = clone_step(step)
        if idx in keep_indices and isinstance(cloned, ActionStep):
            suffix_steps.append(cloned)
            continue
        prefix_steps.append(cloned)
    return prefix_steps, suffix_steps


def _determine_suffix_action_count(tree: ContextTree, preserve_recent_actions: int) -> int:
    """计算要保留多少个 recent ActionStep。"""
    base_count = max(0, preserve_recent_actions)
    if base_count == 0:
        return 0

    action_steps = [step for step in tree.steps if isinstance(step, ActionStep)]
    if not action_steps:
        return 0
    last_action = action_steps[-1]
    if _tail_has_context_activity(last_action) and base_count < 2:
        return 2
    return base_count


def _tail_has_context_activity(step: ActionStep) -> bool:
    """尾部若还带 error/update/attachment，往前多保留一轮，避免续写丢失最近上下文。"""
    return bool(step.error or step.attachments)


def _summarize_steps(
    *,
    tree: ContextTree,
    steps: list[AgentStep],
    compression_model: Any,
    compact_instructions: str,
) -> str:
    transcript = _build_transcript_for_steps(tree, steps)
    system_for_compress = _DEFAULT_SUMMARY_SYSTEM_PROMPT
    if compact_instructions:
        system_for_compress = system_for_compress.rstrip() + "\n\n" + compact_instructions.strip()

    summarizer_messages = [
        {"role": "system", "content": [text_block(system_for_compress)]},
        {"role": "user", "content": [text_block("Summarize the following conversation transcript:\n\n" + transcript)]},
    ]

    try:
        response = compression_model.generate(summarizer_messages)
    except Exception as exc:  # pragma: no cover - 运行时保护
        logger.exception("压缩模型生成摘要失败")
        raise RuntimeError("compact 压缩模型调用失败") from exc

    response_content = response.get("content", "") if isinstance(response, dict) else str(response)
    summary_text = _extract_summary_from_content(response_content)
    if not summary_text:
        return response_content.strip() or "(summary unavailable)"
    return summary_text


def _build_transcript_for_steps(tree: ContextTree, steps: list[AgentStep]) -> str:
    """只为被压缩前缀构建 transcript，不把 tree 的 system prompt 混进摘要输入。"""
    transcript_parts: list[str] = []
    # 压缩模型也是模型输入，必须复用与普通会话完全相同的 observation 投影预算。
    # 临时 tree 仅持有 step 引用；to_messages() 不修改 raw observation。
    projected = ContextTree(
        tree_id="temp_transcript",
        system_prompt="",
        include_reasoning_in_context=tree.include_reasoning_in_context,
    )
    # 将 steps 添加到临时树中
    for step in steps:
        projected.append_step(step)

    for message in projected.to_messages()[1:]:
        transcript_parts.extend(message_content_to_transcript_parts(message.get("content")))
    transcript = "\n\n".join(part for part in transcript_parts if str(part).strip()).strip()
    return transcript or "(no content)"


def _extract_summary_from_content(content: str) -> str:
    """从模型输出中提取 <summary>...</summary> 块内容。"""
    if not content:
        return ""
    match = re.search(r"<summary>\s*([\s\S]*?)</summary>", content, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return content.strip()


__all__ = [
    "CompressStrategy",
    "NoCompressStrategy",
    "RemainCompressStrategy",
    "CompactCompressStrategy",
]
