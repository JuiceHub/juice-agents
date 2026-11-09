import assert from "node:assert/strict";
import test from "node:test";
import { conversationReducer, createConversationState, type ConversationState } from "../../frontend/shared/src/conversation/state.js";
import type { ActiveAgentsReport, RunnerEventData, RunnerStreamEvent } from "../../frontend/shared/src/gateway/types.js";
import { presentActiveAgent, presentAgentSession } from "../../frontend/shared/src/presenter/stream.js";
let sequence = 0;
function event<K extends keyof RunnerEventData>(kind: K, data: RunnerEventData[K], options: { round?: string; runner?: string; agent?: string; role?: string; id?: string } = {}): RunnerStreamEvent {
  return { kind, data, event_id: options.id || String(++sequence), timestamp: 1,
    runner_id: options.runner || "runner", agent_id: options.agent || "root", agent_name: options.agent || "root",
    agent_role: options.role || "root", round_id: options.round || "round" } as RunnerStreamEvent;
}
const result = (status: "completed" | "cancelled" | "failed", round = "round") => ({ round_id: round, status, output: status === "completed" ? "done" : null, reason: status === "failed" ? "max_steps_exceeded" : "", error: null });
function receive(state: ConversationState, next: RunnerStreamEvent) { return conversationReducer(state, { type: "ambient_event", event: next }); }

test("autonomous rounds set running status and publish one result without a request", () => {
  let state = receive(createConversationState(), event("round_start", {}));
  assert.equal(state.streaming, true);
  assert.equal(state.streamInFlight, false);
  const end = event("round_end", { result: result("completed") });
  state = receive(state, end);
  state = receive(state, end);
  assert.equal(state.streaming, false);
  assert.deepEqual(state.messages.map((block) => block.text), ["done"]);
});
test("a late start or duplicate result cannot reopen a committed round", () => {
  let state = receive(createConversationState(), event("round_end", { result: result("completed") }));
  state = receive(state, event("round_start", {}));
  state = receive(state, event("round_end", { result: result("cancelled") }));
  assert.equal(state.streaming, false);
  assert.deepEqual(state.messages.map((block) => block.text), ["done"]);
});
test("old request settlement cannot hide the next autonomous round", () => {
  let state = conversationReducer(createConversationState(), { type: "stream_started", generation: 1, token: "request", message: "start" });
  state = receive(state, event("round_start", {}, { round: "next" }));
  state = conversationReducer(state, { type: "stream_settled", generation: 1, token: "request" });
  assert.equal(state.streaming, true);
  assert.equal(state.streamInFlight, false);
});
test("runner switches reject old events and reset running state", () => {
  let state = receive(createConversationState(), event("round_start", {}));
  state = conversationReducer(state, { type: "scope_changed", runnerId: "new" });
  state = receive(state, event("round_end", { result: result("completed") }));
  assert.equal(state.streaming, false);
  assert.equal(state.messages.length, 0);
});
test("cancellation acknowledgement waits for real terminal events and preserves other rounds", () => {
  let state = receive(createConversationState(), event("round_start", {}));
  state = receive(state, event("round_start", {}, { round: "second" }));
  state = conversationReducer(state, { type: "cancel_requested", roundId: "round" });
  assert.equal(state.stopping, true);
  assert.equal(state.streaming, true);
  assert.equal(state.messages.length, 0);
  state = receive(state, event("round_end", { result: result("cancelled") }));
  assert.equal(state.stopping, false);
  assert.equal(state.streaming, true);
  assert.deepEqual(Object.keys(state.activeRounds), ["second"]);
});
test("rejection and transport failure never fabricate a cancelled tool or round", () => {
  let state = receive(createConversationState(), event("round_start", {}));
  state = receive(state, event("tool_start", { call_id: "read", tool_name: "read" }));
  state = conversationReducer(state, { type: "cancel_requested", roundId: "round" });
  state = conversationReducer(state, { type: "cancel_rejected", roundId: "round", message: "not accepted" });
  state = conversationReducer(state, { type: "transport_failed", message: "disconnected" });
  assert.equal(state.streaming, true);
  assert.equal(state.stopping, false);
  assert.equal(state.messages[0].toolCall?.status, "running");
  assert.equal(state.completedRounds.length, 0);
});
test("child transcripts remain separate while workflow invocations retain distinct progress", () => {
  let state = receive(createConversationState(), event("thinking", { thought: "child private thought" }, { role: "worker", agent: "worker" }));
  state = receive(state, event("round_start", {}, { role: "worker", agent: "worker" }));
  state = receive(state, event("workflow_progress", { scope: "workflow", event: "node_started", call_id: "first", node: "fetch" }));
  state = receive(state, event("workflow_progress", { scope: "workflow", event: "node_started", call_id: "second", node: "write" }));
  assert.equal(state.messages.length, 0);
  assert.equal(state.agentMessages.worker[0].text, "child private thought");
  assert.equal(state.streaming, false);
  assert.equal(Object.keys(state.workflowProgress).length, 2);
});
test("reconnect snapshot corrects running state without inventing a result", () => {
  let state = receive(createConversationState(), event("round_start", {}));
  const report: ActiveAgentsReport = { runner_id: "runner", permission_mode: "default", agent_mode: "agent", root_agent_name: "root", agents: [{
    agent_id: "root", agent_name: "root", agent_role: "root", agent_kind: "persistent", status: "idle", is_root: true,
    current_async_task_id: "", metadata: {}, session: {}, steps: [],
  }] };
  state = conversationReducer(state, { type: "snapshot_received", report });
  assert.equal(state.streaming, false);
  assert.equal(state.messages.length, 0);
});

