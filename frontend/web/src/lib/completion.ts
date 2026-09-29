import type { PluginInfo, SkillInfo } from "@juice-agents/shared/gateway/types";
import { collectSkillAliases } from "./skills.js";

export interface CompletionCandidate {
  label: string;
  description: string;
}

export const WEB_COMPLETION_VISIBLE_LIMIT = 5;

export const WEB_SLASH_COMMANDS: CompletionCandidate[] = [
  { label: "/help", description: "Show web commands" },
  { label: "/status", description: "Show current runner status" },
  { label: "/mode", description: "Switch agent mode" },
  { label: "/resources", description: "List mode resources" },
  { label: "/team", description: "Run one task with team mode" },
  { label: "/group", description: "Run one task with group mode" },
  { label: "/agent-type", description: "Save the default Agent type" },
  { label: "/models", description: "List runtime models" },
  { label: "/model", description: "Switch runtime model" },
  { label: "/agents", description: "List or view available agents" },
  { label: "/teams", description: "List or view Team manifests" },
  { label: "/memory", description: "Inspect workspace memory" },
  { label: "/dream", description: "Run memory consolidation" },
  { label: "/goal", description: "Set or inspect active goal" },
  { label: "/loop", description: "Schedule a recurring prompt" },
  { label: "/cron", description: "Manage scheduled prompts" },
  { label: "/skills", description: "List or view local skills" },
  { label: "/plugins", description: "List or view installed plugins" },
  { label: "/graph", description: "List, inspect, run, or control graphs" },
  { label: "/deep-research", description: "Run deep_research with a plain question" },
  { label: "/permissions", description: "Inspect permission rules" },
  { label: "/sessions", description: "List runner threads" },
  { label: "/clear", description: "Clear visible transcript" },
];

// 两个维度各自独立补全：/mode 走执行模式，/permissions 走审批策略。
const MODE_OPTIONS: CompletionCandidate[] = [
  { label: "agent", description: "Single agent" },
  { label: "plan", description: "Read-only planning" },
  { label: "team", description: "Team" },
  { label: "group", description: "Group" },
];

const PERMISSION_OPTIONS: CompletionCandidate[] = [
  { label: "default", description: "Ask before mutations" },
  { label: "accept", description: "Allow workspace edits" },
  { label: "status", description: "Show permission rules" },
];

const AGENT_TYPE_OPTIONS: CompletionCandidate[] = [
  { label: "react", description: "Tool-call agent" },
  { label: "codeact", description: "CodeAct agent" },
];

const MEMORY_OPTIONS: CompletionCandidate[] = [
  { label: "status", description: "Show memory status" },
  { label: "search", description: "Search memory topics" },
  { label: "view", description: "Read memory index or topic" },
];

const GOAL_OPTIONS: CompletionCandidate[] = [
  { label: "pause", description: "Pause active goal" },
  { label: "resume", description: "Resume active goal" },
  { label: "clear", description: "Clear active goal" },
];

const CRON_OPTIONS: CompletionCandidate[] = [
  { label: "list", description: "List scheduled prompts" },
  { label: "delete", description: "Delete scheduled prompt" },
  { label: "status", description: "Show scheduler status" },
];

const GRAPH_OPTIONS: CompletionCandidate[] = [
  { label: "list", description: "List available graphs" },
  { label: "view", description: "View one graph source" },
  { label: "run", description: "Run one graph" },
  { label: "runs", description: "List persisted graph runs" },
  { label: "pause", description: "Pause a graph run" },
  { label: "resume", description: "Resume a graph run" },
  { label: "stop", description: "Stop a graph run" },
  { label: "restart", description: "Restart a graph run" },
];

const GRAPH_NAME_OPTIONS: CompletionCandidate[] = [
  { label: "deep_research", description: "Cross-checked research report" },
];

const RESEARCH_SOURCE_OPTIONS: CompletionCandidate[] = [
  { label: "--web", description: "Use web sources (default)" },
  { label: "--workspace", description: "Use workspace sources" },
  { label: "--web-workspace", description: "Use web and workspace sources" },
  { label: '{"question":"","source_mode":"web"}', description: "Advanced JSON payload" },
  { label: '{"question":"","source_mode":"workspace"}', description: "Advanced JSON payload" },
  { label: '{"question":"","source_mode":"web_workspace"}', description: "Advanced JSON payload" },
];

interface CursorToken {
  token: string;
  start: number;
  end: number;
}

function clampCursor(input: string, cursor?: number): number {
  return Math.max(0, Math.min(cursor ?? input.length, input.length));
}

function normalizeCommandContext(input: string): string {
  return input.replace(/\s+/g, " ").trim();
}

export function cursorToken(input: string, cursor?: number): CursorToken {
  const position = clampCursor(input, cursor);
  let start = position;
  while (start > 0 && !/\s/.test(input[start - 1] || "")) {
    start -= 1;
  }
  let end = position;
  while (end < input.length && !/\s/.test(input[end] || "")) {
    end += 1;
  }
  return { token: input.slice(start, end), start, end };
}

function prefixFilter(candidates: CompletionCandidate[], prefix: string): CompletionCandidate[] {
  const normalized = prefix.toLowerCase();
  return candidates.filter((candidate) => {
    const label = candidate.label.toLowerCase();
    return label.startsWith(normalized) && label !== normalized;
  });
}

