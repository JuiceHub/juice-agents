/**
 * Shared slash-command presentation helpers for CLI and Web.
 *
 * Each block keeps a text fallback so search, copy, older renderers, and
 * terminal output remain stable even when a surface renders display payloads.
 */

import type {
  AvailableAgentsResponse,
  TeamManifest,
  TeamsListResponse,
  CronStatusResponse,
  CronTask,
  CronTaskDeleteResponse,
  CronTaskListResponse,
  ModeResourcesResponse,
  DreamRunResponse,
  GoalSummary,
  MemorySearchResponse,
  MemoryStatusResponse,
  MemoryViewResponse,
  PendingSessionRuntime,
  PluginsListResponse,
  RuntimeModelInfo,
  SessionStatus,
  SessionSummary,
  SkillsListResponse,
  SkillViewResponse,
  WorktreeListResponse,
  WorktreeStatus,
} from "../gateway/types.js";
import type { MessageBlock, MessageDisplay } from "./stream.js";

const UNKNOWN_MODEL = "unknown-model";

export function messageBlockToText(block: MessageBlock): string {
  return block.text || (block.display ? formatDisplayAsText(block.display) : "");
}

export function formatDisplayAsText(display: MessageDisplay): string {
  if (display.type === "table") {
    return formatTable(display.columns, display.rows);
  }
  if (display.type === "kv") {
    return display.items.map((item) => `${item.label}: ${item.value}`).join("\n");
  }
  if (display.type === "list") {
    return display.items
      .map((item) => (item.description ? `${item.label}  ${item.description}` : item.label))
      .join("\n");
  }
  return display.content;
}

export function presentCommandHelp(commands?: { label: string; description?: string }[]): MessageBlock {
  const items = commands || [
    { label: "/help" },
    { label: "/status" },
    { label: "/mode [agent|plan|team|group]" },
    { label: "/resources [agent|team|group]" },
    { label: "/team <task>" },
    { label: "/group <task>" },
    { label: "/agent-type [react|codeact]" },
    { label: "/models" },
    { label: "/model [model_name] [effort]" },
    { label: "/config" },
    { label: "/agents [name]" },
    { label: "/teams [name]" },
    { label: "/memory [status|search|view] [query|path]" },
    { label: "/dream" },
    { label: "/goal [pause|resume|clear|objective]" },
    { label: "/worktree [enter|exit|list|status] [name]" },
    { label: "/loop <interval> <prompt>" },
    { label: "/loop [list|delete|status]" },
    { label: "/cron [list|delete|status]" },
    { label: "/skills [name] [file_path]" },
    { label: "/plugins [name] [file_path]" },
    { label: "/graph [list|view|run|runs|pause|resume|stop|restart]" },
    { label: "/deep-research [--web|--workspace|--web-workspace] <question|json>" },
    { label: "/permissions [default|accept|status]" },
    { label: "/tasks" },
    { label: "/resume [runner_id]" },
    { label: "/sessions" },
    { label: "/clear" },
    { label: "/exit" },
  ];
  const display: MessageDisplay = { type: "list", items };
  return { kind: "system", title: "Commands", text: formatDisplayAsText(display), display };
}

export function presentPendingStatus(runtime: PendingSessionRuntime): MessageBlock {
  const display = kv([
    ["active_session", "no"],
    ["workspace", runtime.base_dir],
    ["pending_agent_mode", runtime.agent_mode],
    ["pending_permission_mode", runtime.permission_mode],
    ["pending_agent_type", runtime.agent_type],
    ["pending_model_name", runtime.model_name || UNKNOWN_MODEL],
    ["pending_model_effort", runtime.model_effort || "disabled"],
  ]);
  return { kind: "system", title: "Status", text: formatDisplayAsText(display), display };
}

/**
 * Present the config-only agent type contract consistently across CLI and Web.
 * This deliberately avoids rendering a SessionStatus: saving the workspace
 * default must never imply that an already-running actor changed protocol.
 */
export function presentAgentTypeConfigSaved(agentType: string): MessageBlock {
  return {
    kind: "system",
    title: "Agent type default saved",
    text: [
      `Workspace default agent_type: ${agentType}`,
      "Existing live actors are unchanged.",
      `Newly created default Agents will use ${agentType}.`,
    ].join("\n"),
  };
}

