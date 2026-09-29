/**
 * Slash-command completion for the Rich Composer.
 *
 * The drawer still only covers slash commands, but the replacement logic now
 * preserves multiline drafts instead of collapsing everything into plain words.
 */

import { useState, useCallback } from "react";

export interface CandidateItem {
  label: string;
  description: string;
}

const SLASH_COMMANDS: CandidateItem[] = [
  { label: "/help", description: "Show available commands" },
  { label: "/status", description: "Show the current session" },
  { label: "/mode", description: "Switch agent mode" },
  { label: "/resources", description: "List mode resources" },
  { label: "/team", description: "Run one task with the team mode" },
  { label: "/group", description: "Run one task with the group mode" },
  { label: "/agent-type", description: "Save the default Agent type" },
  { label: "/models", description: "List available models" },
  { label: "/model", description: "Show or switch runtime model" },
  { label: "/config", description: "Edit runtime and memory config" },
  { label: "/agents", description: "List or view available agents" },
  { label: "/teams", description: "List or view Team manifests" },
  { label: "/memory", description: "Inspect workspace memory" },
  { label: "/dream", description: "Run memory consolidation" },
  { label: "/goal", description: "Set or inspect the active goal" },
  { label: "/loop", description: "Schedule a recurring prompt" },
  { label: "/cron", description: "Manage scheduled prompts" },
  { label: "/worktree", description: "Manage git worktrees" },
  { label: "/skills", description: "List or view local skills" },
  { label: "/plugins", description: "List or view installed plugins" },
  { label: "/graph", description: "List, inspect, run, or control graphs" },
  { label: "/deep-research", description: "Run deep_research with a plain question" },
  { label: "/permissions", description: "Switch permission mode or inspect rules" },
  { label: "/tasks", description: "Inspect background async tasks (Ctrl+T)" },
  { label: "/actors", description: "Switch between actor sessions (Ctrl+S)" },
  { label: "/resume", description: "Resume an existing session" },
  { label: "/sessions", description: "List saved sessions" },
  { label: "/clear", description: "Clear the transcript" },
  { label: "/exit", description: "Exit the application" },
];

// 两个维度各自独立补全：/mode 走执行模式，/permissions 走审批策略。
const MODE_OPTIONS: CandidateItem[] = [
  { label: "agent", description: "Single agent" },
  { label: "plan", description: "Read-only planning" },
  { label: "team", description: "Team" },
  { label: "group", description: "Group" },
];

const PERMISSION_OPTIONS: CandidateItem[] = [
  { label: "default", description: "Ask before mutations" },
  { label: "accept", description: "Allow workspace edits" },
  { label: "status", description: "Show permission rules" },
];

const MEMORY_OPTIONS: CandidateItem[] = [
  { label: "status", description: "Show memory status" },
  { label: "search", description: "Search memory topics" },
  { label: "view", description: "Read memory index or topic" },
];

const GOAL_OPTIONS: CandidateItem[] = [
  { label: "pause", description: "Pause the active goal" },
  { label: "resume", description: "Resume the active goal" },
  { label: "clear", description: "Clear the active goal" },
];

const CRON_OPTIONS: CandidateItem[] = [
  { label: "list", description: "List scheduled prompts" },
  { label: "delete", description: "Delete a scheduled prompt" },
  { label: "status", description: "Show cron scheduler state" },
];

const WORKTREE_OPTIONS: CandidateItem[] = [
  { label: "enter", description: "Create or switch to a worktree" },
  { label: "exit", description: "Exit the active worktree" },
  { label: "list", description: "List managed worktrees" },
  { label: "status", description: "Show worktree status" },
];

const WORKTREE_EXIT_OPTIONS: CandidateItem[] = [
  { label: "--discard", description: "Force cleanup dirty or ahead worktree" },
];

const GRAPH_OPTIONS: CandidateItem[] = [
  { label: "list", description: "List available graphs" },
  { label: "view", description: "View one graph source" },
  { label: "run", description: "Run one graph" },
  { label: "runs", description: "List persisted graph runs" },
  { label: "pause", description: "Pause a graph run" },
  { label: "resume", description: "Resume a graph run" },
  { label: "stop", description: "Stop a graph run" },
  { label: "restart", description: "Restart a graph run" },
];

