"""CompositeModel: 多模型候选生成、裁判选择、综合与迭代修正。"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from juice_agents.core.runner.execution.cancellation import raise_if_cancelled
from juice_agents.core.models.models import normalize_token_usage
from juice_agents.core.utils import parse_model_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ModelMember:
    """Composite 内部成员模型。

    name 只用于日志、裁判提示和错误定位；model 是任何实现 generate(...) 的对象。
    """

    name: str
    model: Any


@dataclass(frozen=True)
class _CandidateResult:
    """候选模型的一次成功输出。"""

    candidate_id: str
    name: str
    response: dict[str, Any]


class _UsageCollector:
    """收集 Composite 内部每次成功模型调用的 token usage。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.totals: dict[str, int] = {}

    @staticmethod
    def normalize_usage(usage: Any) -> dict[str, Any] | None:
        if usage is None:
            return None
        if isinstance(usage, dict) and ("provider_usage" in usage or "calls" in usage):
            normalized = dict(usage)
            if "total_tokens" not in normalized:
                input_tokens = normalized.get("input_tokens")
                output_tokens = normalized.get("output_tokens")
                if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                    normalized["total_tokens"] = input_tokens + output_tokens
            return normalized
        return normalize_token_usage(usage)

    def record(self, *, phase: str, name: str, response: dict[str, Any]) -> None:
        usage = self.normalize_usage(response.get("usage"))
        if not usage:
            return
        self.calls.append({"phase": phase, "name": name, "usage": usage})
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                self.totals[key] = self.totals.get(key, 0) + value

    def summary(self) -> dict[str, Any] | None:
        if not self.calls:
            return None
        usage: dict[str, Any] = dict(self.totals)
        usage["calls"] = list(self.calls)
        return usage


