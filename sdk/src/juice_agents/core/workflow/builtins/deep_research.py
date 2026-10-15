"""Juice 原生 deep research workflow。

拓扑沿用 open deep-research 的 supervisor/researcher 模式，全部执行由 Juice
WorkflowContext 与临时 agent 完成；prompt 与验证策略是 Juice 自己的。

    brief ──> [plan ──> parallel researchers ──> dedupe ──> vote]* ──> write ──> verify citations

- LLM 只负责 brief / 选题 / 研究 / 写作四种角色。
- 去重、投票、引用校验是确定性代码：verified = 一个权威来源或两个独立来源且无冲突；
  disputed/unverified 只进入 limitations，不进入正文。
- 有未验证的 claim 且未达轮次上限时，以这些 gaps 为输入再规划一轮。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

WORKFLOW_METADATA = {
    "name": "deep_research",
    "description": "Conduct cross-checked research and produce a cited report.",
    "phases": ["Brief", "Research", "Report"],
    "read_only": True,
    "allowed_modes": ["agent", "plan", "team", "group"],
    "tools": ["api_web_search", "web_search", "browser_open_url", "browser_extract", "read", "glob", "grep"],
    "input_schema": {
        "question": "string",
        "source_mode": "web | web_workspace | workspace",
        "scope": "optional string",
    },
    "output_schema": {
        "report_markdown": "string",
        "sources": "list",
        "verified_claims": "list",
        "disputed_claims": "list",
        "assumptions": "list",
        "limitations": "list",
        "stats": "object",
        "artifacts": "object",
    },
}

SOURCE_MODES = {"web", "web_workspace", "workspace"}

DEFAULT_LIMITS = {
    "max_concurrent_research_units": 5,
    "max_research_rounds": 3,
    "max_tool_calls_per_researcher": 10,
    "max_sources": 40,
}

RESEARCH_BRIEF_PROMPT = """
You are a research supervisor. Convert the dynamic user question into a concise
research brief. Keep fixed process instructions here, not in the task input.
Classify complexity as simple, multi_part, or broad. A simple question asks for
one directly verifiable fact; multi_part requires comparison or several related
facts; broad requires open-ended synthesis. Submit JSON with: objective, scope,
complexity, success_criteria, assumptions, limitations.
Do not invent facts or sources.
""".strip()

TOPIC_PLANNER_PROMPT = """
You are a research supervisor. Plan independent research units that collectively
answer the brief and close the listed gaps. Submit JSON object {"topics": [...]},
where each topic has id, question, rationale, and source_mode. Avoid duplicate
angles, especially topics already present in prior_topic_ids. Prefer primary
sources and explicit comparisons. Use the fewest independent topics needed and
never exceed max_topics.
""".strip()

RESEARCHER_PROMPT = """
You are a focused researcher. Research only the assigned topic using available
tools. Search-result snippets are leads, not evidence: open or read sources
before citing them. Submit JSON with summary, sources, and claims.

Each source must contain id, title, location, source_type, authority,
independence_key, and excerpt. Each claim must contain text, source_ids,
confidence (a number between 0.0 and 1.0, where 0.0 means no confidence and
1.0 means absolute certainty), and optional conflict_source_ids. Never fabricate
a source.