const GRAPH_NAME_OPTIONS: CandidateItem[] = [
  { label: "deep_research", description: "Cross-checked research report" },
];

const RESEARCH_SOURCE_OPTIONS: CandidateItem[] = [
  { label: "--web", description: "Use web sources (default)" },
  { label: "--workspace", description: "Use workspace sources" },
  { label: "--web-workspace", description: "Use web and workspace sources" },
  { label: '{"question":"","source_mode":"web"}', description: "Advanced JSON payload" },
  { label: '{"question":"","source_mode":"workspace"}', description: "Advanced JSON payload" },
  { label: '{"question":"","source_mode":"web_workspace"}', description: "Advanced JSON payload" },
];

function normalizeCommandInput(input: string): string {
  return input.replace(/\s+/g, " ").trim();
}

interface CursorToken {
  token: string;
  start: number;
  end: number;
}

function clampCompletionCursor(input: string, cursor?: number): number {
  return Math.max(0, Math.min(cursor ?? input.length, input.length));
}

function getCursorToken(input: string, cursor?: number): CursorToken {
  const position = clampCompletionCursor(input, cursor);
  let start = position;
  while (start > 0 && !/\s/.test(input[start - 1] || "")) {
    start -= 1;
  }

  let end = position;
  while (end < input.length && !/\s/.test(input[end] || "")) {
    end += 1;
  }

  return {
    token: input.slice(start, end),
    start,
    end,
  };
}

function getUniqueNames(names: string[]): string[] {
  return [...new Set(names.filter(Boolean))];
}

function prefixFilter(candidates: CandidateItem[], prefix: string): CandidateItem[] {
  const normalized = prefix.toLowerCase();
  return candidates.filter((candidate) => {
    const label = candidate.label.toLowerCase();
    return label.startsWith(normalized) && label !== normalized;
  });
}

export function getCommandCandidates(
  input: string,
  runtimeModelNames: string[] = [],
  skillNames: string[] = [],
  cursor?: number,
  pluginNames: string[] = [],
  availableAgentNames: string[] = [],
): CandidateItem[] {
  const tokenAtCursor = getCursorToken(input, cursor);
  const context = input.slice(0, clampCompletionCursor(input, cursor));
  const firstTokenStart = input.search(/\S|$/);
  if (tokenAtCursor.start === firstTokenStart && tokenAtCursor.token.startsWith("$")) {
    const prefix = tokenAtCursor.token.toLowerCase();
    const graphCandidates = [{ label: "$graph:deep_research", description: "Graph" }];
    if (prefix.startsWith("$graph:")) {
      return prefixFilter(graphCandidates, prefix);
    }
    const skillCandidates = getUniqueNames(skillNames).map((name) => ({
      label: `$${name}`,
      description: "Skill",
    }));
    const skillLabels = new Set(skillCandidates.map((item) => item.label.toLowerCase()));
    const pluginCandidates = getUniqueNames(pluginNames)
      .map((name) => ({ label: `$${name}`, description: "Plugin" }))
      .filter((item) => !skillLabels.has(item.label.toLowerCase()));
    return prefixFilter([...skillCandidates, ...pluginCandidates, ...graphCandidates], prefix);
  }
  const normalized = tokenAtCursor.token.startsWith("/")
    ? tokenAtCursor.token
    : normalizeCommandInput(context);
  if (!normalized.startsWith("/")) {
    return [];
  }

  const parts = normalized.split(/\s+/);
  const uniqueSkillNames = getUniqueNames(skillNames);
  const uniquePluginNames = getUniqueNames(pluginNames);
  const hasTrailingWhitespace = /\s$/.test(context);

  if (parts.length === 1 && !hasTrailingWhitespace) {
    const prefix = parts[0].toLowerCase();
    // Keep the candidate visible even when the command name matches exactly.
    // A trailing space (added on completion) skips this branch and falls
    // through to close the panel, so ↑/↓ can recall history uninterrupted.
    return SLASH_COMMANDS.filter((candidate) =>
      candidate.label.toLowerCase().startsWith(prefix),
    );
  }

  if (parts[0] === "/mode") {
    if (parts.length === 2) {
      return prefixFilter(MODE_OPTIONS, parts[1]);
    }
  }

  if (parts[0] === "/permissions") {
    if (parts.length === 2) {
      return prefixFilter(PERMISSION_OPTIONS, parts[1]);
    }
  }

  if (parts[0] === "/model" && parts.length === 2) {
    const prefix = parts[1].toLowerCase();
    return runtimeModelNames
      .filter((name) => {
        const label = name.toLowerCase();
        return label.startsWith(prefix) && label !== prefix;
      })
      .map((name) => ({
        label: name,
        description: "Runtime model",
      }));
  }

  if (parts[0] === "/agents" && parts.length === 2) {
    return prefixFilter(
      getUniqueNames(availableAgentNames).map((label) => ({ label, description: "Available agent" })),
      parts[1],
    );
  }

  if (parts[0] === "/memory" && parts.length === 2) {
    return prefixFilter(MEMORY_OPTIONS, parts[1]);
  }

  if (parts[0] === "/goal" && parts.length === 2) {
    return prefixFilter(GOAL_OPTIONS, parts[1]);
  }

  if ((parts[0] === "/cron" || parts[0] === "/loop") && parts.length === 2) {
    return prefixFilter(CRON_OPTIONS, parts[1]);
  }

  if (parts[0] === "/worktree") {
    if (parts.length === 2) {
      return prefixFilter(WORKTREE_OPTIONS, parts[1]);
    }
    if (parts[1] === "exit" && parts.length === 3) {
      return prefixFilter(WORKTREE_EXIT_OPTIONS, parts[2]);
    }
  }

  if (parts[0] === "/graph" && parts.length === 2) {
    return prefixFilter(GRAPH_OPTIONS, parts[1]);
  }

  if (parts[0] === "/graph" && ["view", "run"].includes(parts[1] || "") && parts.length === 3 && !hasTrailingWhitespace) {
    return prefixFilter(GRAPH_NAME_OPTIONS, parts[2]);
  }

  if (parts[0] === "/deep-research" && ((parts.length === 1 && hasTrailingWhitespace) || parts.length === 2)) {
    return prefixFilter(RESEARCH_SOURCE_OPTIONS, parts[1] || "");
  }

  if (parts[0] === "/skills") {
    if (parts.length === 2) {
      const prefix = parts[1].toLowerCase();
      return uniqueSkillNames
        .filter((name) => {
          const label = name.toLowerCase();
          return label.startsWith(prefix) && label !== prefix;
        })
        .map((name) => ({
          label: name,
          description: "Skill name",
        }));
    }
  }

  if (parts[0] === "/plugins" && parts.length === 2) {
    return prefixFilter(
      uniquePluginNames.map((label) => ({ label, description: "Plugin name" })),
      parts[1],
    );
  }

  return [];
}

