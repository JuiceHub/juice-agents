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
    const workflowCandidates = [{ label: "$workflow:deep_research", description: "Workflow" }];
    if (token.token.toLowerCase().startsWith("$workflow:")) {
      return prefixFilter(workflowCandidates, token.token);
    }
    const skillAliases = collectSkillAliases(params.skills || []);
    const skillCandidates = skillAliases.map((name) => ({ label: `$${name}`, description: "Skill" }));
    const skillLabels = new Set(skillCandidates.map((item) => item.label.toLowerCase()));
    const pluginCandidates = [...new Set((params.plugins || []).map((plugin) => plugin.name).filter(Boolean))]
      .map((name) => ({ label: `$${name}`, description: "Plugin" }))
      .filter((item) => !skillLabels.has(item.label.toLowerCase()));
    return prefixFilter([...skillCandidates, ...pluginCandidates, ...workflowCandidates], token.token);
  }
  const normalized = token.token.startsWith("/")
    ? token.token
    : normalizeCommandContext(context);
  if (!normalized.startsWith("/")) return [];

  const parts = normalized.split(/\s+/);
  const skillAliases = collectSkillAliases(params.skills || []);
  const hasTrailingWhitespace = /\s$/.test(context);

  // 无参数命令列表：输入完整后不再显示候选，允许历史导航
  const COMMANDS_WITHOUT_PARAMS = new Set([
    "/help", "/status", "/models", "/clear", "/sessions",
  ]);

  if (parts.length === 1 && !hasTrailingWhitespace) {
    const prefix = parts[0].toLowerCase();
    // 对于无参数命令，完全匹配时关闭候选列表，让用户可以使用方向键切换历史
    if (COMMANDS_WITHOUT_PARAMS.has(prefix)) {
      return [];
    }
    return prefixFilter(WEB_SLASH_COMMANDS, parts[0]);
  }

  if (parts[0] === "/mode" && parts.length === 2) return prefixFilter(MODE_OPTIONS, parts[1]);
  if (parts[0] === "/permissions" && parts.length === 2) return prefixFilter(PERMISSION_OPTIONS, parts[1]);
  if (parts[0] === "/agent-type" && parts.length === 2) return prefixFilter(AGENT_TYPE_OPTIONS, parts[1]);
  if (parts[0] === "/memory" && parts.length === 2) return prefixFilter(MEMORY_OPTIONS, parts[1]);
  if (parts[0] === "/goal" && parts.length === 2) return prefixFilter(GOAL_OPTIONS, parts[1]);
  if ((parts[0] === "/cron" || parts[0] === "/loop") && parts.length === 2) return prefixFilter(CRON_OPTIONS, parts[1]);
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
