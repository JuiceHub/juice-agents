"""Juice native deep-research graph.

The topology follows the supervisor/researcher pattern popularized by open
deep-research implementations, while all execution uses Juice StateGraph and
Juice agents. Prompts and verification policy are intentionally Juice-specific.
"""

from __future__ import annotations

import hashlib
import json
import logging
import operator
import re
from pathlib import Path
from typing import Annotated, Any, TypedDict

from juice_agents.core.graph import END, Command, Send, StateGraph
from juice_agents.core.graph.types import CompiledPayloadGraph, GraphBuildContext
from juice_agents.core.registry.agents.types import AgentConfig, normalize_agent_type
from juice_agents.core.utils import parse_model_json

logger = logging.getLogger(__name__)

GRAPH_METADATA = {
    "name": "deep_research",
    "description": "Conduct cross-checked research and produce a cited report.",
    "read_only": True,
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
        "artifact_path": "string",
        "sources_path": "string",
        "claims_path": "string",
        "debug_path": "string",
    },
}

SOURCE_MODES = {"web", "web_workspace", "workspace"}

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
confidence, and optional conflict_source_ids. Never fabricate a source.
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
        "claims": {"type": "array"},
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


class ResearchState(TypedDict, total=False):
    question: str
    source_mode: str
    scope: str
    complexity: str
    brief: dict[str, Any]
    topics: list[dict[str, Any]]
    active_topic: dict[str, Any]
    research_results: Annotated[list[dict[str, Any]], operator.add]
    sources: list[SourceRecord]
    claims: list[ClaimRecord]
    verified_claims: list[ClaimRecord]
    disputed_claims: list[ClaimRecord]
    unverified_claims: list[ClaimRecord]
    gaps: list[str]
    round: int
    report_markdown: str
    assumptions: list[str]
    limitations: list[str]
    stats: dict[str, Any]
    artifact_path: str
    sources_path: str
    claims_path: str
    debug_path: str
    debug_events: Annotated[list[dict[str, Any]], operator.add]


def _limits(context: GraphBuildContext) -> dict[str, int]:
    defaults = {
        "max_concurrent_research_units": 5,
        "max_research_rounds": 3,
        "max_tool_calls_per_researcher": 10,
        "max_sources": 40,
    }
    try:
        raw = context.config_context.read_merged_config().get("graphs", {}).get("deep_research", {})
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        for key in defaults:
            if isinstance(raw.get(key), int) and int(raw[key]) > 0:
                defaults[key] = int(raw[key])
    return defaults


def _context_agent_type(context: GraphBuildContext) -> str:
    """Resolve the agent protocol inherited by graph-internal structured agents."""

    configured = dict(getattr(context, "configurable", {}) or {}).get("agent_type")
    if configured:
        return normalize_agent_type(configured)
    dispatcher = context.agent_dispatcher
    owner_name = str(getattr(context, "owner_agent_name", "") or "").strip()
    if dispatcher is not None and owner_name:
        try:
            resolved_type = dispatcher.agent_type_for(owner_name)
            if resolved_type:
                return normalize_agent_type(resolved_type)
        except Exception:
            logger.debug("无法从 graph owner 推断 agent_type: owner=%s", owner_name, exc_info=True)
    return "react"


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


