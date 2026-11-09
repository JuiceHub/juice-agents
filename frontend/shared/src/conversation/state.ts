import type { ActiveAgentsReport, ActiveAgentSnapshot, AgentStateData, RoundResult, RunnerLifecycleEvent, RunnerStreamEvent, } from "../gateway/types.js";
import { normalizeToolStatus, presentAgentSession, presentStreamEvent, presentToolLifecycle, type MessageBlock, } from "../presenter/stream.js";
/** One reducer owns transport deduplication, committed rounds and transcript rows. */
export interface ConversationState {
  messages: MessageBlock[];
  agentMessages: Record<string, MessageBlock[]>;
  agentStates: Record<string, AgentStateData>;
  agentReport: ActiveAgentsReport | null;
  streaming: boolean;
  stopping: boolean;
  streamInFlight: boolean;
  streamFailed: boolean;
  activeGeneration: number | null;
  inFlightTokens: string[];
  lifecycleEvent: RunnerLifecycleEvent | null;
  workflowProgress: Record<string, RunnerLifecycleEvent>;
  runnerId: string;
  activeRounds: Record<string, RunnerStreamEvent>;
  completedRounds: string[];
  roundResults: Record<string, RoundResult>;
  stoppingRounds: string[];
  stoppingGenerations: number[];
  seenEventIds: string[];
}
export type ConversationAction = {
  type: "stream_started";
  generation: number;
  token: string;
  message: string;
} | {
  type: "stream_event" | "ambient_event";
  generation?: number;
  event?: RunnerStreamEvent;
  blocks?: MessageBlock[];
  lifecycleEvent?: RunnerLifecycleEvent | null;
} | {
  type: "cancel_requested";
  roundId: string;
  generation?: number;
} | {
  type: "cancel_rejected";
  roundId: string;
  generation?: number;
  message?: string;
} | {
  type: "stream_settled";
  generation: number;
  token: string;
} | {
  type: "stream_failed";
  generation: number;
  message: string;
} | {
  type: "stream_succeeded";
  generation: number;
  roundId?: string;
} | {
  type: "transport_failed";
  message: string;
} | {
  type: "scope_changed";
  runnerId: string;
} | {
  type: "snapshot_received";
  report: ActiveAgentsReport;
} | {
  type: "messages_added";
  messages: MessageBlock[];
} | {
  type: "messages_replaced";
  messages: MessageBlock[];
} | {
  type: "message_removed";
  id: string;
} | {
  type: "messages_cleared";
};
export function createConversationState(messages: MessageBlock[] = []): ConversationState {
  return {
    messages, agentMessages: {}, agentStates: {}, agentReport: null,
    streaming: false, stopping: false, streamInFlight: false, streamFailed: false,
    activeGeneration: null, inFlightTokens: [], lifecycleEvent: null, workflowProgress: {},
    runnerId: "", activeRounds: {}, completedRounds: [], roundResults: {}, stoppingRounds: [], stoppingGenerations: [], seenEventIds: []
  };
}
/** Calls finishing before their start retain their terminal status and position. */
function mergeBlocks(messages: MessageBlock[], incoming: MessageBlock[]): MessageBlock[] {
  if (!incoming.length)
    return messages;
  const result = [...messages];
  const positions = new Map(messages.flatMap((block, index) => block.id ? [[block.id, index] as const] : []));
  for (const block of incoming) {
    const index = block.id ? positions.get(block.id) : undefined;
    if (index === undefined) {
      if (block.id)
        positions.set(block.id, result.length);
      result.push(block);
      continue;
    }
    const previous = result[index];
    if (!previous.toolCall || !block.toolCall) {
      result[index] = block;
      continue;
    }
    const oldStatus = normalizeToolStatus(previous.toolCall.status);
    const nextStatus = normalizeToolStatus(block.toolCall.status);
    const terminal = !["running", "unknown"].includes(oldStatus);
    // A sparse late start may enrich arguments but cannot rename a completed
    // tool to the generic fallback or erase result metadata.
    const call = { ...previous.toolCall, ...block.toolCall };
    if (call.name === "tool")
      call.name = previous.toolCall.name;
    call.parameters = { ...previous.toolCall.parameters, ...block.toolCall.parameters };
    call.status = terminal && ["running", "unknown"].includes(nextStatus) ? oldStatus : nextStatus;
    result[index] = presentToolLifecycle(call, previous.id);
  }
  return result;
}
function updateAgentReport(report: ActiveAgentsReport | null, event: RunnerStreamEvent): ActiveAgentsReport | null {
  // An ambient child event may arrive before the first snapshot. Preserve its
  // backend identity immediately so both clients can select that transcript.
  const current = report || { runner_id: event.runner_id, permission_mode: event.permission_mode || "",
    agent_mode: event.agent_mode || "", root_agent_name: "", agents: [] };
  let agent: ActiveAgentSnapshot = current.agents.find((item) => item.agent_id === event.agent_id) || {
    agent_id: event.agent_id, agent_name: event.agent_name, agent_role: event.agent_role,
    agent_kind: "", status: "unknown", is_root: event.agent_role === "root",
    current_async_task_id: "", metadata: {}, session: {}, steps: [],
  };
  if (event.kind === "agent_state") {
    agent = { ...agent, status: event.data.status, agent_kind: event.data.lifecycle || agent.agent_kind,
      is_root: event.data.is_root ?? agent.is_root,
      session: { ...agent.session, round_checkpoint: event.data.round_checkpoint || agent.session.round_checkpoint } };
  }
  if (event.kind === "round_start" && event.data.input) {
    const exists = agent.steps.some((step) => step.type === "task"
      && (step as Record<string, unknown>).round_id === event.round_id);
    if (!exists) agent = { ...agent, steps: [...agent.steps,
      { type: "task", round_id: event.round_id, ...event.data.input }] };
  }
  if (event.kind === "round_end") {
    agent = { ...agent, steps: [...agent.steps,
      { type: "round_result", round_id: event.round_id, result: event.data.result }] };
  }
  if (event.kind === "step_end") {
    const step = { ...event.data.step, type: "action", round_id: event.round_id };
    const index = agent.steps.findIndex((item) => {
      const saved = item as Record<string, unknown>;
      return saved.type === "action" && saved.round_id === event.round_id && saved.step_num === event.data.step.step_num;
    });
    const steps = [...agent.steps];
    if (index < 0)
      steps.push(step);
    else
      steps[index] = step;
    agent = { ...agent, steps };
  }
  const agents = current.agents.some((item) => item.agent_id === event.agent_id)
    ? current.agents.map((item) => item.agent_id === event.agent_id ? agent : item)
    : [...current.agents, agent];
  return { ...current, agents, root_agent_name: agent.is_root ? agent.agent_name : current.root_agent_name };
}
/** Only a committed round result can settle an unfinished tool row. */
function settleRound(messages: MessageBlock[], event: RunnerStreamEvent, result?: RoundResult): MessageBlock[] {
  if (!result)
    return messages;
  return messages.map((block) => {
    const call = block.toolCall;
    if (!call || call.status !== "running" || call.roundId !== event.round_id || call.agentId !== event.agent_id)
      return block;
    return presentToolLifecycle({ ...call, status: result.status === "cancelled" ? "cancelled" : "unknown" }, block.id);
  });
}
function rootActive(activeRounds: ConversationState["activeRounds"]): boolean {
  return Object.values(activeRounds).some((event) => event.agent_role === "root");
}

