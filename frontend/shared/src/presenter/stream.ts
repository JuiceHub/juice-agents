/**
 * Pure stream presentation helpers shared by CLI and web surfaces.
 */

import type {
  ActionStep,
  ActorSessionSnapshot,
  ActorSessionStep,
  ObservationImage,
  RunnerStreamEvent,
  TeamStreamEvent,
  ToolCallDisplay,
} from "../gateway/types.js";

export const INTERRUPT_MESSAGE = "[Request interrupted by user]";

export interface ToolCallBlock {
  name: string;
  mode: string;
  status: string;
  parameters: Record<string, string>;
}

export type MessageDisplay =
  | {
      type: "table";
      columns: { key: string; label: string }[];
      rows: Record<string, string>[];
    }
  | {
      type: "kv";
      items: { label: string; value: string }[];
    }
  | {
      type: "list";
      items: { label: string; description?: string }[];
    }
  | {
      type: "code";
      language?: string;
      content: string;
    };

export interface MessageBlock {
  id?: string;
  kind: "user" | "final" | "assistant" | "thinking" | "tool" | "error" | "system";
  text: string;
  title?: string;
  toolCall?: ToolCallBlock;
  display?: MessageDisplay;
}

export function presentStreamEvent(event: RunnerStreamEvent): MessageBlock[] {
  if (!event) return [];
  if (event.kind === "action_step") {
    if (!event.action_step) return [];
    return presentStreamStep(event.action_step);
  }
  if (event.kind === "round_end") {
    const text = event.outcome === "submitted" ? String(event.output ?? "").trim() : "";
    return text ? [{ kind: "final", text }] : [];
  }
  if (event.kind === "stream_cancelled") {
    return [{ kind: "system", text: INTERRUPT_MESSAGE }];
  }
  if (event.kind === "team_update") {
    const summary = presentTeamUpdate(event.team_event);
    return summary ? [{ kind: "system", text: summary }] : [];
  }
  return [];
}

/** Keep Team progress readable in both the CLI and Web transcript. */
export function presentTeamUpdate(teamEvent: TeamStreamEvent | null): string {
  if (!teamEvent) return "";
  const update = teamEvent.update || {};
  const snapshot = teamEvent.snapshot;
  const actor = field(teamEvent.actor) || "team";
  const eventName = field(update.type).replaceAll("_", " ");
  const tasks = Array.isArray(snapshot?.tasks) ? snapshot.tasks : [];
  const members = Array.isArray(snapshot?.members) ? snapshot.members : [];
  const task = tasks.find((item) => item.task_id === update.task_id);
  const taskName = field(update.title) || field(task?.title) || field(update.task_id);
  const memberName = field(update.member_name) || field(update.member) || field(update.name);
  const status = field(update.status) || field(task?.status);
  const claimant = field(task?.claimed_by);
  const taskStatus = status && task ? `${status}${claimant ? ` by ${claimant}` : ""}` : status;
  const action = [eventName, taskName || memberName, taskStatus].filter(Boolean).join(" · ");
  // Failed work keeps its claim and error in the snapshot until root intervenes.
  const critical = field(update.error) || field(task?.error) || field(update.stop_reason) || field(update.phase);
  const completed = tasks.filter((item) => item.status === "completed").length;
  const taskProgress = tasks.length ? `Tasks ${completed}/${tasks.length} complete` : "";
  const memberProgress = members.length
    ? `Members ${members.slice(0, 4).map((member) => `${field(member.name) || "member"}: ${member.busy ? "working" : field(member.status) === "active" ? "idle" : field(member.status) || "idle"}`).join(", ")}${members.length > 4 ? ` +${members.length - 4}` : ""}`
    : "";
  const finished = snapshot?.finished ? "Team finished" : "";
  const lifecycle = field(update.lifecycle_summary)
    || (typeof update.lifecycle_event_count === "number" && update.lifecycle_event_count > 0
      ? `${update.lifecycle_event_count} lifecycle events` : "");
  const headline = critical ? [action, critical].filter(Boolean).join(" · ") : action || lifecycle;
  const parts = [headline, taskProgress, memberProgress, finished].filter(Boolean);
  return parts.length ? `${actor}: ${parts.join(" · ")}` : "";
}