export function presentStatus(status: SessionStatus | null | undefined): MessageBlock {
  if (!status?.runner_id) {
    return { kind: "system", title: "Status", text: "No active runner. Send a message to start one." };
  }
  const items: [string, string][] = [
    ["runner_id", status.runner_id],
    ["agent_mode", status.agent_mode || "agent"],
    ["permission_mode", status.permission_mode],
    ["agent_type", status.agent_type],
    ["model_name", status.model_name || UNKNOWN_MODEL],
    ["model_effort", status.model_effort || "disabled"],
    ["backend", status.backend || "unknown-backend"],
    ["provider_model_name", status.provider_model_name || UNKNOWN_MODEL],
    ["root_actor", status.root_actor_name],
    ["workspace", status.base_dir],
    ["session_kind", status.resumed ? "resumed" : "new"],
    ["started", status.started ? "yes" : "no"],
  ];
  if (status.goal) {
    items.push(
      ["goal_status", status.goal.status],
      ["goal_objective", status.goal.objective || ""],
      ["goal_turns", `${status.goal.turns_used}/${status.goal.max_turns}`],
      ["goal_latest_evaluator_reason", status.goal.latest_evaluator_reason || ""],
    );
  }
  if (status.worktree?.exists || status.worktree?.slug) {
    items.push(
      ["worktree", status.worktree.slug || status.worktree.name || ""],
      ["worktree_branch", status.worktree.branch || ""],
      ["worktree_path", status.worktree.path || ""],
      ["worktree_dirty", status.worktree.dirty ? "yes" : "no"],
      ["worktree_ahead", String(status.worktree.ahead ?? 0)],
    );
  }
  const display = kv(items);
  return { kind: "system", title: "Status", text: formatDisplayAsText(display), display };
}

export function presentWorktree(status: WorktreeStatus | Record<string, any> | null | undefined): MessageBlock {
  if (!status || (!status.exists && !status.slug)) {
    return { kind: "system", title: "Worktree", text: String(status?.error || "No active worktree.") };
  }
  const display = kv([
    ["name", status.name || ""],
    ["slug", status.slug || ""],
    ["branch", status.branch || ""],
    ["path", status.path || ""],
    ["base_sha", status.base_sha || ""],
    ["head_sha", status.head_sha || ""],
    ["dirty", status.dirty ? "yes" : "no"],
    ["ahead", String(status.ahead ?? 0)],
    ["exists", status.exists ? "yes" : "no"],
    ["active", status.active ? "yes" : "no"],
    ["temporary", status.temporary ? "yes" : "no"],
    ["error", status.error || ""],
  ]);
  return { kind: "system", title: "Worktree", text: formatDisplayAsText(display), display };
}

export function presentWorktrees(payload: WorktreeListResponse | Record<string, any>): MessageBlock {
  const worktrees = (payload.worktrees || []) as WorktreeStatus[];
  if (worktrees.length === 0) {
    return { kind: "system", title: "Worktrees", text: "No managed worktrees." };
  }
  const display = table(
    [
      ["slug", "SLUG"],
      ["branch", "BRANCH"],
      ["active", "ACTIVE"],
      ["dirty", "DIRTY"],
      ["ahead", "AHEAD"],
      ["path", "PATH"],
    ],
    worktrees.map((item) => ({
      slug: item.slug,
      branch: item.branch,
      active: item.active ? "yes" : "no",
      dirty: item.dirty ? "yes" : "no",
      ahead: String(item.ahead ?? 0),
      path: item.path,
    })),
  );
  return { kind: "system", title: "Worktrees", text: formatDisplayAsText(display), display };
}

export function presentGoal(goal: GoalSummary | Record<string, any> | null | undefined): MessageBlock {
  if (!goal || !("status" in goal)) {
    return { kind: "system", title: "Goal", text: "No active goal." };
  }
  const display = kv([
    ["status", String(goal.status || "unknown")],
    ["objective", goal.objective || ""],
    ["turns", `${goal.turns_used ?? 0}/${goal.max_turns ?? 0}`],
    ["elapsed_seconds", String(Math.round(Number(goal.elapsed_seconds || 0)))],
    ["max_runtime_seconds", String(Math.round(Number(goal.max_runtime_seconds || 0)))],
    ["latest_evaluator_reason", goal.latest_evaluator_reason || ""],
    ["pause_reason", goal.pause_reason || ""],
  ]);
  return { kind: "system", title: "Goal", text: formatDisplayAsText(display), display };
}