/** Give optimistic input and previously rendered history their backend identity.
 *
 * stream_started precedes the gateway receipt, so that row has no persisted ID
 * yet. Match each identical input once; repeated prompts must remain separate.
 * Tool/thought/result rows already have stable IDs shared with the presenter.
 */
function mergeIdentifiedBlocks(messages: MessageBlock[], saved: MessageBlock[]): MessageBlock[] {
  const result = [...messages];
  const used = new Set<number>();
  for (const block of saved) {
    if (!block.id || result.some((item) => item.id === block.id))
      continue;
    const index = result.findIndex((item, position) => !used.has(position)
      && item.kind === block.kind && !item.id && (block.toolCall?.callId
        ? item.toolCall?.callId === block.toolCall.callId : item.text === block.text));
    if (index >= 0) {
      result[index] = block;
      used.add(index);
    }
  }
  return mergeBlocks(result, saved);
}

/** A snapshot proves whether an Agent still executes a call even after a gap. */
function reconcileSnapshotTools(messages: MessageBlock[], agent: ActiveAgentSnapshot,
  results: ConversationState["roundResults"]): MessageBlock[] {
  return messages.map((block) => {
    const call = block.toolCall;
    if (!call || call.status !== "running" || call.agentId !== agent.agent_id)
      return block;
    const result = call.roundId ? results[call.roundId] : undefined;
    const activeRound = String(agent.session.round_checkpoint?.round_id || "");
    if (!result && agent.status === "running" && (!activeRound || !call.roundId || call.roundId === activeRound))
      return block;
    return presentToolLifecycle({ ...call,
      status: result?.status === "cancelled" ? "cancelled" : "unknown" }, block.id);
  });
}

