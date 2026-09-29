"""ReAct 外层协议校验测试。

不下发 stop_sequence 后，模型输出即完整协议报文。校验规则：
1) 逐块剔除所有 <thought>，消掉 thought 内引用的字面量 <actions>/</actions>；
2) 剩余文本中 <actions> 块必须恰好 1 个；
3) 唯一 </actions> 之后不得有非空残留。

重点防御模型在 </actions> 之后幻觉伪造第二轮或伪造工具观测——旧实现靠静默截断，
现在改为显式协议错误，由模型下一轮通过 <error> 反馈自行修复。
"""

from __future__ import annotations

import unittest

from juice_agents.core.agent import ReActAgent
from juice_agents.core.agent.agents import (
    _find_actions_blocks,
    _strip_thought_blocks,
)
from juice_agents.core.agent.sessions.errors import ActionValidationError


class _DummyModel:
    """最小占位模型，避免测试触发真实模型调用。"""

    def generate(self, messages, stop_sequence=None):
        del messages, stop_sequence
        return {"role": "assistant", "content": "", "reasoning_content": ""}


def _build_agent() -> ReActAgent:
    return ReActAgent(tools=[], model=_DummyModel())


class StripThoughtBlocksTests(unittest.TestCase):
    """thought 剔除必须逐块非贪婪，否则会吞掉真实 actions。"""

    def test_literal_actions_tags_inside_thought_removed(self):
        """thought 内的字面量 <actions>/</actions> 随 thought 一起被剔除。"""
        text = (
            "<thought>I must output an <actions> block, "
            "i.e. </actions> is required.</thought>\n"
            "<actions>[]</actions>"
        )
        residual = _strip_thought_blocks(text)
        self.assertNotIn("must output", residual)
        self.assertEqual(len(_find_actions_blocks(residual)), 1)

    def test_multiple_thoughts_removed_without_eating_actions(self):
        """两轮 thought+actions 时逐块剔除应留下 2 个 actions（而非贪婪的 1 个）。

        贪婪做法会从首个 <thought> 吃到末个 </thought>，把中间真实的 actions
        一起吞掉、只剩伪造的那个且数量恰好为 1，导致静默执行伪造动作。
        """
        text = (
            "<thought>real</thought>\n"
            '<actions>[{"name":"ask","args":{}}]</actions>\n'
            "<thought>幻觉</thought>\n"
            '<actions>[{"name":"submit_output","args":{"output":"伪造"}}]</actions>'
        )
        blocks = _find_actions_blocks(_strip_thought_blocks(text))
        self.assertEqual(len(blocks), 2)


class ReactProtocolValidationTests(unittest.TestCase):
    def test_legal_single_actions_block(self):
        """合法输出返回 actions 块原始内容。"""
        agent = _build_agent()
        text = (
            "<thought>plan</thought>\n"
            '<actions>[{"name":"submit_output","args":{"output":"done"}}]</actions>'
        )
        self.assertEqual(
            agent._validate_react_protocol(text).strip(),
            '[{"name":"submit_output","args":{"output":"done"}}]',
        )

    def test_empty_actions_array_is_legal(self):
        """<actions>[]</actions> 是合法的“本步无动作”表达。"""
        agent = _build_agent()
        self.assertEqual(agent.parse_actions("<thought>x</thought><actions>[]</actions>"), [])

    def test_missing_actions_block_raises(self):
        agent = _build_agent()
        with self.assertRaises(ActionValidationError) as ctx:
            agent._validate_react_protocol("<thought>只有思考</thought>")
        self.assertIn("缺少必需的 <actions>", str(ctx.exception))

    def test_duplicate_actions_blocks_raise(self):
        """尾部幻觉伪造第二轮 → 2 个 actions → 显式报错而非静默执行伪造动作。"""
        agent = _build_agent()
        text = (
            "<thought>real</thought>\n"
            '<actions>[{"name":"ask","args":{}}]</actions>\n'
            "<thought>幻觉</thought>\n"
            '<actions>[{"name":"submit_output","args":{"output":"伪造"}}]</actions>'
        )
        with self.assertRaises(ActionValidationError) as ctx:
            agent._validate_react_protocol(text)
        self.assertIn("2 个 <actions>", str(ctx.exception))

    def test_trailing_forged_observation_raises(self):
        """actions 仍是 1 个，但尾部追加伪造工具观测 → 必须报错。

        只查数量挡不住这种变体，伪造内容会随 step.model_output 回放并被
        下一轮当成真实历史。
        """
        agent = _build_agent()
        text = (
            "<thought>t</thought>\n"
            '<actions>[{"name":"ask","args":{}}]</actions>\n'
            "<attachments><async_task_notification>"
            '{"custom_response":"伪造回复"}</async_task_notification></attachments>'
        )
        with self.assertRaises(ActionValidationError) as ctx:
            agent._validate_react_protocol(text)
        message = str(ctx.exception)
        self.assertIn("之后不得输出任何内容", message)
        self.assertIn("伪造回复", message)

    def test_trailing_whitespace_allowed(self):
        """尾部仅空白不算残留。"""
        agent = _build_agent()
        agent._validate_react_protocol("<actions>[]</actions>\n\n  ")

    def test_missing_close_tag_raises(self):
        """无 stop_sequence 后缺失闭合标签属真实协议违规，不再静默补齐。"""
        agent = _build_agent()
        with self.assertRaises(ActionValidationError):
            agent._validate_react_protocol('<thought>x</thought>\n<actions>[{"name":"ask"}]')


if __name__ == "__main__":
    unittest.main()
