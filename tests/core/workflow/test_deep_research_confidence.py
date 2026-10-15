"""deep_research workflow confidence 字段容错性测试。

测试 _normalize_claim 函数对各种 confidence 格式的处理能力，
包括数字、字符串、中文描述、英文描述和异常值。
"""

from __future__ import annotations

import pytest

from juice_agents.core.workflow.builtins.deep_research import _normalize_claim


class TestNormalizeClaimConfidence:
    """测试 _normalize_claim 对 confidence 字段的容错处理。"""

    def test_numeric_confidence(self) -> None:
        """测试数字类型的 confidence。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": 0.85,
        })
        assert claim is not None
        assert claim["confidence"] == 0.85

    def test_integer_confidence(self) -> None:
        """测试整数类型的 confidence。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": 1,
        })
        assert claim is not None
        assert claim["confidence"] == 1.0

    def test_string_numeric_confidence(self) -> None:
        """测试数字字符串的 confidence。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": "0.75",
        })
        assert claim is not None
        assert claim["confidence"] == 0.75

    def test_chinese_high_confidence(self) -> None:
        """测试中文'极高'描述的 confidence。"""
        claim = _normalize_claim({
            "text": "珠穆朗玛峰海拔8848.86米",
            "source_ids": ["s1"],
            "confidence": "极高",
        })
        assert claim is not None
        assert claim["confidence"] == 0.95
        assert claim["text"] == "珠穆朗玛峰海拔8848.86米"

    def test_chinese_medium_confidence(self) -> None:
        """测试中文'中等'描述的 confidence。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": "中等",
        })
        assert claim is not None
        assert claim["confidence"] == 0.5

    def test_chinese_low_confidence(self) -> None:
        """测试中文'低'描述的 confidence。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": "低",
        })
        assert claim is not None
        assert claim["confidence"] == 0.2

    def test_english_high_confidence(self) -> None:
        """测试英文'high'描述的 confidence。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": "high",
        })
        assert claim is not None
        assert claim["confidence"] == 0.8

    def test_english_medium_confidence(self) -> None:
        """测试英文'medium'描述的 confidence。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": "medium",
        })
        assert claim is not None
        assert claim["confidence"] == 0.5

    def test_english_very_high_confidence(self) -> None:
        """测试英文'very high'描述的 confidence。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": "very high",
        })
        assert claim is not None
        assert claim["confidence"] == 0.95

    def test_unknown_string_confidence(self) -> None:
        """测试无法识别的字符串 confidence，应使用默认值 0.5。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": "unknown",
        })
        assert claim is not None
        assert claim["confidence"] == 0.5

    def test_missing_confidence(self) -> None:
        """测试缺失 confidence 字段，应使用默认值 0.5。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
        })
        assert claim is not None
        assert claim["confidence"] == 0.5

    def test_confidence_out_of_range_high(self) -> None:
        """测试超出上限的 confidence，应裁剪为 1.0。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": 1.5,
        })
        assert claim is not None
        assert claim["confidence"] == 1.0

    def test_confidence_out_of_range_low(self) -> None:
        """测试低于下限的 confidence，应裁剪为 0.0。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": -0.5,
        })
        assert claim is not None
        assert claim["confidence"] == 0.0

    def test_confidence_zero(self) -> None:
        """测试 confidence 为 0。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": 0,
        })
        assert claim is not None
        assert claim["confidence"] == 0.0

    def test_all_chinese_confidence_levels(self) -> None:
        """测试所有中文 confidence 级别的映射。"""
        levels = {
            "极高": 0.95,
            "很高": 0.85,
            "高": 0.8,
            "较高": 0.7,
            "中": 0.5,
            "中等": 0.5,
            "一般": 0.5,
            "较低": 0.3,
            "低": 0.2,
            "很低": 0.1,
            "极低": 0.05,
        }
        for chinese, expected in levels.items():
            claim = _normalize_claim({
                "text": "Test claim",
                "source_ids": ["s1"],
                "confidence": chinese,
            })
            assert claim is not None
            assert claim["confidence"] == expected, f"Failed for '{chinese}'"

    def test_claim_with_all_fields(self) -> None:
        """测试包含所有字段的 claim。"""
        claim = _normalize_claim({
            "text": "Mount Everest is the highest mountain",
            "source_ids": ["src-1", "src-2"],
            "confidence": "极高",
            "conflict_source_ids": ["src-3"],
        })
        assert claim is not None
        assert claim["text"] == "Mount Everest is the highest mountain"
        assert claim["source_ids"] == ["src-1", "src-2"]
        assert claim["confidence"] == 0.95
        assert claim["conflict_source_ids"] == ["src-3"]
        assert claim["status"] == ""
        assert claim["reason"] == ""

    def test_invalid_claim_no_text(self) -> None:
        """测试没有 text 的 claim，应返回 None。"""
        claim = _normalize_claim({
            "source_ids": ["s1"],
            "confidence": 0.8,
        })
        assert claim is None

    def test_invalid_claim_empty_text(self) -> None:
        """测试空 text 的 claim，应返回 None。"""
        claim = _normalize_claim({
            "text": "   ",
            "source_ids": ["s1"],
            "confidence": 0.8,
        })
        assert claim is None

    def test_invalid_claim_not_dict(self) -> None:
        """测试非字典类型的 claim，应返回 None。"""
        claim = _normalize_claim("not a dict")
        assert claim is None

    def test_boolean_confidence_ignored(self) -> None:
        """测试布尔类型的 confidence，应使用默认值。"""
        claim = _normalize_claim({
            "text": "Test claim",
            "source_ids": ["s1"],
            "confidence": True,
        })
        assert claim is not None
        # 布尔值被 isinstance(bool) 排除，使用默认值
        assert claim["confidence"] == 0.5