export function getVisibleWebCompletions(params: {
  candidates: CompletionCandidate[];
  selectedIndex: number;
  maxVisible?: number;
}): Array<CompletionCandidate & { originalIndex: number }> {
  const maxVisible = Math.max(1, params.maxVisible ?? WEB_COMPLETION_VISIBLE_LIMIT);
  if (params.candidates.length <= maxVisible) {
    return params.candidates.map((candidate, originalIndex) => ({
      ...candidate,
      originalIndex,
    }));
  }

  const selectedIndex = Math.max(0, Math.min(params.selectedIndex, params.candidates.length - 1));
  const lastStart = params.candidates.length - maxVisible;
  const startIndex = Math.min(Math.max(0, selectedIndex - maxVisible + 1), lastStart);
  return params.candidates
    .slice(startIndex, startIndex + maxVisible)
    .map((candidate, offset) => ({
      ...candidate,
      originalIndex: startIndex + offset,
    }));
}

export function getWebCommandCandidates(params: {
  input: string;
  cursor?: number;
  runtimeModelNames?: string[];
  skills?: Array<Pick<SkillInfo, "name" | "qualified_name">>;
  plugins?: Array<Pick<PluginInfo, "name">>;
  availableAgentNames?: string[];
}): CompletionCandidate[] {
  const input = params.input;
  const token = cursorToken(input, params.cursor);
  const context = input.slice(0, clampCursor(input, params.cursor));
  const firstTokenStart = input.search(/\S|$/);
  if (token.start === firstTokenStart && token.token.startsWith("$")) {
    const graphCandidates = [{ label: "$graph:deep_research", description: "Graph" }];
    if (token.token.toLowerCase().startsWith("$graph:")) {
      return prefixFilter(graphCandidates, token.token);
    }
    const skillAliases = collectSkillAliases(params.skills || []);
    const skillCandidates = skillAliases.map((name) => ({ label: `$${name}`, description: "Skill" }));
    const skillLabels = new Set(skillCandidates.map((item) => item.label.toLowerCase()));
    const pluginCandidates = [...new Set((params.plugins || []).map((plugin) => plugin.name).filter(Boolean))]
      .map((name) => ({ label: `$${name}`, description: "Plugin" }))
      .filter((item) => !skillLabels.has(item.label.toLowerCase()));
    return prefixFilter([...skillCandidates, ...pluginCandidates, ...graphCandidates], token.token);
  }
  const normalized = token.token.startsWith("/")
    ? token.token
    : normalizeCommandContext(context);
  if (!normalized.startsWith("/")) return [];

  const parts = normalized.split(/\s+/);
  const skillAliases = collectSkillAliases(params.skills || []);
  const hasTrailingWhitespace = /\s$/.test(context);

  if (parts.length === 1) {
    return prefixFilter(WEB_SLASH_COMMANDS, parts[0]);
  }

  if (parts[0] === "/mode" && parts.length === 2) return prefixFilter(MODE_OPTIONS, parts[1]);
  if (parts[0] === "/permissions" && parts.length === 2) return prefixFilter(PERMISSION_OPTIONS, parts[1]);
  if (parts[0] === "/agent-type" && parts.length === 2) return prefixFilter(AGENT_TYPE_OPTIONS, parts[1]);
  if (parts[0] === "/memory" && parts.length === 2) return prefixFilter(MEMORY_OPTIONS, parts[1]);
  if (parts[0] === "/goal" && parts.length === 2) return prefixFilter(GOAL_OPTIONS, parts[1]);
  if ((parts[0] === "/cron" || parts[0] === "/loop") && parts.length === 2) return prefixFilter(CRON_OPTIONS, parts[1]);
  if (parts[0] === "/graph" && parts.length === 2) return prefixFilter(GRAPH_OPTIONS, parts[1]);
  if (parts[0] === "/graph" && ["view", "run"].includes(parts[1] || "") && parts.length === 3 && !hasTrailingWhitespace) {
    return prefixFilter(GRAPH_NAME_OPTIONS, parts[2]);
  }
  if (parts[0] === "/deep-research" && ((parts.length === 1 && hasTrailingWhitespace) || parts.length === 2)) {
    return prefixFilter(RESEARCH_SOURCE_OPTIONS, parts[1] || "");
  }
  if (parts[0] === "/agents" && parts.length === 2) {
    const names = [...new Set((params.availableAgentNames || []).filter(Boolean))];
    return prefixFilter(names.map((label) => ({ label, description: "Available agent" })), parts[1]);
  }

  if (parts[0] === "/model" && parts.length === 2) {
    const prefix = parts[1].toLowerCase();
    return (params.runtimeModelNames || [])
      .filter((name) => name.toLowerCase().startsWith(prefix))
      .map((label) => ({ label, description: "Runtime model" }));
  }

  if (parts[0] === "/skills") {
    if (parts.length === 2) {
      const prefix = parts[1].toLowerCase();
      return skillAliases
        .filter((name) => name.toLowerCase().startsWith(prefix))
        .map((label) => ({ label, description: "Skill name" }));
    }
  }

  if (parts[0] === "/plugins" && parts.length === 2) {
    const pluginNames = [...new Set((params.plugins || []).map((plugin) => plugin.name).filter(Boolean))];
    return prefixFilter(
      pluginNames.map((label) => ({ label, description: "Plugin name" })),
      parts[1],
    );
  }

  return [];
}

export function acceptWebCompletion(params: {
  input: string;
  cursor?: number;
  candidate: CompletionCandidate;
}): { input: string; cursor: number } {
  const token = cursorToken(params.input, params.cursor);
  const input =
    params.input.slice(0, token.start) +
    params.candidate.label +
    params.input.slice(token.end);
  return {
    input,
    cursor: token.start + params.candidate.label.length,
  };
}