class CompositeModel:
    """按策略组合多个模型输出的模型实现。

    该类刻意保持与 provider 模型一致的 generate(...) 契约，方便被 Runner、
    session compression、goal evaluator 等任何模型消费者直接使用。
    """

    SUPPORTED_STRATEGIES = {"judge_select", "synthesize", "iterative_refine"}

    def __init__(
        self,
        *,
        strategy: str,
        candidates: Iterable[Any] | None = None,
        judge_model: Any | None = None,
        synthesis_model: Any | None = None,
        generator_model: Any | None = None,
        reviewer_models: Iterable[Any] | None = None,
        fail_fast: bool = False,
        max_refine_rounds: int = 2,
        model_name: str = "composite",
    ) -> None:
        normalized_strategy = str(strategy or "").strip().lower()
        if normalized_strategy not in self.SUPPORTED_STRATEGIES:
            raise ValueError(
                f"CompositeModel strategy 不合法: {strategy}. "
                f"可选值: {', '.join(sorted(self.SUPPORTED_STRATEGIES))}"
            )
        if max_refine_rounds < 0:
            raise ValueError("max_refine_rounds 必须大于等于 0")

        self.strategy = normalized_strategy
        self.candidates = self._normalize_members(candidates or [], default_prefix="candidate")
        self.judge_model = judge_model
        self.synthesis_model = synthesis_model
        self.generator_model = generator_model
        self.reviewer_models = self._normalize_members(reviewer_models or [], default_prefix="reviewer")
        self.fail_fast = bool(fail_fast)
        self.max_refine_rounds = int(max_refine_rounds)
        self.model_name = model_name
        self._validate_strategy_config()

    def _validate_strategy_config(self) -> None:
        if self.strategy in {"judge_select", "synthesize"} and not self.candidates:
            raise ValueError(f"{self.strategy} 策略必须配置 candidates")
        if self.strategy == "judge_select" and self.judge_model is None:
            raise ValueError("judge_select 策略必须配置 judge_model")
        if self.strategy == "synthesize" and self.synthesis_model is None:
            raise ValueError("synthesize 策略必须配置 synthesis_model")
        if self.strategy == "iterative_refine":
            if self.generator_model is None:
                raise ValueError("iterative_refine 策略必须配置 generator_model")
            if not self.reviewer_models:
                raise ValueError("iterative_refine 策略必须配置 reviewer_models")

    @classmethod
    def _normalize_members(cls, members: Iterable[Any], *, default_prefix: str) -> list[_ModelMember]:
        normalized: list[_ModelMember] = []
        for index, item in enumerate(members, start=1):
            if isinstance(item, _ModelMember):
                normalized.append(item)
                continue
            if isinstance(item, dict):
                model = item.get("model")
                name = str(item.get("name") or getattr(model, "model_name", "") or f"{default_prefix}_{index}")
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                name = str(item[0] or f"{default_prefix}_{index}")
                model = item[1]
            else:
                model = item
                name = str(getattr(model, "model_name", "") or f"{default_prefix}_{index}")
            cls._require_generate_model(model, name)
            normalized.append(_ModelMember(name=name, model=model))
        return normalized

    @staticmethod
    def _require_generate_model(model: Any, name: str) -> None:
        if not callable(getattr(model, "generate", None)):
            raise TypeError(f"模型成员 {name} 必须实现 generate(...)")

    @staticmethod
    def _normalize_response(response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            return {
                "role": str(response.get("role") or "assistant"),
                "content": str(response.get("content") or response.get("output") or ""),
                "reasoning_content": str(response.get("reasoning_content") or ""),
                "usage": _UsageCollector.normalize_usage(response.get("usage")),
            }
        return {"role": "assistant", "content": str(response or ""), "reasoning_content": "", "usage": None}

    @staticmethod
    def _attach_usage_summary(response: dict[str, Any], usage_collector: _UsageCollector) -> dict[str, Any]:
        result = dict(response)
        result["usage"] = usage_collector.summary()
        return result

    @staticmethod
    def _content(response: dict[str, Any]) -> str:
        return str(response.get("content") or "")

    @staticmethod
    def _serialize_messages(messages: list[dict[str, Any]]) -> str:
        return json.dumps(messages, ensure_ascii=False, default=str, indent=2)

    def _call_model(
        self,
        model: Any,
        messages: list[dict[str, Any]],
        stop_sequence: list[str] | None,
        cancel_event: threading.Event | None,
        *,
        usage_collector: _UsageCollector,
        phase: str,
        name: str,
    ) -> dict[str, Any]:
        raise_if_cancelled(cancel_event)
        response = model.generate(messages, stop_sequence=stop_sequence, cancel_event=cancel_event)
        raise_if_cancelled(cancel_event)
        normalized = self._normalize_response(response)
        usage_collector.record(phase=phase, name=name, response=normalized)
        return normalized

    def _generate_candidates(
        self,
        messages: list[dict[str, Any]],
        stop_sequence: list[str] | None,
        cancel_event: threading.Event | None,
        usage_collector: _UsageCollector,
    ) -> list[_CandidateResult]:
        results: list[_CandidateResult] = []
        failures: list[str] = []
        for index, member in enumerate(self.candidates, start=1):
            try:
                response = self._call_model(
                    member.model,
                    messages,
                    stop_sequence,
                    cancel_event,
                    usage_collector=usage_collector,
                    phase="candidate",
                    name=member.name,
                )
            except Exception as exc:
                if self.fail_fast:
                    raise RuntimeError(f"候选模型 {member.name} 生成失败") from exc
                logger.warning("CompositeModel 候选模型生成失败，已跳过: name=%s error=%s", member.name, exc)
                failures.append(f"{member.name}: {exc}")
                continue
            results.append(
                _CandidateResult(
                    candidate_id=f"candidate_{index}",
                    name=member.name,
                    response=response,
                )
            )

        if not results:
            detail = "; ".join(failures) if failures else "未配置可用候选模型"
            raise RuntimeError(f"CompositeModel 没有成功候选输出: {detail}")
        return results

    def _candidate_payload(self, candidates: list[_CandidateResult]) -> list[dict[str, Any]]:
        return [
            {
                "id": candidate.candidate_id,
                "name": candidate.name,
                "content": self._content(candidate.response),
            }
            for candidate in candidates
        ]

    def _generate_with_judge(
        self,
        messages: list[dict[str, Any]],
        stop_sequence: list[str] | None,
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        usage_collector = _UsageCollector()
        candidates = self._generate_candidates(messages, stop_sequence, cancel_event, usage_collector)
        system = (
            "You are a strict response judge. Score every candidate against the original "
            "conversation and choose the single best final answer. Return only JSON with "
            'this shape: {"winner":"candidate_1","scores":[{"id":"candidate_1","score":0.0,"reason":"..."}]}.'
        )
        user = json.dumps(
            {
                "original_messages": messages,
                "candidates": self._candidate_payload(candidates),
            },
            ensure_ascii=False,
            default=str,
            indent=2,
        )
        judge_response = self._call_model(
            self.judge_model,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            stop_sequence,
            cancel_event,
            usage_collector=usage_collector,
            phase="judge",
            name=getattr(self.judge_model, "model_name", "judge"),
        )
        verdict = parse_model_json(self._content(judge_response), field_name="composite judge", expected_type=dict)
        winner = str(verdict.get("winner") or verdict.get("selected") or "").strip()
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        if winner not in by_id:
            raise ValueError(f"裁判模型返回了不存在的 winner: {winner}")
        selected = by_id[winner]
        logger.info("CompositeModel 裁判选择候选: composite=%s winner=%s", self.model_name, selected.name)
        return self._attach_usage_summary(selected.response, usage_collector)

    def _generate_with_synthesis(
        self,
        messages: list[dict[str, Any]],
        stop_sequence: list[str] | None,
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        usage_collector = _UsageCollector()
        candidates = self._generate_candidates(messages, stop_sequence, cancel_event, usage_collector)
        system = (
            "You are the final response model. Use the original conversation and all candidate "
            "answers to produce one concise, correct final answer. Do not mention candidate ids."
        )
        user = json.dumps(
            {
                "original_messages": messages,
                "candidate_answers": self._candidate_payload(candidates),
            },
            ensure_ascii=False,
            default=str,
            indent=2,
        )
        response = self._call_model(
            self.synthesis_model,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            stop_sequence,
            cancel_event,
            usage_collector=usage_collector,
            phase="synthesis",
            name=getattr(self.synthesis_model, "model_name", "synthesis"),
        )
        return self._attach_usage_summary(response, usage_collector)

    def _review_current_answer(
        self,
        reviewer: _ModelMember,
        original_messages: list[dict[str, Any]],
        current_response: dict[str, Any],
        stop_sequence: list[str] | None,
        cancel_event: threading.Event | None,
        usage_collector: _UsageCollector,
    ) -> dict[str, Any]:
        system = (
            "You are a strict answer reviewer. Decide whether the answer satisfies the original "
            "conversation. Return only JSON with this shape: "
            '{"approved":true,"critique":"short reason"}.'
        )
        user = json.dumps(
            {
                "original_messages": original_messages,
                "answer": self._content(current_response),
            },
            ensure_ascii=False,
            default=str,
            indent=2,
        )
        review_response = self._call_model(
            reviewer.model,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            stop_sequence,
            cancel_event,
            usage_collector=usage_collector,
            phase="review",
            name=reviewer.name,
        )
        verdict = parse_model_json(
            self._content(review_response),
            field_name=f"composite reviewer {reviewer.name}",
            expected_type=dict,
        )
        if "approved" not in verdict:
            raise ValueError(f"评审模型 {reviewer.name} 未返回 approved 字段")
        return verdict

    def _refine_answer(
        self,
        original_messages: list[dict[str, Any]],
        current_response: dict[str, Any],
        critique: str,
        stop_sequence: list[str] | None,
        cancel_event: threading.Event | None,
        usage_collector: _UsageCollector,
    ) -> dict[str, Any]:
        system = (
            "You are revising your previous answer. Use the reviewer critique to produce a "
            "better final answer for the original conversation."
        )
        user = json.dumps(
            {
                "original_messages": original_messages,
                "previous_answer": self._content(current_response),
                "reviewer_critique": critique,
            },
            ensure_ascii=False,
            default=str,
            indent=2,
        )
        return self._call_model(
            self.generator_model,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            stop_sequence,
            cancel_event,
            usage_collector=usage_collector,
            phase="refine",
            name=getattr(self.generator_model, "model_name", "generator"),
        )

    def _generate_with_iterative_refine(
        self,
        messages: list[dict[str, Any]],
        stop_sequence: list[str] | None,
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        usage_collector = _UsageCollector()
        current = self._call_model(
            self.generator_model,
            messages,
            stop_sequence,
            cancel_event,
            usage_collector=usage_collector,
            phase="generate",
            name=getattr(self.generator_model, "model_name", "generator"),
        )
        for round_index in range(self.max_refine_rounds + 1):
            rejected_by = ""
            critique = ""
            for reviewer in self.reviewer_models:
                verdict = self._review_current_answer(
                    reviewer,
                    messages,
                    current,
                    stop_sequence,
                    cancel_event,
                    usage_collector,
                )
                if bool(verdict.get("approved")):
                    continue
                rejected_by = reviewer.name
                critique = str(verdict.get("critique") or "answer rejected").strip()
                break
            if not rejected_by:
                logger.info("CompositeModel 迭代评审通过: composite=%s rounds=%s", self.model_name, round_index)
                return self._attach_usage_summary(current, usage_collector)
            if round_index >= self.max_refine_rounds:
                raise RuntimeError(
                    f"CompositeModel 迭代修正超过最大轮次: reviewer={rejected_by} critique={critique}"
                )
            logger.info(
                "CompositeModel 评审拒绝，开始修正: composite=%s reviewer=%s round=%s",
                self.model_name,
                rejected_by,
                round_index + 1,
            )
            current = self._refine_answer(
                messages,
                current,
                critique,
                stop_sequence,
                cancel_event,
                usage_collector,
            )

        raise RuntimeError("CompositeModel 迭代修正未知错误")

    def generate(
        self,
        messages: list[dict[str, Any]],
        stop_sequence: list[str] | None = None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not isinstance(messages, list):
            raise TypeError("messages 必须为 list[dict]")
        if stop_sequence is not None and not isinstance(stop_sequence, list):
            raise TypeError("stop_sequence 必须为 list[str]")
        raise_if_cancelled(cancel_event)
        if self.strategy == "judge_select":
            return self._generate_with_judge(messages, stop_sequence, cancel_event)
        if self.strategy == "synthesize":
            return self._generate_with_synthesis(messages, stop_sequence, cancel_event)
        return self._generate_with_iterative_refine(messages, stop_sequence, cancel_event)


__all__ = ["CompositeModel"]