test("late tool starts cannot leave a committed cancelled round running", () => {
  let state = receive(createConversationState(), event("round_end", { result: result("cancelled") }));
  state = receive(state, event("tool_start", { call_id: "late", tool_name: "read" }));
  assert.equal(state.messages.find((block) => block.kind === "tool")?.toolCall?.status, "cancelled");
  assert.equal(state.streaming, false);
});
test("completed workflows stop showing progress when their caller round ends", () => {
  let state = receive(createConversationState(), event("round_start", {}));
  state = receive(state, event("workflow_progress", { scope: "workflow", event: "node_started", call_id: "first", node: "fetch" }));
  state = receive(state, event("round_end", { result: result("completed") }));
  assert.deepEqual(state.workflowProgress, {});
  assert.equal(state.lifecycleEvent, null);
});
test("saved child history and live committed steps update the same tool row", () => {
  const step = { step_num: 1, model_output: "", thought: "checking", code_action: "", reasoning_content: "", observations: ["result"], observation_images: [], attachments: [], error: null, round_outcome: "continue" as const, output: null,
    tool_calls: [{ name: "read", args: { path: "README.md" }, mode: "foreground", status: "completed", call_id: "child-read", observation_index: 0, is_terminal: false }] };
  const report: ActiveAgentsReport = { runner_id: "runner", permission_mode: "default", agent_mode: "agent", root_agent_name: "root", agents: [{ agent_id: "child", agent_name: "worker", agent_role: "worker", agent_kind: "persistent", status: "running", is_root: false, current_async_task_id: "", metadata: {}, session: {}, steps: [{ ...step, type: "action", round_id: "round" }] }] };
  let state = conversationReducer(createConversationState(), { type: "snapshot_received", report });
  state = receive(state, event("step_end", { step }, { agent: "child", role: "worker" }));
  assert.equal(state.messages.length, 0);
  assert.equal(state.agentMessages.child.filter((block) => block.kind === "tool").length, 1);
});