Example claim:
{
  "text": "Mount Everest is 8848.86m high",
  "source_ids": ["src-1", "src-2"],
  "confidence": 0.95,
  "conflict_source_ids": []
}
""".strip()

REPORT_WRITER_PROMPT = """
You are a report writer. Write a concise Markdown report using only the provided
verified claims. Cite sources using [source-id]. Put uncertainty and conflicts in
the limitations section. Submit JSON object {"report_markdown": "..."}.
""".strip()

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "scope": {"type": "string"},
        "complexity": {"type": "string", "enum": ["simple", "multi_part", "broad"]},
        "success_criteria": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["objective"],
}

TOPICS_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "rationale": {"type": "string"},
                    "source_mode": {"type": "string"},
                },
                "required": ["question"],
            },
        }
    },
    "required": ["topics"],
}

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "sources": {"type": "array"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "conflict_source_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_ids", "confidence"],
            },
        },
    },
    "required": ["sources", "claims"],
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {"report_markdown": {"type": "string"}},
    "required": ["report_markdown"],
}


class SourceRecord(TypedDict, total=False):
    id: str
    title: str
    location: str
    source_type: str
    authority: bool
    independence_key: str
    excerpt: str
    content_hash: str


class ClaimRecord(TypedDict, total=False):
    text: str
    source_ids: list[str]
    conflict_source_ids: list[str]
    confidence: float
    status: str
    reason: str


def _limits(args: dict[str, Any]) -> dict[str, int]:
    """调用方可在 args.limits 中覆盖默认上限；非正整数一律忽略。"""

    limits = dict(DEFAULT_LIMITS)
    raw = args.get("limits")
    if isinstance(raw, dict):
        for key in limits:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                limits[key] = value
    return limits


def _infer_question_complexity(question: str) -> str:
    """Provide a deterministic fallback when the brief model omits complexity."""

    normalized = " ".join(str(question or "").strip().lower().split())
    multi_part_markers = (
        " compare ",
        " versus ",
        " vs ",
        " difference",
        " differences",
        "比较",
        "对比",
        "区别",
        "分别",
        "以及",
        "和谁",
    )
    broad_markers = (
        "research",
        "analyze",
        "analysis",
        "survey",
        "landscape",
        "comprehensive",
        "调研",
        "分析",
        "综述",
        "全面",
        "现状",
        "趋势",
    )
    padded = f" {normalized} "
    if any(marker in padded for marker in broad_markers):
        return "broad"
    if any(marker in padded for marker in multi_part_markers):
        return "multi_part"
    if len(normalized) > 120:
        return "broad"
    if normalized.count("?") + normalized.count("？") > 1:
        return "multi_part"
    return "simple"


def _topic_limit(*, complexity: str, configured_limit: int) -> int:
    if complexity == "simple":
        return 1
    if complexity == "multi_part":
        return min(3, configured_limit)
    return configured_limit


def _research_tools(source_mode: str) -> list[str]:
    tools: list[str] = []
    if source_mode in {"web", "web_workspace"}:
        tools.extend(["api_web_search", "web_search", "browser_open_url", "browser_extract"])
    if source_mode in {"workspace", "web_workspace"}:
        tools.extend(["read", "glob", "grep"])
    return tools


def _normalize_source(raw: Any) -> SourceRecord | None:
    if not isinstance(raw, dict):
        return None
    location = str(raw.get("location") or raw.get("url") or raw.get("path") or "").strip()
    excerpt = str(raw.get("excerpt") or raw.get("content") or "").strip()
    if not location or not excerpt:
        return None
    source_id = str(raw.get("id") or "").strip() or f"src-{hashlib.sha256(location.encode()).hexdigest()[:10]}"
    return {
        "id": source_id,
        "title": str(raw.get("title") or location).strip(),
        "location": location,
        "source_type": str(raw.get("source_type") or "unknown").strip(),
        "authority": bool(raw.get("authority")),
        "independence_key": str(raw.get("independence_key") or location).strip(),
        "excerpt": excerpt,
        "content_hash": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
    }


def _normalize_claim(raw: Any) -> ClaimRecord | None:
    """归一化 claim 并容错处理 confidence 字段。

    confidence 字段支持：
    - 数字（int/float）：直接使用
    - 数字字符串："0.9" -> 0.9
    - 中文描述："极高" -> 0.9, "高" -> 0.8, "中等" -> 0.5, "低" -> 0.3
    - 英文描述："high" -> 0.8, "medium" -> 0.5, "low" -> 0.3
    - 无效值或缺失：默认 0.5
    """
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()
    if not text:
        return None

    # 容错处理 confidence 字段
    confidence = 0.5  # 默认值（中等信心）
    raw_confidence = raw.get("confidence")

    if isinstance(raw_confidence, (int, float)) and not isinstance(raw_confidence, bool):
        # 数字类型直接转换
        confidence = float(raw_confidence)
    elif isinstance(raw_confidence, str):
        raw_str = raw_confidence.strip()
        # 尝试转换为数字
        try:
            confidence = float(raw_str)
        except ValueError:
            # 中文/英文描述映射
            confidence_map = {
                # 中文
                "极高": 0.95, "很高": 0.85, "高": 0.8, "较高": 0.7,
                "中": 0.5, "中等": 0.5, "一般": 0.5,
                "较低": 0.3, "低": 0.2, "很低": 0.1, "极低": 0.05,
                # 英文
                "very high": 0.95, "high": 0.8, "medium": 0.5,
                "low": 0.3, "very low": 0.1,
            }
            normalized_key = raw_str.lower()
            if normalized_key in confidence_map:
                confidence = confidence_map[normalized_key]
                logger.warning(
                    "Researcher returned non-numeric confidence '%s', mapped to %.2f",
                    raw_confidence, confidence
                )
            else:
                logger.warning(
                    "Researcher returned unrecognized confidence '%s', using default 0.5",
                    raw_confidence
                )

    # 确保在有效范围 [0.0, 1.0] 内
    confidence = max(0.0, min(1.0, confidence))

    return {
        "text": text,
        "source_ids": [str(item) for item in list(raw.get("source_ids") or []) if str(item).strip()],
        "conflict_source_ids": [
            str(item) for item in list(raw.get("conflict_source_ids") or []) if str(item).strip()
        ],
        "confidence": confidence,
        "status": "",
        "reason": "",
    }


def dedupe(research_results: list[dict[str, Any]], *, max_sources: int) -> dict[str, Any]:
    """按 location / 内容哈希合并来源，按归一化文本合并 claim，并把 claim 的来源 id 重映射到保留的来源。"""

    selected: list[SourceRecord] = []
    source_aliases: dict[str, str] = {}
    seen_locations: dict[str, SourceRecord] = {}
    seen_hashes: dict[str, SourceRecord] = {}
    raw_claims: list[ClaimRecord] = []
    for result in research_results:
        for raw_source in list(result.get("sources") or []):
            source = _normalize_source(raw_source)
            if source is None:
                continue
            existing = seen_locations.get(str(source["location"])) or seen_hashes.get(str(source["content_hash"]))
            if existing is not None:
                source_aliases[str(source["id"])] = str(existing["id"])
                continue
            selected.append(source)
            seen_locations[str(source["location"])] = source
            seen_hashes[str(source["content_hash"])] = source
            source_aliases[str(source["id"])] = str(source["id"])
        for raw_claim in list(result.get("claims") or []):
            claim = _normalize_claim(raw_claim)
            if claim is not None:
                raw_claims.append(claim)
    sources = selected[:max_sources]
    allowed_ids = {str(item["id"]) for item in sources}
    claims_by_text: dict[str, ClaimRecord] = {}
    for claim in raw_claims:
        key = " ".join(str(claim["text"]).lower().split())
        current = claims_by_text.setdefault(key, dict(claim))  # type: ignore[arg-type]
        support_ids = {
            source_aliases.get(item, item)
            for item in list(current.get("source_ids", [])) + list(claim.get("source_ids", []))
        }
        conflict_ids = {
            source_aliases.get(item, item)
            for item in list(current.get("conflict_source_ids", [])) + list(claim.get("conflict_source_ids", []))
        }
        current["source_ids"] = sorted(item for item in support_ids if item in allowed_ids)
        current["conflict_source_ids"] = sorted(item for item in conflict_ids if item in allowed_ids)
        current["confidence"] = max(float(current.get("confidence") or 0.0), float(claim.get("confidence") or 0.0))
    return {"sources": sources, "claims": list(claims_by_text.values())}


def vote(sources: list[SourceRecord], claims: list[ClaimRecord]) -> dict[str, Any]:
    """verified：一个权威来源，或两个独立来源且无冲突；有冲突为 disputed；其余 unverified。"""

    by_id = {str(item.get("id") or ""): item for item in sources}
    verified: list[ClaimRecord] = []
    disputed: list[ClaimRecord] = []
    unverified: list[ClaimRecord] = []
    for raw_claim in claims:
        claim = dict(raw_claim)
        supports = [by_id[item] for item in claim.get("source_ids", []) if item in by_id]
        conflicts = [by_id[item] for item in claim.get("conflict_source_ids", []) if item in by_id]
        independent = {str(item.get("independence_key") or item.get("location") or "") for item in supports}
        authoritative = any(bool(item.get("authority")) for item in supports)
        if conflicts:
            claim.update(status="disputed", reason="Reliable sources conflict")
            disputed.append(claim)  # type: ignore[arg-type]
        elif authoritative or len(independent) >= 2:
            claim.update(
                status="verified",
                reason="Supported by an authoritative source" if authoritative else "Supported by independent sources",
            )
            verified.append(claim)  # type: ignore[arg-type]
        else:
            claim.update(status="unverified", reason="Insufficient readable independent sources")
            unverified.append(claim)  # type: ignore[arg-type]
    return {
        "verified_claims": verified,
        "disputed_claims": disputed,
        "unverified_claims": unverified,
        "gaps": [str(item.get("text") or "") for item in unverified[:5]],
    }


def verify_citations(report: str, verified_claims: list[ClaimRecord]) -> tuple[str, list[str]]:
    """删除报告中不被任何 verified claim 支持的引用 id，返回 (清理后报告, 新增 limitations)。"""

    supported_ids = {str(source_id) for claim in verified_claims for source_id in list(claim.get("source_ids") or [])}
    cited = set(re.findall(r"\[([A-Za-z0-9._:-]+)\]", report))
    invalid = sorted(item for item in cited if item not in supported_ids)
    limitations: list[str] = []
    if invalid:
        limitations.append(f"Removed or unresolved citation ids: {', '.join(invalid)}")
        for item in invalid:
            report = report.replace(f"[{item}]", "")
    return report, limitations


def _plan_topics(
    ctx: Any,
    *,
    question: str,
    source_mode: str,
    brief: dict[str, Any],
    complexity: str,
    gaps: list[str],
    prior_topics: list[dict[str, Any]],
    sources: list[SourceRecord],
    round_number: int,
    limits: dict[str, int],
) -> list[dict[str, Any]]:
    max_topics = _topic_limit(complexity=complexity, configured_limit=limits["max_concurrent_research_units"])
    planned = ctx.agent(
        {
            "brief": brief,
            "gaps": gaps,
            "prior_topic_ids": [str(item.get("id") or "") for item in prior_topics],
            "known_source_locations": [str(item.get("location") or "") for item in sources[: limits["max_sources"]]],
            "round": round_number,
            "source_mode": source_mode,
            "complexity": complexity,
            "max_topics": max_topics,
        },
        instructions=TOPIC_PLANNER_PROMPT,
        schema=TOPICS_SCHEMA,
        phase="Research",
        label="plan",
        tools=[],
        max_steps=2,
    )
    topics = [dict(item) for item in list(planned.get("topics") or []) if isinstance(item, dict)][:max_topics]
    if not topics:
        # 模型没给出选题时，直接研究原问题，保证每轮至少有一个 researcher。
        topics = [
            {
                "id": f"round-{round_number}-primary",
                "question": question,
                "rationale": "Directly answer the research question",
                "source_mode": source_mode,
            }
        ]
    for index, topic in enumerate(topics):
        topic.setdefault("id", f"round-{round_number}-{index + 1}")
        # 选题的 source_mode 不能超出用户请求的范围。
        if topic.get("source_mode") not in SOURCE_MODES or (
            source_mode != "web_workspace" and topic.get("source_mode") != source_mode
        ):
            topic["source_mode"] = source_mode
    return topics


def run(ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
    if "source" in args:
        raise ValueError("deep_research 使用 source_mode，不支持 source")
    question = str(args.get("question") or "").strip()
    source_mode = str(args.get("source_mode") or "web").strip()
    scope = str(args.get("scope") or "").strip()
    if not question:
        raise ValueError("deep_research.question 不能为空")
    if source_mode not in SOURCE_MODES:
        raise ValueError(f"source_mode 不支持 {source_mode!r}，可选值: {sorted(SOURCE_MODES)}")
    limits = _limits(args)

    brief = ctx.agent(
        {"question": question, "requested_scope": scope, "source_mode": source_mode},
        instructions=RESEARCH_BRIEF_PROMPT,
        schema=BRIEF_SCHEMA,
        phase="Brief",
        label="brief",
        tools=[],
        max_steps=2,
    )
    complexity = str(brief.get("complexity") or "").strip()
    if complexity not in {"simple", "multi_part", "broad"}:
        complexity = _infer_question_complexity(question)
    brief["complexity"] = complexity
    assumptions = [str(item) for item in list(brief.get("assumptions") or [])]
    limitations = [str(item) for item in list(brief.get("limitations") or [])]

    research_results: list[dict[str, Any]] = []
    topics: list[dict[str, Any]] = []
    merged: dict[str, Any] = {"sources": [], "claims": []}
    voted: dict[str, Any] = {"verified_claims": [], "disputed_claims": [], "unverified_claims": [], "gaps": []}
    rounds = 0
    for round_number in range(1, limits["max_research_rounds"] + 1):
        rounds = round_number
        round_topics = _plan_topics(
            ctx,
            question=question,
            source_mode=source_mode,
            brief=brief,
            complexity=complexity,
            gaps=list(voted["gaps"]),
            prior_topics=topics,
            sources=merged["sources"],
            round_number=round_number,
            limits=limits,
        )
        topics.extend(round_topics)
        research_results.extend(
            ctx.parallel(
                [
                    lambda topic=topic: ctx.agent(
                        {"topic": topic, "brief": brief, "known_gaps": list(voted["gaps"])},
                        instructions=RESEARCHER_PROMPT,
                        tools=_research_tools(str(topic["source_mode"])),
                        schema=RESEARCH_SCHEMA,
                        phase="Research",
                        label="researcher",
                        max_steps=limits["max_tool_calls_per_researcher"],
                    )
                    for topic in round_topics
                ]
            )
        )
        merged = dedupe(research_results, max_sources=limits["max_sources"])
        voted = vote(merged["sources"], merged["claims"])
        if not voted["gaps"]:
            break

    written = ctx.agent(
        {
            "question": question,
            "brief": brief,
            "verified_claims": voted["verified_claims"],
            "sources": merged["sources"],
            "disputed_claims": voted["disputed_claims"],
            "limitations": limitations,
        },
        instructions=REPORT_WRITER_PROMPT,
        schema=REPORT_SCHEMA,
        phase="Report",
        label="writer",
        tools=[],
        max_steps=3,
    )
    report, citation_limitations = verify_citations(
        str(written.get("report_markdown") or "").strip(), voted["verified_claims"]
    )
    limitations.extend(citation_limitations)
    limitations.extend(f"Disputed claim: {item.get('text', '')}" for item in voted["disputed_claims"])

    claims_payload = {
        "verified": voted["verified_claims"],
        "disputed": voted["disputed_claims"],
        "unverified": voted["unverified_claims"],
    }
    artifacts = {
        "report": str(ctx.artifact("report.md", report + "\n")),
        "sources": str(ctx.artifact("sources.json", merged["sources"])),
        "claims": str(ctx.artifact("claims.json", claims_payload)),
        "debug": str(
            ctx.artifact(
                "debug.json",
                {"question": question, "source_mode": source_mode, "rounds": rounds, "topics": topics, "gaps": voted["gaps"]},
            )
        ),
    }
    return {
        "report_markdown": report,
        "sources": merged["sources"],
        "verified_claims": voted["verified_claims"],
        "disputed_claims": voted["disputed_claims"],
        "assumptions": assumptions,
        "limitations": limitations,
        "stats": {
            "rounds": rounds,
            "research_units": len(research_results),
            "sources": len(merged["sources"]),
            "verified_claims": len(voted["verified_claims"]),
            "disputed_claims": len(voted["disputed_claims"]),
        },
        "artifacts": artifacts,
    }


__all__ = ["WORKFLOW_METADATA", "ClaimRecord", "SourceRecord", "dedupe", "run", "verify_citations", "vote"]