export function conversationReducer(state: ConversationState, action: ConversationAction): ConversationState {
  if (action.type === "scope_changed") {
    return state.runnerId === action.runnerId ? state : { ...createConversationState(state.messages), runnerId: action.runnerId };
  }
  if (action.type === "snapshot_received") {
    if (state.runnerId && action.report.runner_id !== state.runnerId)
      return state;
    const agentStates = Object.fromEntries(action.report.agents.map((agent) => [agent.agent_id, {
        status: agent.status as AgentStateData["status"], is_root: agent.is_root
      }]));
    // Rebuild round identity from the authoritative checkpoint, not just Agent
    // status: the same Agent may have finished A and started B while offline.
    const activeRounds: ConversationState["activeRounds"] = {};
    const roundResults = { ...state.roundResults };
    const completedRounds = [...state.completedRounds];
    const savedByAgent: Record<string, MessageBlock[]> = {};
    for (const agent of action.report.agents) {
      const checkpoint = agent.session.round_checkpoint || {};
      const roundId = String(checkpoint.round_id || "");
      const scope = { runner_id: action.report.runner_id, agent_id: agent.agent_id,
        agent_name: agent.agent_name, agent_role: agent.agent_role, round_id: roundId,
        event_id: `snapshot:${action.report.runner_id}:${agent.agent_id}:${roundId}`, timestamp: 0 };
      if (agent.status === "running" && roundId && checkpoint.status === "active") {
        activeRounds[roundId] = state.activeRounds[roundId] || {
          ...scope, kind: "round_start", data: { resumed: true, message_ids: checkpoint.message_ids || [] },
        };
      } else if (agent.status === "running" && !roundId) {
        // In-memory reports from embedders may omit checkpoint details; retain
        // their known identity without inventing a new cancellation target.
        Object.assign(activeRounds, Object.fromEntries(Object.entries(state.activeRounds)
          .filter(([, event]) => event.agent_id === agent.agent_id)));
      }
      const saved = presentAgentSession(action.report, agent);
      // Journals are projected as interleaved round_result entries, so missed
      // results from earlier rounds also protect against late starts and tools.
      for (const entry of agent.steps) {
        const raw = entry as Record<string, any>;
        if (raw.type !== "round_result" || !raw.result?.round_id) continue;
        const result = raw.result as RoundResult;
        roundResults[result.round_id] = result;
        if (!completedRounds.includes(result.round_id)) completedRounds.push(result.round_id);
      }
      const result = checkpoint.result as RoundResult | undefined;
      if (result && result.round_id === roundId && ["completed", "failed", "cancelled"].includes(result.status)) {
        roundResults[roundId] = result;
        if (!completedRounds.includes(roundId)) completedRounds.push(roundId);
        // A persisted result is as authoritative as a received round_end. This
        // recovers cancellations/failures that have no submitted ActionStep.
        saved.push(...presentStreamEvent({ ...scope, kind: "round_end", data: { result } }));
      }
      savedByAgent[agent.agent_id] = saved;
    }
    const agentMessages = Object.fromEntries(action.report.agents.map((agent) => [agent.agent_id,
      reconcileSnapshotTools(mergeIdentifiedBlocks(state.agentMessages[agent.agent_id] || [],
        savedByAgent[agent.agent_id]), agent, roundResults),
    ]));
    const root = action.report.agents.find((agent) => agent.is_root);
    const messages = root ? reconcileSnapshotTools(mergeIdentifiedBlocks(state.messages,
      savedByAgent[root.agent_id].filter((block) => block.id !== `agent-status:${root.agent_id}`)), root, roundResults) : state.messages;
    const stoppingRounds = state.stoppingRounds.filter((id) => Boolean(activeRounds[id]));
    const workflowProgress = Object.fromEntries(Object.entries(state.workflowProgress)
      .filter(([, progress]) => Boolean(progress.round_id && activeRounds[String(progress.round_id)])));
    const lifecycleEvent = Object.values(workflowProgress)
      .filter((progress) => action.report.agents.some((agent) => agent.is_root && agent.agent_id === progress.agent_id))
      .at(-1) || null;
    return { ...state, runnerId: action.report.runner_id, agentReport: action.report, agentStates, agentMessages,
      activeRounds, roundResults, completedRounds, messages, workflowProgress, lifecycleEvent,
      streaming: action.report.agents.some((agent) => agent.is_root && agent.status === "running"),
      stopping: stoppingRounds.length > 0 || state.stoppingGenerations.length > 0, stoppingRounds };
  }
  if ((action.type === "stream_event" || action.type === "ambient_event") && action.event) {
    const event = action.event;
    if (state.runnerId && state.runnerId !== event.runner_id)
      return state;
    if (state.seenEventIds.includes(event.event_id))
      return state;
    const roundId = event.round_id || "";
    const activeRounds = { ...state.activeRounds };
    let completedRounds = state.completedRounds;
    if (event.kind === "round_start" && !completedRounds.includes(roundId))
      activeRounds[roundId] = event;
    if (event.kind === "round_end") {
      delete activeRounds[roundId];
      if (!completedRounds.includes(roundId))
        completedRounds = [...completedRounds, roundId];
    }
    const duplicateTerminal = event.kind === "round_end" && state.completedRounds.includes(roundId);
    // A delayed running summary cannot reopen an instance whose matching
    // result was already committed; a new round has a different identity.
    const staleAgentState = event.kind === "agent_state" && event.data.status === "running"
      && completedRounds.includes(roundId);
    const roundResults = event.kind === "round_end" && !duplicateTerminal
      ? { ...state.roundResults, [roundId]: event.data.result } : state.roundResults;
    const blocks = duplicateTerminal ? [] : action.blocks ?? presentStreamEvent(event);
    const agentMessages = { ...state.agentMessages,
      [event.agent_id]: settleRound(mergeIdentifiedBlocks(state.agentMessages[event.agent_id] || [], blocks), event, roundResults[roundId]) };
    const workflowProgress = { ...state.workflowProgress };
    if (event.kind === "workflow_progress") {
      // A workflow invocation owns its own progress even when two runs share
      // names or overlap on a single calling Agent.
      const key = `${event.agent_id}:${event.data.call_id || event.data.workflow_run_id || event.event_id}`;
      workflowProgress[key] = { ...event.data, round_id: roundId, agent_id: event.agent_id };
    }
    if (event.kind === "round_end") {
      for (const [key, progress] of Object.entries(workflowProgress)) {
        if (progress.agent_id === event.agent_id && progress.round_id === roundId) delete workflowProgress[key];
      }
    }
    const stoppingRounds = event.kind === "round_end"
      ? state.stoppingRounds.filter((id) => id !== roundId) : state.stoppingRounds;
    return {
      ...state, runnerId: state.runnerId || event.runner_id, activeRounds, completedRounds, roundResults, agentMessages,
      agentReport: duplicateTerminal || staleAgentState ? state.agentReport : updateAgentReport(state.agentReport, event),
      agentStates: event.kind === "agent_state" && !staleAgentState ? { ...state.agentStates, [event.agent_id]: event.data } : state.agentStates,
      streaming: ["round_start", "round_end"].includes(event.kind) ? rootActive(activeRounds) : state.streaming,
      stoppingRounds, stopping: stoppingRounds.length > 0 || state.stoppingGenerations.length > 0,
      seenEventIds: [...state.seenEventIds, event.event_id], workflowProgress,
      messages: event.agent_role === "root" ? settleRound(mergeIdentifiedBlocks(state.messages, blocks), event, roundResults[roundId]) : state.messages,
      lifecycleEvent: event.kind === "workflow_progress" && event.agent_role === "root" ? event.data
        : event.kind === "round_end" && event.agent_role === "root"
        ? Object.values(workflowProgress).filter((progress) => progress.agent_id === event.agent_id).at(-1) || null
        : state.lifecycleEvent
    };
  }
  if (action.type === "cancel_requested") {
    // Before round_start there is no trustworthy round identity. Track the
    // submitting request instead; an unrelated round_end cannot acknowledge it.
    const stoppingRounds = action.roundId && !state.stoppingRounds.includes(action.roundId)
      ? [...state.stoppingRounds, action.roundId] : state.stoppingRounds;
    const stoppingGenerations = !action.roundId && action.generation !== undefined
      && !state.stoppingGenerations.includes(action.generation)
      ? [...state.stoppingGenerations, action.generation] : state.stoppingGenerations;
    return { ...state, stopping: stoppingRounds.length > 0 || stoppingGenerations.length > 0,
      stoppingRounds, stoppingGenerations };
  }
  if (action.type === "cancel_rejected") {
    const stoppingRounds = state.stoppingRounds.filter((id) => id !== action.roundId);
    const stoppingGenerations = state.stoppingGenerations.filter((generation) => generation !== action.generation);
    return { ...state, stoppingRounds, stoppingGenerations,
      stopping: stoppingRounds.length > 0 || stoppingGenerations.length > 0,
      messages: action.message ? [...state.messages, { kind: "error", text: action.message }] : state.messages };
  }
  if (action.type === "stream_started") {
    const inFlightTokens = [...state.inFlightTokens, action.token];
    return { ...state, messages: [...state.messages, { kind: "user", text: action.message }],
      streaming: true, streamInFlight: true, streamFailed: false, activeGeneration: action.generation, inFlightTokens };
  }
  if (action.type === "stream_event" || action.type === "ambient_event") {
    if (action.type === "stream_event" && state.activeGeneration !== action.generation)
      return state;
    return { ...state, messages: mergeBlocks(state.messages, action.blocks || []),
      lifecycleEvent: action.lifecycleEvent === undefined ? state.lifecycleEvent : action.lifecycleEvent };
  }
  if (action.type === "messages_added")
    return { ...state, messages: mergeBlocks(state.messages, action.messages) };
  if (action.type === "messages_replaced")
    return { ...state, messages: action.messages };
  if (action.type === "message_removed")
    return { ...state, messages: state.messages.filter((message) => message.id !== action.id) };
  if (action.type === "messages_cleared")
    return { ...state, messages: [] };
  if (action.type === "transport_failed" || action.type === "stream_failed") {
    if (action.type === "stream_failed" && state.activeGeneration !== action.generation)
      return state;
    // Request rejection/disconnection cannot prove that execution stopped.
    const last = state.messages.at(-1);
    return { ...state, streamFailed: true, messages: last?.kind === "error" && last.text === action.message
      ? state.messages : [...state.messages, { kind: "error", text: action.message }] };
  }
  if (action.type === "stream_succeeded") {
    // The RPC result binds this request to its concrete round; it does not
    // invent termination. Events or recovery snapshots must commit that round.
    const bindsCancellation = Boolean(action.roundId) && state.stoppingGenerations.includes(action.generation);
    const stoppingGenerations = bindsCancellation
      ? state.stoppingGenerations.filter((generation) => generation !== action.generation) : state.stoppingGenerations;
    const stoppingRounds = bindsCancellation && !state.completedRounds.includes(action.roundId!)
      && !state.stoppingRounds.includes(action.roundId!)
      ? [...state.stoppingRounds, action.roundId!] : state.stoppingRounds;
    return { ...state, stoppingGenerations, stoppingRounds,
      stopping: stoppingRounds.length > 0 || stoppingGenerations.length > 0,
      streamFailed: state.activeGeneration === action.generation ? false : state.streamFailed };
  }
  if (action.type !== "stream_settled")
    return state;
  const inFlightTokens = state.inFlightTokens.filter((token) => token !== action.token);
  const activeGeneration = state.activeGeneration === action.generation ? null : state.activeGeneration;
  return { ...state, inFlightTokens, activeGeneration, streamInFlight: inFlightTokens.length > 0,
    streaming: rootActive(state.activeRounds) || (activeGeneration !== null && state.streaming) };
}
