import unittest
from types import SimpleNamespace

import pytest

from juice_agents.core.utils import normalize_token_usage, parse_model_json


class ModelJsonParsingTests(unittest.TestCase):
    """模型输出结构化 JSON 解析能力回归测试。"""

    def test_parse_model_json_plain_text(self):
        raw = '{"a": 1, "b": "ok"}'
        parsed = parse_model_json(raw, field_name="plain")
        self.assertEqual(parsed, {"a": 1, "b": "ok"})

    def test_parse_model_json_code_fence(self):
        raw = "```json\n{\"a\": 1, \"b\": [1,2,3]}\n```"
        parsed = parse_model_json(raw, field_name="fence")
        self.assertEqual(parsed.get("b"), [1, 2, 3])

    def test_parse_model_json_repair_if_available(self):
        malformed = "{'a': 1,}"
        try:
            parsed = parse_model_json(malformed, field_name="repair")
        except ValueError:
            self.skipTest("环境未提供可用的 json_repair 兜底")
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed.get("a"), 1)

    def test_parse_model_json_expected_type_guard(self):
        raw = "[1, 2, 3]"
        with self.assertRaises(ValueError):
            parse_model_json(raw, field_name="type_guard", expected_type=dict)

    def test_parse_model_json_dict_input_direct_return(self):
        raw = {"x": 1}
        parsed = parse_model_json(raw, field_name="dict_input", expected_type=dict)
        self.assertEqual(parsed, raw)

    def test_parse_model_json_prefers_outer_json_over_embedded_html_code_fence(self):
        raw = (
            '{"dispatches": [], "final_report": "请保存以下 HTML:\\n'
            '```html\\n<!DOCTYPE html>\\n<html><body>ok</body></html>\\n```"}'
        )
        parsed = parse_model_json(raw, field_name="outer_json", expected_type=dict)
        self.assertEqual(parsed["dispatches"], [])
        self.assertIn("```html", parsed["final_report"])


@pytest.mark.parametrize("raw", [
    {"prompt_tokens": "7", "completion_tokens": 3.0, "prompt_tokens_details": {"cached_tokens": 2}},
    SimpleNamespace(input_tokens=7, output_tokens=3, cache_read_input_tokens=2),
])
def test_normalization_preserves_provider_details_and_is_idempotent(raw: object) -> None:
    """SDK 对象和字典均能归一化，重复转换保留 provider 原始详情。"""
    normalized = normalize_token_usage(raw)
    assert normalized["input_tokens"] == 7
    assert normalized["output_tokens"] == 3
    assert normalized["total_tokens"] == 10
    assert normalized["provider_usage"]
    assert normalize_token_usage(normalized) == normalized


@pytest.mark.parametrize("invalid", [-1, True, float("nan"), float("inf"), -float("inf"), 1.5, "-2"])
def test_invalid_counts_are_omitted_instead_of_becoming_estimates(invalid: object) -> None:
    """无效计数保持未知，避免被负数、非有限值或截断后的小数污染。"""
    normalized = normalize_token_usage({"input_tokens": invalid, "output_tokens": 4})
    assert "input_tokens" not in normalized
    assert "total_tokens" not in normalized
    assert normalized["output_tokens"] == 4
    assert "input_tokens" in normalized["provider_usage"]


def test_missing_usage_and_known_zero_remain_distinguishable() -> None:
    """引擎未给统计与明确返回零是不同状态，不在本地估算 token。"""
    assert normalize_token_usage(None) is None
    assert normalize_token_usage({}) == {"provider_usage": {}}
    assert normalize_token_usage({"input_tokens": 0, "output_tokens": 0}) == {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "provider_usage": {"input_tokens": 0, "output_tokens": 0},
    }


if __name__ == "__main__":
    unittest.main()