/**
 * 构建内联提示文本 - 当命令后输入空格时显示所有可选参数
 * 例如: /mode  -> <default|accept|plan>
 */
export function buildInlineHint(params: {
  input: string;
  cursor?: number;
  candidates: CandidateItem[];
}): string | null {
  const position = clampCompletionCursor(params.input, params.cursor);

  // 只在光标位于输入末尾时显示内联提示
  if (position !== params.input.length) {
    return null;
  }

  // 必须以 / 开头
  if (!params.input.startsWith("/")) {
    return null;
  }

  // 必须以空格结尾（表示命令已输入完成，等待参数）
  if (!/\s$/.test(params.input)) {
    return null;
  }

  const normalized = normalizeCommandInput(params.input);
  const parts = normalized.split(/\s+/);

  // 根据命令类型返回对应的选项提示
  if (parts.length === 1) {
    // 一级命令后的空格，显示子命令或参数选项
    if (parts[0] === "/mode") {
      return "<agent|plan|team|group>";
    }
    if (parts[0] === "/permissions") {
      return "<default|accept|status>";
    }
    if (parts[0] === "/memory") {
      return "<status|search|view>";
    }
    if (parts[0] === "/goal") {
      return "<pause|resume|clear>";
    }
    if (parts[0] === "/cron" || parts[0] === "/loop") {
      return "<list|delete|status>";
    }
    if (parts[0] === "/worktree") {
      return "<enter|exit|list|status>";
    }
    if (parts[0] === "/graph") {
      return "<list|view|run|runs|pause|resume|stop|restart>";
    }
    if (parts[0] === "/agents") {
      return "<agent-name>";
    }
    if (parts[0] === "/deep-research") {
      return "<--web|--workspace|--web-workspace>";
    }
  } else if (parts.length === 2) {
    // 二级命令后的空格
    if (parts[0] === "/worktree" && parts[1] === "exit") {
      return "<--discard>";
    }
    if (parts[0] === "/graph" && (parts[1] === "view" || parts[1] === "run")) {
      return "<deep_research>";
    }
  }

  return null;
}