function reportFor(checkpoint: Record<string, unknown>, status: string, steps: ActiveAgentsReport["agents"][number]["steps"] = []): ActiveAgentsReport {
  return { runner_id: "runner", permission_mode: "default", agent_mode: "agent", root_agent_name: "root", agents: [{
    agent_id: "root", agent_name: "root", agent_role: "root", agent_kind: "persistent", status, is_root: true,
    current_async_task_id: "", metadata: {}, session: { round_checkpoint: checkpoint }, steps,
  }] };
}
test("a running reconnect snapshot restores the concrete cancellation target and replaces an old round", () => {
  let state = receive(createConversationState(), event("round_start", {}, { round: "old-round" }));
  state = conversationReducer(state, { type: "snapshot_received", report: reportFor({ round_id: "new-round", status: "active" }, "running") });
  assert.deepEqual(Object.keys(state.activeRounds), ["new-round"]);
  assert.equal(state.activeRounds["new-round"].agent_id, "root");
  assert.equal(state.streaming, true);
  const fresh = conversationReducer(createConversationState(), { type: "snapshot_received", report: reportFor({ round_id: "new-round", status: "active" }, "running") });
  assert.deepEqual(Object.keys(fresh.activeRounds), ["new-round"]);
});
test("snapshots recover missed root tool completion and answer without duplicating optimistic user input", () => {
  let state = conversationReducer(createConversationState(), { type: "stream_started", generation: 1, token: "request", message: "inspect" });
  state = receive(state, event("round_start", {}));
  state = receive(state, event("tool_start", { call_id: "read", tool_name: "read", tool_args: { path: "README.md" } }));
  const step = { type: "action", round_id: "round", step_num: 1, model_output: "", thought: "", code_action: "", reasoning_content: "", observations: ["saved result"], observation_images: [], attachments: [], error: null, round_outcome: "submitted", output: "done",
    tool_calls: [{ name: "read", args: { path: "README.md" }, mode: "foreground", status: "completed", call_id: "read", observation_index: 0, is_terminal: false }] };
  const report = reportFor({ round_id: "round", status: "completed", result: result("completed") }, "idle", [
    { type: "task", task: "inspect", round_id: "round" }, step,
    { type: "round_result", round_id: "round", result: result("completed") },
  ]);
  state = conversationReducer(state, { type: "snapshot_received", report });
  state = conversationReducer(state, { type: "snapshot_received", report });
  assert.equal(state.streaming, false);
  assert.deepEqual(state.messages.filter((block) => block.kind === "user").map((block) => block.text), ["inspect"]);
  assert.deepEqual(state.messages.filter((block) => block.kind === "final").map((block) => block.text), ["done"]);
  const tools = state.messages.filter((block) => block.kind === "tool");
  assert.equal(tools.length, 1);
  assert.equal(tools[0].toolCall?.status, "completed");
  assert.equal(tools[0].toolCall?.observation, "saved result");
});
test("persisted cancelled rounds recover real results and never render an uncommitted submitted output", () => {
  const cancelled = result("cancelled");
  const report = reportFor({ round_id: "round", status: "cancelled", result: cancelled }, "idle", [
    { type: "action", round_id: "round", step_num: 1, model_output: "", thought: "", tool_calls: [], code_action: "", reasoning_content: "", observations: [], observation_images: [], attachments: [], error: null, round_outcome: "submitted", output: "uncommitted" },
    { type: "round_result", round_id: "round", result: cancelled },
  ]);
  for (const blocks of [presentActiveAgent(report.agents[0]), presentAgentSession(report, report.agents[0])]) {
    assert.equal(blocks.some((block) => block.kind === "final"), false);
    assert.equal(blocks.filter((block) => block.text.includes("interrupted")).length, 1);
  }
  let state = conversationReducer(createConversationState(), { type: "snapshot_received", report });
  state = receive(state, event("round_start", {}));
  state = receive(state, event("tool_start", { call_id: "late", tool_name: "read" }));
  assert.equal(state.streaming, false);
  assert.equal(state.roundResults.round.status, "cancelled");
  assert.equal(state.messages.find((block) => block.kind === "tool")?.toolCall?.status, "cancelled");
});
test("an authoritative idle snapshot removes stale workflow progress and rejects a late running summary", () => {
  let state = receive(createConversationState(), event("round_start", {}));
  state = receive(state, event("workflow_progress", { scope: "workflow", event: "node_started", call_id: "offline", node: "fetch" }));
  state = conversationReducer(state, { type: "snapshot_received", report: reportFor({ round_id: "round", status: "completed", result: result("completed") }, "idle") });
  state = receive(state, event("agent_state", { status: "running", lifecycle: "persistent", is_root: true, round_checkpoint: { round_id: "round", status: "active" } }));
  assert.equal(state.agentStates.root.status, "idle");
  assert.equal(state.agentReport?.agents[0].status, "idle");
  assert.deepEqual(state.workflowProgress, {});
  assert.equal(state.lifecycleEvent, null);
  assert.equal(state.streaming, false);
});