export function presentModels(models: RuntimeModelInfo[]): MessageBlock {
  if (models.length === 0) {
    return { kind: "system", title: "Models", text: "No models found." };
  }
  const display = table(
    [
      ["model_name", "MODEL_NAME"],
      ["backend", "BACKEND"],
      ["provider_model_name", "PROVIDER_MODEL"],
      ["supported_efforts", "EFFORTS"],
    ],
    models.map((model) => ({
      model_name: model.model_name,
      backend: model.backend || "unknown",
      provider_model_name: model.provider_model_name || "",
      supported_efforts: (model.supported_efforts || []).join(",") || "disabled",
    })),
  );
  return { kind: "system", title: "Models", text: formatDisplayAsText(display), display };
}

export function presentSessions(summaries: SessionSummary[]): MessageBlock {
  if (summaries.length === 0) {
    return { kind: "system", title: "Sessions", text: "No sessions found." };
  }
  const display = table(
    [
      ["runner_id", "RUNNER_ID"],
      ["request", "REQUEST"],
      ["agent_mode", "AGENT_MODE"],
      ["permission_mode", "PERMISSION"],
      ["root_actor", "ROOT_ACTOR"],
      ["goal", "GOAL"],
      ["updated_at", "UPDATED_AT"],
    ],
    summaries.map((session) => ({
      runner_id: session.runner_id,
      request: session.first_user_request_preview || session.goal_objective_preview || session.runner_id,
      permission_mode: session.permission_mode,
      agent_mode: session.agent_mode,
      root_actor: session.root_actor_name,
      goal: session.goal_status ? `${session.goal_status}:${session.goal_objective_preview || ""}` : "",
      updated_at: session.updated_at,
    })),
  );
  return { kind: "system", title: "Sessions", text: formatDisplayAsText(display), display };
}

export function presentSkills(payload: SkillsListResponse): MessageBlock {
  const skills = payload.skills || [];
  if (skills.length === 0) {
    return { kind: "system", title: "Skills", text: "No skills found." };
  }
  const display = table(
    [
      ["skill", "SKILL"],
      ["source", "SOURCE"],
      ["description", "DESCRIPTION"],
    ],
    skills.map((skill) => ({
      skill: skill.qualified_name || skill.name,
      source: skill.source || "unknown",
      description: truncateOneLine(skill.description || "", 96),
    })),
  );
  return { kind: "system", title: "Skills", text: formatDisplayAsText(display), display };
}

export function presentPlugins(payload: PluginsListResponse): MessageBlock {
  const plugins = payload.plugins || [];
  if (plugins.length === 0) {
    return { kind: "system", title: "Plugins", text: "No plugins found." };
  }
  const display = table(
    [
      ["plugin", "PLUGIN"],
      ["source", "SOURCE"],
      ["skills", "SKILLS"],
      ["description", "DESCRIPTION"],
    ],
    plugins.map((plugin) => ({
      plugin: plugin.name,
      source: plugin.source || "unknown",
      skills: (plugin.skill_names || []).join(", "),
      description: truncateOneLine(plugin.description || "", 80),
    })),
  );
  return { kind: "system", title: "Plugins", text: formatDisplayAsText(display), display };
}

/** Render the same availability-filtered declarations returned by `/agents`. */
export function presentAvailableAgents(payload: AvailableAgentsResponse): MessageBlock {
  const agents = payload.agents || [];
  if (agents.length === 0) {
    return { kind: "system", title: "Agents", text: "No agents are available in this mode." };
  }
  if (agents.length === 1) {
    const agent = agents[0];
    const config = agent.config || {};
    const display = kv([
      ["name", agent.name],
      ["source", agent.source],
      ["allowed_modes", agent.allowed_modes?.join(", ") || "all"],
      ["description", agent.description],
      ["config", JSON.stringify(config)],
    ]);
    return { kind: "system", title: `Agent ${agent.name}`, text: formatDisplayAsText(display), display };
  }
  const display = table(
    [
      ["agent", "AGENT"],
      ["type", "TYPE"],
      ["modes", "MODES"],
      ["tools", "TOOLS"],
      ["description", "DESCRIPTION"],
    ],
    agents.map((agent) => ({
      agent: agent.name,
      type: String(agent.config?.agent_type || "default"),
      modes: agent.allowed_modes?.join(", ") || "all",
      tools: truncateOneLine(
        Array.isArray(agent.config?.tools)
          ? agent.config.tools.map((tool) => typeof tool === "string" ? tool : String((tool as Record<string, unknown>)?.name || "")).filter(Boolean).join(",")
          : "",
        34,
      ),
      description: truncateOneLine(agent.description, 80),
    })),
  );
  return { kind: "system", title: "Agents", text: formatDisplayAsText(display), display };
}