export function buildGhostText(params: {
  input: string;
  cursor?: number;
  candidates: CandidateItem[];
  selectedIndex: number;
  visible: boolean;
}): string | null {
  if (!params.visible || params.candidates.length === 0) {
    return null;
  }

  const selected = params.candidates[params.selectedIndex];
  if (!selected) {
    return null;
  }

  const tokenAtCursor = getCursorToken(params.input, params.cursor);
  if (!tokenAtCursor.token || clampCompletionCursor(params.input, params.cursor) !== tokenAtCursor.end) {
    return null;
  }

  if (!selected.label.toLowerCase().startsWith(tokenAtCursor.token.toLowerCase())) {
    return null;
  }

  const suffix = selected.label.slice(tokenAtCursor.token.length);
  return suffix || null;
}

export function acceptCommandCompletion(params: {
  input: string;
  cursor?: number;
  candidate: CandidateItem;
}): { input: string; cursor: number } {
  const tokenAtCursor = getCursorToken(params.input, params.cursor);
  const rest = params.input.slice(tokenAtCursor.end);
  // Append a trailing space so the candidate panel closes after completion and
  // ↑/↓ recall history instead of cycling candidates. Skip it when the next
  // character is already whitespace to avoid doubling spaces mid-line.
  const suffix = /^\s/.test(rest) ? "" : " ";
  const completed = params.candidate.label + suffix;
  const nextInput =
    params.input.slice(0, tokenAtCursor.start) + completed + rest;

  return {
    input: nextInput,
    cursor: tokenAtCursor.start + completed.length,
  };
}

export function useCompletion(
  runtimeModelNames: string[] = [],
  skillNames: string[] = [],
  pluginNames: string[] = [],
  availableAgentNames: string[] = [],
) {
  const [candidates, setCandidates] = useState<CandidateItem[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [visible, setVisible] = useState(false);
  const [currentInput, setCurrentInput] = useState("");
  const [currentCursor, setCurrentCursor] = useState(0);

  const updateCandidates = useCallback((input: string, cursor?: number) => {
    const nextCursor = clampCompletionCursor(input, cursor);
    setCurrentInput(input);
    setCurrentCursor(nextCursor);
    const matches = getCommandCandidates(
      input,
      runtimeModelNames,
      skillNames,
      nextCursor,
      pluginNames,
      availableAgentNames,
    );
    setCandidates(matches);
    setSelectedIndex(0);
    setVisible(matches.length > 0);
  }, [runtimeModelNames, skillNames, pluginNames, availableAgentNames]);

  const selectNext = useCallback(() => {
    setCandidates((current) => {
      if (current.length === 0) return current;
      setSelectedIndex((prev) => (prev + 1) % current.length);
      return current;
    });
  }, []);

  const selectPrev = useCallback(() => {
    setCandidates((current) => {
      if (current.length === 0) return current;
      setSelectedIndex((prev) => (prev - 1 + current.length) % current.length);
      return current;
    });
  }, []);

  const accept = useCallback(
    (input: string, cursor?: number): { input: string; cursor: number } | null => {
      if (candidates.length === 0 || !visible) return null;

      const selected = candidates[selectedIndex];
      if (!selected) return null;

      const result = acceptCommandCompletion({ input, cursor, candidate: selected });

      setCandidates([]);
      setVisible(false);
      setSelectedIndex(0);

      return result;
    },
    [candidates, selectedIndex, visible]
  );

  const dismiss = useCallback(() => {
    setCandidates([]);
    setVisible(false);
    setSelectedIndex(0);
  }, []);

  const ghostText = buildGhostText({
    input: currentInput,
    cursor: currentCursor,
    candidates,
    selectedIndex,
    visible,
  });

  const inlineHint = buildInlineHint({
    input: currentInput,
    cursor: currentCursor,
    candidates,
  });

  return {
    candidates,
    selectedIndex,
    visible,
    ghostText,
    inlineHint,
    updateCandidates,
    selectNext,
    selectPrev,
    accept,
    dismiss,
  };
}
