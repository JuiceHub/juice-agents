/**
* Pure stream presentation helpers shared by CLI and web surfaces.
*/
import type { ActionStep, ActiveAgentsReport, ActiveAgentSnapshot, ActiveAgentStep, ObservationImage, RunnerStreamEvent, TeamStreamEvent, ToolCallDisplay, } from "../gateway/types.js";
export const INTERRUPT_MESSAGE = "[Request interrupted by user]";
export interface ToolCallBlock {
  name: string;
  mode: string;
  status: string;
  parameters: Record<string, string>;
  callId?: string;
  taskId?: string;
  error?: string;
  /** Tool result text; keep it separate from framework-caught exceptions. */
  observation?: string;
  executionTime?: number | null;
  // Scope is presentation metadata: it lets cancellation settle only the
  // affected round without changing another Agent's concurrent tool calls.
  roundId?: string;
  agentId?: string;
}
/** Translate SDK result states and saved display aliases into one UI contract. */
export function normalizeToolStatus(status: string): string {
  const value = String(status || "unknown").trim().toLowerCase();
  if (value === "success")
    return "completed";
  if (value === "error")
    return "failed";
  return ["running", "completed", "background", "failed", "denied", "cancelled"].includes(value)
    ? value : "unknown";
}
export function toolStatusLabel(status: string): string {
  return ({
    running: "Running", completed: "Completed", background: "Launched",
    failed: "Failed", denied: "Denied", cancelled: "Cancelled", unknown: "Unconfirmed"
  } as Record<string, string>)[normalizeToolStatus(status)];
}
/** Rebuild text as well as structured data whenever a lifecycle state changes. */
export function presentToolLifecycle(toolCall: ToolCallBlock, id?: string): MessageBlock {
  const call = { ...toolCall, status: normalizeToolStatus(toolCall.status) };
  const title = `${toolStatusLabel(call.status)} ${call.name}`;
  const summary = formatParameterSummary(call.parameters);
  // CodeAct includes its complete exception in the result. Keep distinct
  // output/error details without repeating an exception already shown there.
  const details = [call.observation,
    call.error && !call.observation?.includes(call.error) ? call.error : undefined,
  ].filter(Boolean);
  return {
    ...(id ? { id } : {}), kind: "tool", title,
    text: [summary ? `${title} ${summary}` : title, ...details].join("\n"),
    toolCall: call
  };
}
export type MessageDisplay = {
  type: "table";
  columns: {
    key: string;
    label: string;
  }[];
  rows: Record<string, string>[];
} | {
  type: "kv";
  items: {
    label: string;
    value: string;
  }[];
} | {
  type: "list";
  items: {
    label: string;
    description?: string;
  }[];
} | {
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
/** Stable IDs let snapshots and lifecycle events enrich the same transcript row. */
function eventKey(event: RunnerStreamEvent, detail: string): string {
  return JSON.stringify([event.runner_id, event.agent_id, event.round_id || "", event.step_num ?? 0, detail]);
}
type InputScope = Pick<RunnerStreamEvent, "runner_id" | "agent_id" | "round_id">;
/** Inbox acceptance, committed steps and restored history share message IDs. */
function presentAttachments(attachments: Record<string, any>[] | undefined, scope?: InputScope): MessageBlock[] {
  return (attachments || []).flatMap((attachment): MessageBlock[] => {
    const payload = attachment.payload || {};
    const type = attachment.attachment_type;
    const messageId = String(attachment.message_id || payload.message_id || "");
    const id = messageId ? `message:${JSON.stringify([scope?.runner_id || "", scope?.agent_id || "", messageId])}` : undefined;
    const text = String(payload.text || "").trim();
    if (["user_message", "agent_user_message", "task_assignment"].includes(type))
      return text ? [{ id, kind: "user", text }] : [];
    if (type === "inbox_message") {
      const body = String(payload.content || payload.text || "").trim();
      const sender = String(payload.from || payload.sender || "team").trim();
      return body ? [{ id, kind: "system", text: `${sender}: ${body}` }] : [];
    }
    if (type === "async_task_notification") {
      const status = String(payload.status || "unknown");
      const task = String(payload.async_task_id || "background task");
      const summary = String(payload.summary || "").trim();
      return [{ id, kind: status === "failed" ? "error" : "system",
        text: `${task}: ${status}${summary ? ` · ${summary}` : ""}` }];
    }
    // Prompt-only capability, goal and plan attachments are not chat messages.
    return [];
  });
}
function presentTaskInput(input: { task?: unknown; attachments?: Record<string, any>[] }, scope?: InputScope): MessageBlock[] {
  const blocks = presentAttachments(input.attachments, scope);
  const task = String(input.task || "").trim();
  // Scheduled task text repeats the user/task-assignment attachments. Keep
  // each original message separate and suppress only that aggregate copy.
  const representedText = (input.attachments || [])
    .filter((item) => ["user_message", "agent_user_message", "task_assignment", "inbox_message"].includes(item.attachment_type))
    .map((item) => String(item.payload?.text || "")).filter(Boolean).join("\n").trim();
  if (task && task !== representedText)
    blocks.unshift({ kind: "user", text: task,
      ...(scope ? { id: `task:${JSON.stringify([scope.runner_id, scope.agent_id, scope.round_id || ""])}` } : {}) });
  return blocks;
}
function presentCall(call: ToolCallDisplay, observation?: string, event?: RunnerStreamEvent): MessageBlock {
  const callId = String(call.call_id || "").trim();
  const id = event && callId ? `tool:${JSON.stringify([
    event.runner_id, event.agent_id, event.round_id || "", callId,
  ])}` : undefined;
  return presentToolLifecycle({
    ...(callId ? { callId } : {}), ...(call.task_id ? { taskId: call.task_id } : {}),
    ...(call.error ? { error: String(call.error).trim() } : {}),
    ...(observation && !call.is_terminal ? { observation: observation.trim() } : {}),
    ...(call.execution_time != null ? { executionTime: call.execution_time } : {}),
    ...(event ? { roundId: event.round_id || "", agentId: event.agent_id } : {}),
    name: call.name, mode: call.mode || "foreground", status: normalizeToolStatus(call.status),
    parameters: selectKeyParameters(call.name, call.args || {})
  }, id);
}
export function presentStreamEvent(event: RunnerStreamEvent): MessageBlock[] {
  if (!event)
    return [];
  if (event.kind === "message_received")
    return presentAttachments(event.data.attachments, event);
  if (event.kind === "round_start")
    return event.data.input ? presentTaskInput(event.data.input, event) : [];
  if (event.kind === "thinking") {
    const thought = String(event.data.thought || event.data.content || "").trim();
    return thought ? [{ kind: "thinking", text: thought, id: `thinking:${eventKey(event, "thought")}` }] : [];
  }
  if (event.kind === "tool_start" || event.kind === "tool_end") {
    const data = event.data;
    if (data.tool_name === "submit_output" || !data.call_id)
      return [];
    return [presentCall({
        name: String(data.tool_name || "tool"), args: data.tool_args || {},
        mode: data.execution_mode || "foreground", call_id: data.call_id,
        status: event.kind === "tool_start" ? "running" : String(data.status || "unknown"),
        task_id: data.task_id, error: data.error, execution_time: data.execution_time,
        observation_index: null, is_terminal: Boolean(data.is_terminal)
      }, event.kind === "tool_end" ? String(data.observation ?? "") : undefined, event)];
  }
  if (event.kind === "step_end") {
    // The committed step is authoritative and reuses the same call/thought IDs.
    // Missing live deliveries are restored without duplicating existing rows.
    const step = event.data.step;
    return presentStreamStep(step, event).map((block, index) => {
      if (block.id)
        return block;
      if (block.kind === "thinking")
        return { ...block, id: `thinking:${eventKey(event, "thought")}` };
      if (block.toolCall?.callId) {
        const call = step.tool_calls.find((item) => item.call_id === block.toolCall?.callId);
        if (call)
          return presentCall(call, call.observation_index == null ? undefined : step.observations[call.observation_index], event);
      }
      return { ...block, id: `step:${eventKey(event, String(index))}` };
    });
  }
  if (event.kind === "round_end") {
    const result = event.data.result;
    const id = `round:${JSON.stringify([event.runner_id, event.agent_id, result.round_id])}`;
    if (result.status === "cancelled")
      return [{ id, kind: "system", text: INTERRUPT_MESSAGE }];
    if (result.status === "failed")
      return [{ id, kind: "error", text: result.error || result.reason || "Round failed" }];
    const text = String(result.output ?? "").trim();
    return text ? [{ id, kind: "final", text }] : [];
  }
  if (event.kind === "team_update") {
    const text = presentTeamUpdate(event.data);
    return text ? [{ kind: "system", text }] : [];
  }
  return [];
}
/** Keep Team progress readable in both the CLI and Web transcript. */
export function presentTeamUpdate(teamEvent: TeamStreamEvent | null): string {
  if (!teamEvent)
    return "";
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
export function presentStreamStep(step: ActionStep, scope?: InputScope): MessageBlock[] {
  if (!step)
    return [];
  if (step.error) {
    const toolBlocks = presentToolBlocks(step);
    const blocks: MessageBlock[] = toolBlocks.length > 0
      ? [...toolBlocks, { kind: "error", text: step.error }]
      : [{ kind: "error", text: step.error }];
    return [...blocks, ...presentAttachments(step.attachments, scope)];
  }
  const blocks: MessageBlock[] = [];
  const thought = extractThought(step);
  if (thought) {
    blocks.push({ kind: "thinking", text: thought });
  }
  blocks.push(...presentToolBlocks(step));
  blocks.push(...presentObservationImageBlocks(step.observation_images));
  blocks.push(...presentAttachments(step.attachments, scope));
  return blocks;
}
export function presentObservationImageBlocks(images: ObservationImage[] | undefined): MessageBlock[] {
  const visible = (images || [])
    .map((image) => ({
    imageUrl: String(image?.image_url || "").trim(),
    description: String(image?.description || "").trim()
  }))
    .filter((image) => image.imageUrl);
  if (visible.length === 0)
    return [];
  return visible.map((image) => ({
    kind: "assistant",
    title: "Image",
    text: image.description ? `${image.description}\n${image.imageUrl}` : image.imageUrl
  }));
}
export function presentActiveAgent(actor: ActiveAgentSnapshot | null | undefined, presentStep: (step: ActionStep) => MessageBlock[] = presentStreamStep): MessageBlock[] {
  if (!actor) {
    return [{ kind: "system", text: "No actor session selected." }];
  }
  const blocks: MessageBlock[] = [presentActorStatus(actor)];
  const resultRounds = committedResultRounds(actor);
  for (const step of actor.steps || []) {
    blocks.push(...presentActiveAgentStep(step, presentStep, !resultRounds.has(String((step as Record<string, any>).round_id || ""))));
  }
  blocks.push(...presentAttachments(actor.pending_messages));
  if (blocks.length === 1) {
    blocks.push({
      kind: "system",
      text: `No transcript entries for ${actor.agent_name || "actor"} yet.`
    });
  }
  return blocks;
}
/** Build saved child views with the same row identities as live events. */
export function presentAgentSession(report: ActiveAgentsReport, actor: ActiveAgentSnapshot): MessageBlock[] {
  const blocks: MessageBlock[] = [{ ...presentActorStatus(actor), id: `agent-status:${actor.agent_id}` }];
  const resultRounds = committedResultRounds(actor);
  for (const [index, entry] of actor.steps.entries()) {
    const raw = entry as Record<string, any>;
    if (raw.type === "round_result") {
      const result = raw.result as import("../gateway/types.js").RoundResult;
      blocks.push(...presentStreamEvent({ runner_id: report.runner_id, agent_id: actor.agent_id,
        agent_name: actor.agent_name, agent_role: actor.agent_role, round_id: result.round_id,
        event_id: `snapshot:${actor.agent_id}:${index}`, timestamp: 0,
        kind: "round_end", data: { result } }));
      continue;
    }
    if (raw.type === "task") {
      blocks.push(...presentTaskInput(raw, { runner_id: report.runner_id, agent_id: actor.agent_id,
        round_id: String(raw.round_id || "") }));
      continue;
    }
    if (raw.type !== "action") {
      blocks.push(...presentActiveAgentStep(entry, presentStreamStep).map((block) => ({
        ...block, id: `agent-history:${actor.agent_id}:${index}`,
      })));
      continue;
    }
    const action = coerceActorActionStep(entry);
    const scope = { runner_id: report.runner_id, agent_id: actor.agent_id, agent_name: actor.agent_name,
      agent_role: actor.agent_role, round_id: String(raw.round_id || ""), step_num: action.step_num,
      event_id: `snapshot:${actor.agent_id}:${index}`, timestamp: 0 };
    blocks.push(...presentStreamEvent({ ...scope, kind: "step_end", data: { step: action } }));
    if (action.round_outcome === "submitted" && !resultRounds.has(scope.round_id)) blocks.push(...presentStreamEvent({ ...scope,
      kind: "round_end", data: { result: { round_id: scope.round_id, status: "completed", output: action.output, reason: "", error: null } },
    }));
  }
  blocks.push(...presentAttachments(actor.pending_messages, {
    runner_id: report.runner_id, agent_id: actor.agent_id,
  }));
  return blocks;
}

/** An explicit durable result always takes precedence over protocol intent. */
function committedResultRounds(actor: ActiveAgentSnapshot): Set<string> {
  return new Set(actor.steps.flatMap((entry) => {
    const raw = entry as Record<string, any>;
    return raw.type === "round_result" && raw.result?.round_id ? [String(raw.result.round_id)] : [];
  }));
}

function presentActorStatus(actor: ActiveAgentSnapshot): MessageBlock {
  const details = [
    actor.status || "unknown",
    actor.agent_kind || actor.agent_role || "actor",
    actor.current_async_task_id || "",
  ].filter(Boolean);
  return {
    kind: "system",
    text: `${actor.agent_name || "actor"}: ${details.join(" · ")}`
  };
}
function presentActiveAgentStep(step: ActiveAgentStep, presentStep: (step: ActionStep) => MessageBlock[], includeSubmittedOutput = true): MessageBlock[] {
  if (!step || typeof step !== "object")
    return [];
  const raw = step as Record<string, any>;
  const stepType = String(raw.type || "").trim();
  if (stepType === "round_result") {
    const result = raw.result as import("../gateway/types.js").RoundResult;
    if (result.status === "cancelled")
      return [{ kind: "system", text: INTERRUPT_MESSAGE }];
    if (result.status === "failed")
      return [{ kind: "error", text: result.error || result.reason || "Round failed" }];
    const text = String(result.output ?? "").trim();
    return text ? [{ kind: "final", text }] : [];
  }
  if (stepType === "task") {
    return presentTaskInput(raw);
  }
  if (stepType === "summary") {
    const content = String(raw.content || "").trim();
    return content ? [{ kind: "system", text: content, title: "Summary" }] : [];
  }
  if (stepType === "action") {
    const action = coerceActorActionStep(step);
    const blocks = presentStep(action);
    // 实时回答由 round_end 展示；恢复历史没有该事件，需要从已提交的
    // ActionStep 补回最终输出。保留 0/false 等有效值，只忽略空输出。
    const output = includeSubmittedOutput && action.round_outcome === "submitted" ? String(action.output ?? "").trim() : "";
    if (output)
      blocks.push({ kind: "final", text: output });
    return blocks;
  }
  return [];
}
function coerceActorActionStep(step: ActiveAgentStep): ActionStep {
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
    output: raw.output
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
  const python = step.code_action?.trim() ? calls.find((call) => call.name === "Python") : undefined;
  if (python) {
    // New CodeAct sessions persist the public Python wrapper. Reuse it rather
    // than adding a synthetic success beside the saved wrapper and inner calls.
    return [presentToolLifecycle({
        name: python.name,
        mode: python.mode,
        status: python.status,
        parameters: selectKeyParameters(python.name, python.args || {}),
        callId: python.call_id,
        error: python.error || undefined,
        observation: python.is_terminal || python.observation_index == null
          ? undefined : step.observations?.[python.observation_index],
        executionTime: python.execution_time
      })];
  }
  // CodeAct exposes the Python wrapper only. Inner tool calls are audit
 // details; a partial snapshot without its wrapper cannot invent one.
 if (step.code_action?.trim()) return [];
 return calls
  .filter((call) => String(call?.name || "").trim() && call.name !== "submit_output")
  .map((call) => presentCall(call, call.observation_index == null ? undefined : step.observations?.[call.observation_index]));
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
    if (Object.keys(selected).length >= 3)
      break;
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
  if (value == null)
    return "";
  if (typeof value === "string")
    return truncate(value.trim(), 1200);
  if (["number", "boolean", "bigint"].includes(typeof value))
    return String(value);
  try {
    return truncate(JSON.stringify(value), 600);
  }
  catch {
    return truncate(String(value), 600);
  }
}
function formatParameterSummary(parameters: Record<string, string>): string {
  const entries = Object.entries(parameters);
  if (entries.length === 0)
    return "";
  const [firstKey, firstValue] = entries[0] || [];
  if (!firstKey)
    return "";
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