/** Render Team relationships without exposing Team-private Agent copies. */
export function presentTeams(payload: TeamsListResponse): MessageBlock {
  const teams = payload.teams || [];
  if (teams.length === 0) {
    return { kind: "system", title: "Teams", text: "No Team manifests found." };
  }
  const display = table(
    [
      ["team", "TEAM"],
      ["members", "MEMBERS"],
      ["description", "DESCRIPTION"],
    ],
    teams.map((team) => ({
      team: team.team_name,
      members: team.member_names.join(", "),
      description: truncateOneLine(team.description, 80),
    })),
  );
  return { kind: "system", title: "Teams", text: formatDisplayAsText(display), display };
}

/** Render one Team manifest and its global Agent name references. */
export function presentTeamManifest(manifest: TeamManifest): MessageBlock {
  const display = kv([
    ["team_name", manifest.team_name],
    ["schema_version", String(manifest.schema_version)],
    ["description", manifest.description],
    ["member_names", manifest.member_names.join(", ")],
  ]);
  return { kind: "system", title: `Team ${manifest.team_name}`, text: formatDisplayAsText(display), display };
}

export function presentModeResources(payload: ModeResourcesResponse | Record<string, any>): MessageBlock {
  const resources = (payload.resources || []) as Array<Record<string, any>>;
  if (resources.length === 0) {
    return { kind: "system", title: "Mode Resources", text: "No mode resources found." };
  }
  const display = table(
    [
      ["mode_id", "MODE ID"],
      ["role", "ROLE"],
      ["name", "NAME"],
      ["source", "SOURCE"],
      ["status", "STATUS"],
      ["path", "PATH"],
      ["description", "DESCRIPTION"],
    ],
    resources.map((resource) => ({
      mode_id: String(resource.mode_id || ""),
      role: String(resource.role || ""),
      name: String(resource.name || ""),
      source: String(resource.source || ""),
      status: String(resource.status || ""),
      path: String(resource.path || ""),
      description: truncateOneLine(String(resource.description || ""), 72),
    })),
  );
  return { kind: "system", title: "Mode Resources", text: formatDisplayAsText(display), display };
}

export function presentSkillView(payload: SkillViewResponse | Record<string, any>): MessageBlock {
  if (!payload.success) {
    return { kind: "error", text: payload.error || "Skill view failed." };
  }
  const title = payload.qualified_name || payload.name || "Skill";
  const content = payload.content || "";
  const prefix = payload.path ? `path: ${payload.path}\n\n` : "";
  return {
    kind: "system",
    title,
    text: `${prefix}${content}`.trim(),
    display: { type: "code", language: "markdown", content },
  };
}

export function presentMemoryStatus(payload: MemoryStatusResponse | Record<string, any>): MessageBlock {
  const display = kv([
    ["enabled", payload.enabled ? "yes" : "no"],
    ["dream_enabled", payload.dream_enabled ? "yes" : "no"],
    ["memory_dir", payload.memory_dir || ""],
    ["entrypoint", payload.entrypoint || ""],
    ["topic_count", String(payload.topic_count ?? 0)],
    ["last_dream_at", payload.last_dream_at || "never"],
    ["auto_dream_due", payload.auto_dream_due ? "yes" : "no"],
    ["next_dream_reason", payload.next_dream_reason || "unknown"],
    ["eligible_session_count", String(payload.eligible_session_count ?? 0)],
    ["dream_lock_owner", payload.dream_lock_owner || ""],
  ]);
  return { kind: "system", title: "Memory", text: formatDisplayAsText(display), display };
}

export function presentMemorySearch(payload: MemorySearchResponse | Record<string, any>): MessageBlock {
  if (payload.error) {
    return { kind: "system", title: "Memory Search", text: payload.error };
  }
  const hits = (payload.hits || []) as { path: string; line: number; snippet: string }[];
  if (hits.length === 0) {
    return { kind: "system", title: "Memory Search", text: "No memory hits." };
  }
  const display = table(
    [
      ["location", "LOCATION"],
      ["snippet", "SNIPPET"],
    ],
    hits.map((hit) => ({
      location: `${hit.path}:${hit.line}`,
      snippet: hit.snippet,
    })),
  );
  return { kind: "system", title: "Memory Search", text: formatDisplayAsText(display), display };
}

export function presentMemoryView(payload: MemoryViewResponse | Record<string, any>): MessageBlock {
  if (payload.error) {
    return { kind: "system", title: "Memory", text: payload.error };
  }
  return {
    kind: "system",
    title: payload.path || "MEMORY.md",
    text: payload.content || "",
    display: { type: "code", language: "markdown", content: payload.content || "" },
  };
}