def _structured_agent(
    context: GraphBuildContext,
    *,
    role: str,
    system_prompt: str,
    task: dict[str, Any],
    output_schema: dict[str, Any],
    tools: list[str],
    max_steps: int,
) -> dict[str, Any]:
    callback = context.callbacks.get("structured_agent")
    if callable(callback):
        result = callback(
            role=role,
            system_prompt=system_prompt,
            task=dict(task),
            output_schema=dict(output_schema),
            tools=list(tools),
            max_steps=max_steps,
        )
        parsed = dict(parse_model_json(result, field_name=f"{role} output", expected_type=dict))
        parsed.setdefault("_debug", {"role": role, "runner_id": "", "mode": "callback"})
        return parsed

    config = AgentConfig.from_dict(
        {
            "name": role,
            "description": f"deep_research {role}",
            "agent_type": _context_agent_type(context),
            # Keep the standard ReAct/CodeAct protocol prompt intact. The role
            # prompt is dynamic workflow context, while submit_output/schema
            # enforcement comes from the standard agent runtime.
            "instructions": (
                f"{system_prompt}\n\n"
                "Finish by calling submit_output exactly once with a JSON object "
                "that conforms to the output_schema. Do not return bare JSON outside "
                "the submit_output tool."
            ),
            "max_steps": max_steps,
            "tools": tools,
            "enable_skill_tools": False,
            "output_schema": output_schema,
        }
    )
    dispatcher = context.agent_dispatcher
    if dispatcher is None:
        raise RuntimeError("deep_research 调用真实 Agent 时必须绑定 Agent 调度能力")
    owner_agent_name = str(context.owner_agent_name or dispatcher.root_agent_name).strip()
    logger.info("deep_research role start: role=%s runner=%s", role, dispatcher.runner_id)
    invocation = dispatcher.invoke_agent(
        {
            "agent_ref": config,
            "task": json.dumps(task, ensure_ascii=False),
            "owner_agent_name": owner_agent_name,
            "execution": "sync",
            "lifecycle": "functional",
            "agent_role": role,
            "max_steps": max_steps,
            "model": context.model,
            "metadata": {
                "graph_name": "deep_research",
                "graph_run_id": context.graph_run_id,
                "graph_role": role,
            },
        }
    )
    raw = invocation.get("output")
    parsed = dict(parse_model_json(raw, field_name=f"{role} output", expected_type=dict))
    parsed["_debug"] = {
        "role": role,
        "runner_id": dispatcher.runner_id,
        "agent_name": str(invocation.get("agent_name") or ""),
        "mode": "runner",
    }
    logger.info("deep_research role completed: role=%s runner=%s", role, dispatcher.runner_id)
    return parsed


def _without_debug(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload)
    cleaned.pop("_debug", None)
    return cleaned


