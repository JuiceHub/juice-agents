import unittest

from juice_agents.core.utils import parse_model_json


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


if __name__ == "__main__":
    unittest.main()