export function presentDreamReceipt(payload: DreamRunResponse | Record<string, any>): MessageBlock {
  const status = payload.status || "unknown";
  if (status === "disabled" || status === "locked") {
    return { kind: "system", title: "Dream", text: payload.error || status };
  }
  const display = kv([
    ["status", status],
    ["kind", payload.kind || "memory_dream"],
    ["async_task_id", payload.async_task_id || payload.job_id || ""],
    ["summary", payload.summary || ""],
    ["output_dir", payload.output_dir || ""],
  ]);
  return { kind: "system", title: "Dream", text: formatDisplayAsText(display), display };
}

export function presentCronTasks(payload: CronTaskListResponse | Record<string, any>): MessageBlock {
  const tasks = (payload.tasks || []) as CronTask[];
  if (tasks.length === 0) {
    return { kind: "system", title: "Cron", text: "No scheduled tasks." };
  }
  const display = table(
    [
      ["id", "ID"],
      ["schedule", "SCHEDULE"],
      ["type", "TYPE"],
      ["next_run", "NEXT_RUN"],
      ["prompt", "PROMPT"],
    ],
    tasks.map((task) => ({
      id: task.id,
      schedule: task.cron,
      type: task.recurring ? "recurring" : "one-shot",
      next_run: formatCronTime(task.next_run_at),
      prompt: truncateCronPrompt(task.prompt),
    })),
  );
  return { kind: "system", title: "Cron", text: formatDisplayAsText(display), display };
}

export function presentCronStatus(payload: CronStatusResponse | Record<string, any>): MessageBlock {
  const display = kv([
    ["enabled", payload.enabled ? "yes" : "no"],
    ["task_count", String(payload.task_count ?? 0)],
    ["next_run_at", formatCronTime(payload.next_run_at) || "none"],
    ["tasks_path", payload.tasks_path || ""],
    ["lock_path", payload.lock_path || ""],
    ["lock_present", payload.lock_present ? "yes" : "no"],
  ]);
  return { kind: "system", title: "Cron", text: formatDisplayAsText(display), display };
}

export function presentCronDelete(payload: CronTaskDeleteResponse | Record<string, any>): MessageBlock {
  const display = kv([
    ["deleted", payload.deleted ? "yes" : "no"],
    ["id", payload.id || ""],
  ]);
  return {
    kind: "system",
    title: "Cron",
    text: payload.deleted ? `Deleted scheduled task ${payload.id}.` : `No scheduled task found: ${payload.id}`,
    display,
  };
}

export function presentCronCreated(task: CronTask | Record<string, any>): MessageBlock {
  const display = kv([
    ["scheduled", task.id],
    ["cron", task.cron],
    ["next_run_at", formatCronTime(task.next_run_at) || "unknown"],
    ["prompt", task.prompt],
  ]);
  return { kind: "system", title: "Loop", text: formatDisplayAsText(display), display };
}

function kv(items: [string, unknown][]): MessageDisplay {
  return {
    type: "kv",
    items: items.map(([label, value]) => ({ label, value: stringValue(value) })),
  };
}

function table(columns: [string, string][], rows: Record<string, unknown>[]): MessageDisplay {
  return {
    type: "table",
    columns: columns.map(([key, label]) => ({ key, label })),
    rows: rows.map((row) =>
      Object.fromEntries(Object.entries(row).map(([key, value]) => [key, stringValue(value)])),
    ),
  };
}

function formatTable(columns: { key: string; label: string }[], rows: Record<string, string>[]): string {
  if (rows.length === 0) return "";
  const widths = columns.map((column) =>
    Math.max(column.label.length, ...rows.map((row) => stringValue(row[column.key]).length)),
  );
  return [
    columns.map((column, i) => column.label.padEnd(widths[i])).join("  "),
    widths.map((width) => "-".repeat(width)).join("  "),
    ...rows.map((row) => columns.map((column, i) => stringValue(row[column.key]).padEnd(widths[i])).join("  ")),
  ].join("\n");
}

function stringValue(value: unknown): string {
  if (value == null) return "";
  return String(value);
}

function formatCronTime(value: number | null | undefined): string {
  if (!value) return "";
  return new Date(value * 1000).toLocaleString();
}

function truncateCronPrompt(prompt: string): string {
  const normalized = String(prompt || "").replace(/\s+/g, " ").trim();
  return normalized.length > 80 ? `${normalized.slice(0, 77)}...` : normalized;
}

function truncateOneLine(value: string, maxLength: number): string {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, Math.max(0, maxLength - 3))}...`;
}