function field(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function presentStreamStep(step: ActionStep): MessageBlock[] {
  if (!step) return [];

  if (step.error) {
    const toolBlocks = presentToolBlocks(step);
    return toolBlocks.length > 0
      ? [...toolBlocks, { kind: "error", text: step.error }]
      : [{ kind: "error", text: step.error }];
  }

  const blocks: MessageBlock[] = [];
  const thought = extractThought(step);
  if (thought) {
    blocks.push({ kind: "thinking", text: thought });
  }
  blocks.push(...presentToolBlocks(step));
  blocks.push(...presentObservationImageBlocks(step.observation_images));
  return blocks;
}

export function presentObservationImageBlocks(images: ObservationImage[] | undefined): MessageBlock[] {
  const visible = (images || [])
    .map((image) => ({
      imageUrl: String(image?.image_url || "").trim(),
      description: String(image?.description || "").trim(),
    }))
    .filter((image) => image.imageUrl);
  if (visible.length === 0) return [];
  return visible.map((image) => ({
    kind: "assistant",
    title: "Image",
    text: image.description ? `${image.description}\n${image.imageUrl}` : image.imageUrl,
  }));
}

export function presentActorSession(actor: ActorSessionSnapshot | null | undefined): MessageBlock[] {
  if (!actor) {
    return [{ kind: "system", text: "No actor session selected." }];
  }

  const blocks: MessageBlock[] = [presentActorStatus(actor)];
  for (const step of actor.steps || []) {
    blocks.push(...presentActorSessionStep(step));
  }

  if (blocks.length === 1) {
    blocks.push({
      kind: "system",
      text: `No transcript entries for ${actor.actor_name || "actor"} yet.`,
    });
  }

  return blocks;
}

function presentActorStatus(actor: ActorSessionSnapshot): MessageBlock {
  const details = [
    actor.status || "unknown",
    actor.actor_kind || actor.actor_role || "actor",
    actor.current_async_task_id || "",
  ].filter(Boolean);
  return {
    kind: "system",
    text: `${actor.actor_name || "actor"}: ${details.join(" · ")}`,
  };
}

function presentActorSessionStep(step: ActorSessionStep): MessageBlock[] {
  if (!step || typeof step !== "object") return [];
  const raw = step as Record<string, any>;
  const stepType = String(raw.type || "").trim();

  if (stepType === "task") {
    const task = String(raw.task || "").trim();
    return task ? [{ kind: "user", text: task }] : [];
  }

  if (stepType === "summary") {
    const content = String(raw.content || "").trim();
    return content ? [{ kind: "system", text: content, title: "Summary" }] : [];
  }

  if (stepType === "action") {
    return presentStreamStep(coerceActorActionStep(step));
  }

  return [];
}

function coerceActorActionStep(step: ActorSessionStep): ActionStep {
  const raw = step as Record<string, any>;
  return {
    step_num: Number(raw.step_num || 0),
    model_output: String(raw.model_output || ""),
    thought: String(raw.thought || raw.model_output_blocks?.thought || ""),
    tool_calls: Array.isArray(raw.tool_calls) ? raw.tool_calls : [],
    code_action: String(raw.code_action || raw.model_output_blocks?.code || ""),
    reasoning_content: String(raw.reasoning_content || ""),
    observations: Array.isArray(raw.observations) ? raw.observations.map(String) : [],
    observation_limits: Array.isArray(raw.observation_limits)
      ? raw.observation_limits.map(Number)
      : [],
    request_bytes: Number(raw.request_bytes || 0),
    usage: raw.usage && typeof raw.usage === "object" ? raw.usage : null,
    observation_images: Array.isArray(raw.observation_images) ? raw.observation_images : [],
    attachments: Array.isArray(raw.attachments) ? raw.attachments : [],
    error: raw.error == null ? null : String(raw.error),
    round_outcome: String(raw.round_outcome || "continue") as ActionStep["round_outcome"],
    output: raw.output,
  };
}

export function extractThought(step: ActionStep): string {
  if (step.thought?.trim()) {
    return step.thought.trim();
  }
  const matches = Array.from((step.model_output || "").matchAll(/<thought>\s*(.*?)\s*<\/thought>/gs));
  return (matches[matches.length - 1]?.[1] || "").trim();
}

export function presentToolBlocks(step: ActionStep): MessageBlock[] {
  const calls = Array.isArray(step.tool_calls) ? step.tool_calls : [];
  const blocks = calls
    .filter((call) => String(call?.name || "").trim() && String(call.name) !== "submit_output")
    .map((call) => toolCallToBlock(call));
  if (step.code_action?.trim()) {
    blocks.push(
      toolCallToBlock({
        name: "Python",
        args: { code: step.code_action },
        mode: "sync",
        status: step.error ? "error" : "success",
        observation_index: step.observations?.length ? 0 : null,
        is_terminal: step.round_outcome === "submitted",
      })
    );
  }
  return blocks;
}

function toolCallToBlock(call: ToolCallDisplay): MessageBlock {
  const name = String(call.name || "").trim() || "tool";
  const parameters = selectKeyParameters(name, call.args || {});
  const suffix = formatParameterSummary(parameters);
  const status = String(call.status || "unknown");
  const title = status === "error" ? `Failed ${name}` : `Ran ${name}`;
  return {
    kind: "tool",
    title,
    text: suffix ? `${title} ${suffix}` : title,
    toolCall: {
      name,
      mode: String(call.mode || "sync"),
      status,
      parameters,
    },
  };
}

export function selectKeyParameters(toolName: string, args: Record<string, any>): Record<string, string> {
  const normalized = toolName.toLowerCase();
  if (normalized === "shell") {
    return pickParameters(args, ["command", "workdir", "background"]);
  }
  if (normalized === "python") {
    return pickParameters(args, ["code"]);
  }
  if (["read", "write", "edit"].some((part) => normalized.includes(part))) {
    return pickParameters(args, ["path", "file_path"]);
  }
  if (["grep", "search"].some((part) => normalized.includes(part))) {
    return pickParameters(args, ["pattern", "query", "path"]);
  }
  if (normalized.startsWith("browser")) {
    return pickParameters(args, ["url", "selector", "text"]);
  }
  if (normalized === "agent_tool") {
    return pickParameters(args, ["name", "task"]);
  }

  const selected: Record<string, string> = {};
  for (const [key, value] of Object.entries(args || {})) {
    if (Object.keys(selected).length >= 3) break;
    selected[key] = previewValue(value);
  }
  return selected;
}

function pickParameters(args: Record<string, any>, keys: string[]): Record<string, string> {
  const selected: Record<string, string> = {};
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(args, key)) {
      selected[key] = previewValue(args[key]);
    }
  }
  return selected;
}

function previewValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return truncate(value.trim(), 1200);
  if (["number", "boolean", "bigint"].includes(typeof value)) return String(value);
  try {
    return truncate(JSON.stringify(value), 600);
  } catch {
    return truncate(String(value), 600);
  }
}

function formatParameterSummary(parameters: Record<string, string>): string {
  const entries = Object.entries(parameters);
  if (entries.length === 0) return "";
  const [firstKey, firstValue] = entries[0] || [];
  if (!firstKey) return "";
  const compact = firstValue.includes("\n") ? firstValue.split("\n")[0] : firstValue;
  return `(${firstKey}: ${truncate(compact, 96)})`;
}

function truncate(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, Math.max(0, maxLength - 1))}...`;
}

function sanitizeTerminalModelOutput(raw: string): string {
  return (raw || "")
    .replace(/<thought>.*?<\/thought>/gs, "")
    .replace(/<actions>.*?<\/actions>/gs, "")
    .replace(/<code>.*?<\/code>/gs, "")
    .trim();
}

function sanitizeTerminalObservation(raw: string): string {
  return (raw || "")
    .replace(/<result_of_action_\d+>\s*/g, "")
    .replace(/\s*<\/result_of_action_\d+>/g, "")
    .trim();
}