def _debug_event(role: str, task: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    debug = dict(result.get("_debug") or {})
    return {
        "role": role,
        "runner_id": str(debug.get("runner_id") or ""),
        "mode": str(debug.get("mode") or ""),
        "task": dict(task),
        "output": _without_debug(result),
    }


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
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()
    if not text:
        return None
    return {
        "text": text,
        "source_ids": [str(item) for item in list(raw.get("source_ids") or []) if str(item).strip()],
        "conflict_source_ids": [
            str(item) for item in list(raw.get("conflict_source_ids") or []) if str(item).strip()
        ],
        "confidence": float(raw.get("confidence") or 0.0),
        "status": "",
        "reason": "",
    }


def build_graph(context: GraphBuildContext) -> CompiledPayloadGraph:
    limits = _limits(context)
    graph = StateGraph(ResearchState)

    def preflight(state: ResearchState) -> dict[str, Any]:
        question = str(state.get("question") or "").strip()
        source_mode = str(state.get("source_mode") or "web").strip()
        if not question:
            raise ValueError("deep_research.question 不能为空")
        if source_mode not in SOURCE_MODES:
            raise ValueError(f"source_mode 不支持 {source_mode!r}，可选值: {sorted(SOURCE_MODES)}")
        if source_mode in {"workspace", "web_workspace"} and context.workspace_dir is None:
            raise ValueError("workspace 研究需要 GraphBuildContext.workspace_dir")
        if not callable(context.callbacks.get("structured_agent")):
            from juice_agents.core.registry.tools import ToolRegistry

            available = set(ToolRegistry(config_context=context.config_context).list())
            missing_groups: list[str] = []
            if source_mode in {"web", "web_workspace"}:
                if not available.intersection({"api_web_search", "web_search"}):
                    missing_groups.append("web search (api_web_search or web_search)")
                if not available.intersection({"browser_open_url", "browser_extract"}):
                    missing_groups.append("web source reader (browser_open_url or browser_extract)")
            if source_mode in {"workspace", "web_workspace"}:
                missing = sorted({"read", "glob", "grep"} - available)
                if missing:
                    missing_groups.append(f"workspace readers ({', '.join(missing)})")
            if missing_groups:
                raise ValueError(f"deep_research 缺少来源工具: {'; '.join(missing_groups)}")
        return {"source_mode": source_mode, "limitations": []}

    def write_research_brief(state: ResearchState) -> dict[str, Any]:
        task = {
            "question": state["question"],
            "requested_scope": state.get("scope", ""),
            "source_mode": state["source_mode"],
        }
        result = _structured_agent(
            context,
            role="deep_research_brief",
            system_prompt=RESEARCH_BRIEF_PROMPT,
            task=task,
            output_schema=BRIEF_SCHEMA,
            tools=[],
            max_steps=2,
        )
        brief = _without_debug(result)
        complexity = str(brief.get("complexity") or "").strip()
        if complexity not in {"simple", "multi_part", "broad"}:
            complexity = _infer_question_complexity(state["question"])
        brief["complexity"] = complexity
        return {
            "brief": brief,
            "complexity": complexity,
            "assumptions": [str(item) for item in list(brief.get("assumptions") or [])],
            "limitations": list(state.get("limitations") or [])
            + [str(item) for item in list(brief.get("limitations") or [])],
            "round": 0,
            "debug_events": [_debug_event("deep_research_brief", task, result)],
        }

    def plan_research_topics(state: ResearchState) -> dict[str, Any]:
        round_number = int(state.get("round") or 0) + 1
        max_topics = _topic_limit(
            complexity=str(state.get("complexity") or "simple"),
            configured_limit=limits["max_concurrent_research_units"],
        )
        task = {
            "brief": dict(state.get("brief") or {}),
            "gaps": list(state.get("gaps") or []),
            "prior_topic_ids": [
                str(item.get("id") or "")
                for item in list(state.get("topics") or [])
                if isinstance(item, dict)
            ],
            "known_source_locations": [
                str(item.get("location") or "")
                for item in list(state.get("sources") or [])[: limits["max_sources"]]
                if isinstance(item, dict)
            ],
            "round": round_number,
            "source_mode": state["source_mode"],
            "complexity": str(state.get("complexity") or "simple"),
            "max_topics": max_topics,
        }
        result = _structured_agent(
            context,
            role="deep_research_supervisor",
            system_prompt=TOPIC_PLANNER_PROMPT,
            task=task,
            output_schema=TOPICS_SCHEMA,
            tools=[],
            max_steps=2,
        )
        clean_result = _without_debug(result)
        topics = [dict(item) for item in list(clean_result.get("topics") or []) if isinstance(item, dict)]
        topics = topics[:max_topics]
        if not topics:
            topics = [
                {
                    "id": f"round-{round_number}-primary",
                    "question": state["question"],
                    "rationale": "Directly answer the research question",
                    "source_mode": state["source_mode"],
                }
            ]
        for index, topic in enumerate(topics):
            topic.setdefault("id", f"round-{round_number}-{index + 1}")
            topic.setdefault("source_mode", state["source_mode"])
        return {
            "topics": topics,
            "round": round_number,
            "debug_events": [_debug_event("deep_research_supervisor", task, result)],
        }

    def dispatch_research(state: ResearchState) -> Command:
        return Command(goto=[Send("research_topic", {"active_topic": topic}) for topic in state.get("topics", [])])

    def research_topic(state: ResearchState) -> Command:
        topic = dict(state.get("active_topic") or {})
        topic_mode = str(topic.get("source_mode") or state["source_mode"])
        task = {
            "topic": topic,
            "brief": dict(state.get("brief") or {}),
            "known_gaps": list(state.get("gaps") or []),
        }
        result = _structured_agent(
            context,
            role="deep_research_researcher",
            system_prompt=RESEARCHER_PROMPT,
            task=task,
            output_schema=RESEARCH_SCHEMA,
            tools=_research_tools(topic_mode),
            max_steps=limits["max_tool_calls_per_researcher"],
        )
        return Command(
            update={
                "research_results": [{"topic": topic, "result": _without_debug(result)}],
                "debug_events": [_debug_event("deep_research_researcher", task, result)],
            },
            goto=Send("compress_and_deduplicate"),
        )

    def compress_and_deduplicate(state: ResearchState) -> dict[str, Any]:
        selected: list[SourceRecord] = []
        source_aliases: dict[str, str] = {}
        seen_locations: dict[str, SourceRecord] = {}
        seen_hashes: dict[str, SourceRecord] = {}
        raw_claims: list[ClaimRecord] = []
        for item in state.get("research_results", []):
            result = dict(item.get("result") or {}) if isinstance(item, dict) else {}
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
        sources = selected[: limits["max_sources"]]
        allowed_ids = {str(item["id"]) for item in sources}
        claims_by_text: dict[str, ClaimRecord] = {}
        for claim in raw_claims:
            key = " ".join(str(claim["text"]).lower().split())
            current = claims_by_text.setdefault(key, dict(claim))
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
        claims = list(claims_by_text.values())
        return {"sources": sources, "claims": claims}

    def verify_and_vote_claims(state: ResearchState) -> dict[str, Any]:
        by_id = {str(item.get("id") or ""): item for item in state.get("sources", [])}
        verified: list[ClaimRecord] = []
        disputed: list[ClaimRecord] = []
        unverified: list[ClaimRecord] = []
        for raw_claim in state.get("claims", []):
            claim = dict(raw_claim)
            supports = [by_id[item] for item in claim.get("source_ids", []) if item in by_id]
            conflicts = [by_id[item] for item in claim.get("conflict_source_ids", []) if item in by_id]
            independent = {str(item.get("independence_key") or item.get("location") or "") for item in supports}
            authoritative = any(bool(item.get("authority")) for item in supports)
            if conflicts:
                claim.update(status="disputed", reason="Reliable sources conflict")
                disputed.append(claim)
            elif authoritative or len(independent) >= 2:
                claim.update(
                    status="verified",
                    reason="Supported by an authoritative source" if authoritative else "Supported by independent sources",
                )
                verified.append(claim)
            else:
                claim.update(status="unverified", reason="Insufficient readable independent sources")
                unverified.append(claim)
        gaps = [str(item.get("text") or "") for item in unverified[:5]]
        return {
            "verified_claims": verified,
            "disputed_claims": disputed,
            "unverified_claims": unverified,
            "gaps": gaps,
        }

    def assess_gaps(state: ResearchState) -> str:
        if state.get("gaps") and int(state.get("round") or 0) < limits["max_research_rounds"]:
            return "research"
        return "report"

    def write_final_report(state: ResearchState) -> dict[str, Any]:
        task = {
            "question": state["question"],
            "brief": dict(state.get("brief") or {}),
            "verified_claims": list(state.get("verified_claims") or []),
            "sources": list(state.get("sources") or []),
            "disputed_claims": list(state.get("disputed_claims") or []),
            "limitations": list(state.get("limitations") or []),
        }
        result = _structured_agent(
            context,
            role="deep_research_report_writer",
            system_prompt=REPORT_WRITER_PROMPT,
            task=task,
            output_schema=REPORT_SCHEMA,
            tools=[],
            max_steps=3,
        )
        clean_result = _without_debug(result)
        return {
            "report_markdown": str(clean_result.get("report_markdown") or "").strip(),
            "debug_events": [_debug_event("deep_research_report_writer", task, result)],
        }

    def verify_citations(state: ResearchState) -> dict[str, Any]:
        report = str(state.get("report_markdown") or "")
        supported_ids = {
            str(source_id)
            for claim in state.get("verified_claims", [])
            for source_id in list(claim.get("source_ids") or [])
        }
        cited = set(re.findall(r"\[([A-Za-z0-9._:-]+)\]", report))
        invalid = sorted(item for item in cited if item not in supported_ids)
        limitations = list(state.get("limitations") or [])
        if invalid:
            limitations.append(f"Removed or unresolved citation ids: {', '.join(invalid)}")
            for item in invalid:
                report = report.replace(f"[{item}]", "")
        limitations.extend(
            f"Disputed claim: {item.get('text', '')}" for item in state.get("disputed_claims", [])
        )
        return {"report_markdown": report, "limitations": limitations}

    def persist_artifacts(state: ResearchState) -> dict[str, Any]:
        artifact_path = ""
        sources_path = ""
        claims_path = ""
        debug_path = ""
        if context.artifacts_dir is not None:
            artifacts = Path(context.artifacts_dir)
            artifacts.mkdir(parents=True, exist_ok=True)
            report_file = artifacts / "report.md"
            sources_file = artifacts / "sources.json"
            claims_file = artifacts / "claims.json"
            debug_file = artifacts / "debug.json"
            report_file.write_text(str(state.get("report_markdown") or "") + "\n", encoding="utf-8")
            sources_file.write_text(
                json.dumps(state.get("sources", []), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            claims_file.write_text(
                json.dumps(
                    {
                        "verified": state.get("verified_claims", []),
                        "disputed": state.get("disputed_claims", []),
                        "unverified": state.get("unverified_claims", []),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            debug_file.write_text(
                json.dumps(
                    {
                        "question": state.get("question", ""),
                        "source_mode": state.get("source_mode", ""),
                        "rounds": int(state.get("round") or 0),
                        "topics": state.get("topics", []),
                        "events": state.get("debug_events", []),
                        "gaps": state.get("gaps", []),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            artifact_path = str(report_file)
            sources_path = str(sources_file)
            claims_path = str(claims_file)
            debug_path = str(debug_file)
        return {
            "artifact_path": artifact_path,
            "sources_path": sources_path,
            "claims_path": claims_path,
            "debug_path": debug_path,
            "stats": {
                "rounds": int(state.get("round") or 0),
                "research_units": len(state.get("research_results", [])),
                "sources": len(state.get("sources", [])),
                "verified_claims": len(state.get("verified_claims", [])),
                "disputed_claims": len(state.get("disputed_claims", [])),
            },
        }

    graph.add_node("preflight", preflight)
    graph.add_node("write_research_brief", write_research_brief)
    graph.add_node("plan_research_topics", plan_research_topics)
    graph.add_node("dispatch_research", dispatch_research)
    graph.add_node("research_topic", research_topic)
    graph.add_node("compress_and_deduplicate", compress_and_deduplicate, defer=True)
    graph.add_node("verify_and_vote_claims", verify_and_vote_claims)
    graph.add_node("write_final_report", write_final_report)
    graph.add_node("verify_citations", verify_citations)
    graph.add_node("persist_artifacts", persist_artifacts)
    graph.set_entry_point("preflight")
    graph.add_sequence(["preflight", "write_research_brief", "plan_research_topics", "dispatch_research"])
    graph.add_edge("compress_and_deduplicate", "verify_and_vote_claims")
    graph.add_conditional_edges(
        "verify_and_vote_claims",
        assess_gaps,
        {"research": "plan_research_topics", "report": "write_final_report"},
    )
    graph.add_sequence(["write_final_report", "verify_citations", "persist_artifacts"])
    graph.add_edge("persist_artifacts", END)

    compiled = graph.compile()

    def payload_to_state(payload: dict[str, Any], _config: dict[str, Any] | None) -> dict[str, Any]:
        if "source" in payload:
            raise ValueError("deep_research 使用 source_mode，不支持 source")
        return {
            "question": str(payload.get("question") or "").strip(),
            "source_mode": str(payload.get("source_mode") or "web").strip(),
            "scope": str(payload.get("scope") or "").strip(),
            "complexity": "",
            "research_results": [],
            "sources": [],
            "claims": [],
            "verified_claims": [],
            "disputed_claims": [],
            "unverified_claims": [],
            "gaps": [],
            "assumptions": [],
            "limitations": [],
            "stats": {},
            "debug_events": [],
        }

    def state_to_result(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "report_markdown": str(state.get("report_markdown") or ""),
            "sources": list(state.get("sources") or []),
            "verified_claims": list(state.get("verified_claims") or []),
            "disputed_claims": list(state.get("disputed_claims") or []),
            "assumptions": list(state.get("assumptions") or []),
            "limitations": list(state.get("limitations") or []),
            "stats": dict(state.get("stats") or {}),
            "artifact_path": str(state.get("artifact_path") or ""),
            "sources_path": str(state.get("sources_path") or ""),
            "claims_path": str(state.get("claims_path") or ""),
            "debug_path": str(state.get("debug_path") or ""),
        }

    return CompiledPayloadGraph(
        name="deep_research",
        compiled_graph=compiled,
        payload_to_state=payload_to_state,
        state_to_result=state_to_result,
    )


__all__ = ["GRAPH_METADATA", "ClaimRecord", "ResearchState", "SourceRecord", "build_graph"]
