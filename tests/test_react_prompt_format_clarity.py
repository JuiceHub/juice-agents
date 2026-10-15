"""测试 ReAct prompt 的输出格式清晰度改进。

验证修复后的 prompt 包含明确的输出 schema 和示例，
以防止模型输出 </actions></actions> 等格式错误。
"""

import re

import pytest

from sdk.src.juice_agents.core.agent.prompts.react_agent_prompt import (
    get_react_system_prompt,
)


class TestReActPromptFormatClarity:
    """测试 ReAct prompt 输出格式的清晰度。"""

    @pytest.mark.parametrize("language", ["en", "zh"])
    def test_prompt_contains_output_format_schema(self, language: str):
        """验证 prompt 包含明确的输出格式 Schema 部分。"""
        prompt = get_react_system_prompt(language)

        if language == "en":
            assert "Output Format Schema" in prompt, "英文 prompt 应包含 'Output Format Schema' 章节"
            assert "Every step must produce EXACTLY this format:" in prompt
        else:
            assert "输出格式 Schema" in prompt, "中文 prompt 应包含 '输出格式 Schema' 章节"
            assert "每一步必须严格按以下格式输出：" in prompt

    @pytest.mark.parametrize("language", ["en", "zh"])
    def test_prompt_contains_correct_examples(self, language: str):
        """验证 prompt 包含正确的输出示例。"""
        prompt = get_react_system_prompt(language)

        if language == "en":
            assert "## Correct Examples" in prompt, "英文 prompt 应包含 'Correct Examples' 部分"
            # 验证示例格式完整
            assert "<thought>" in prompt and "</thought>" in prompt
            assert "<actions>" in prompt and "</actions>" in prompt
            assert '{"name":' in prompt or '"name":' in prompt
        else:
            assert "## 正确示例" in prompt, "中文 prompt 应包含 '正确示例' 部分"
            assert "<thought>" in prompt and "</thought>" in prompt
            assert "<actions>" in prompt and "</actions>" in prompt

    @pytest.mark.parametrize("language", ["en", "zh"])
    def test_prompt_contains_common_mistakes(self, language: str):
        """验证 prompt 包含常见错误示范部分。"""
        prompt = get_react_system_prompt(language)

        if language == "en":
            assert "## Common Mistakes to Avoid" in prompt, "英文 prompt 应包含 'Common Mistakes' 部分"
            # 验证明确指出重复闭合标签是错误的
            assert "duplicate closing tag" in prompt.lower() or "</actions></actions>" in prompt
            # 验证标记了错误和正确的示例
            assert "WRONG:" in prompt and "CORRECT:" in prompt
        else:
            assert "## 常见错误示范" in prompt, "中文 prompt 应包含 '常见错误示范' 部分"
            assert "重复的闭合标签" in prompt or "</actions></actions>" in prompt
            assert "错误：" in prompt and "正确：" in prompt

    @pytest.mark.parametrize("language", ["en", "zh"])
    def test_prompt_emphasizes_actions_must_be_last(self, language: str):
        """验证 prompt 强调 </actions> 必须是输出的最后内容。"""
        prompt = get_react_system_prompt(language)

        if language == "en":
            # 检查是否有明确强调 </actions> 必须是最后内容
            assert "MUST be the last" in prompt or "must be the last" in prompt, \
                "英文 prompt 应明确强调 </actions> 必须是最后内容"
            assert "Do not write anything after" in prompt or "nothing after" in prompt.lower()
        else:
            # 检查中文版本
            assert "必须是" in prompt and "最后内容" in prompt, \
                "中文 prompt 应明确强调 </actions> 必须是最后内容"
            assert "之后不得输出任何内容" in prompt or "不得输出" in prompt

    @pytest.mark.parametrize("language", ["en", "zh"])
    def test_prompt_shows_duplicate_closing_tag_is_wrong(self, language: str):
        """验证 prompt 明确指出 </actions></actions> 是错误的。"""
        prompt = get_react_system_prompt(language)

        # 应该包含错误示例：</actions></actions>
        assert "</actions></actions>" in prompt, \
            f"{language} prompt 应包含 </actions></actions> 作为错误示例"

        # 在错误示例附近应该有错误标记
        if language == "en":
            pattern = r"WRONG:.*</actions></actions>"
        else:
            pattern = r"错误：.*</actions></actions>"

        assert re.search(pattern, prompt, re.IGNORECASE | re.DOTALL), \
            f"{language} prompt 应标记 </actions></actions> 为错误"

    def test_format_schema_appears_early_in_protocol_section(self):
        """验证输出格式 Schema 出现在 ReAct Protocol 部分的前面位置。"""
        prompt_en = get_react_system_prompt("en")
        prompt_zh = get_react_system_prompt("zh")

        # 找到 ReAct Protocol 部分和 Output Format Schema 的位置
        protocol_pos_en = prompt_en.find("# ReAct Protocol")
        schema_pos_en = prompt_en.find("## Output Format Schema")

        protocol_pos_zh = prompt_zh.find("# ReAct 协议")
        schema_pos_zh = prompt_zh.find("## 输出格式 Schema")

        # Schema 应该在 Protocol 部分开始后不久出现
        assert protocol_pos_en >= 0 and schema_pos_en > protocol_pos_en, \
            "Output Format Schema 应该出现在 ReAct Protocol 部分内"
        assert schema_pos_en - protocol_pos_en < 500, \
            "Output Format Schema 应该在 ReAct Protocol 部分的前面位置"

        assert protocol_pos_zh >= 0 and schema_pos_zh > protocol_pos_zh, \
            "输出格式 Schema 应该出现在 ReAct 协议部分内"
        assert schema_pos_zh - protocol_pos_zh < 500, \
            "输出格式 Schema 应该在 ReAct 协议部分的前面位置"

    def test_examples_show_complete_format(self):
        """验证示例展示了完整的输出格式（包含 thought 和 actions）。"""
        prompt_en = get_react_system_prompt("en")
        prompt_zh = get_react_system_prompt("zh")

        # 提取示例部分
        examples_en = re.search(r"## Correct Examples.*?##", prompt_en, re.DOTALL)
        examples_zh = re.search(r"## 正确示例.*?##", prompt_zh, re.DOTALL)

        assert examples_en, "英文 prompt 应包含 Correct Examples 部分"
        assert examples_zh, "中文 prompt 应包含正确示例部分"

        examples_text_en = examples_en.group(0)
        examples_text_zh = examples_zh.group(0)

        # 验证示例包含完整的 thought + actions 格式
        assert "<thought>" in examples_text_en and "</thought>" in examples_text_en
        assert "<actions>" in examples_text_en and "</actions>" in examples_text_en
        assert re.search(r'<actions>\[.*?\]</actions>', examples_text_en, re.DOTALL), \
            "英文示例应展示完整的 actions 数组格式"

        assert "<thought>" in examples_text_zh and "</thought>" in examples_text_zh
        assert "<actions>" in examples_text_zh and "</actions>" in examples_text_zh
        assert re.search(r'<actions>\[.*?\]</actions>', examples_text_zh, re.DOTALL), \
            "中文示例应展示完整的 actions 数组格式"

    def test_critical_warning_is_bold_or_emphasized(self):
        """验证关键警告使用了强调格式（粗体或特殊标记）。"""
        prompt_en = get_react_system_prompt("en")
        prompt_zh = get_react_system_prompt("zh")

        # 检查是否使用了强调标记
        # Markdown 中 **text** 表示粗体，也可能使用其他强调方式
        assert "**Critical**" in prompt_en or "**MUST**" in prompt_en, \
            "英文 prompt 应使用粗体或强调标记突出关键警告"

        assert "**关键要求**" in prompt_zh or "**必须**" in prompt_zh, \
            "中文 prompt 应使用粗体或强调标记突出关键警告"
