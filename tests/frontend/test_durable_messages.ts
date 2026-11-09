import assert from "node:assert/strict";
import test from "node:test";
import type { ActionStep, ActiveAgentsReport, RunnerEventData, RunnerStreamEvent } from "../../frontend/shared/src/gateway/types.js";
import { conversationReducer, createConversationState, type ConversationState } from "../../frontend/shared/src/conversation/state.js";
import { presentActiveAgent, presentAgentSession, presentStreamEvent } from "../../frontend/shared/src/presenter/stream.js";

let sequence = 0;
function event<K extends keyof RunnerEventData>(kind: K, data: RunnerEventData[K], agent = "root", round = "round"): RunnerStreamEvent {
  return { kind, data, event_id: `message-event-${++sequence}`, timestamp: 1, runner_id: "runner",
    agent_id: agent, agent_name: agent, agent_role: agent === "root" ? "root" : "subagent", round_id: round } as RunnerStreamEvent;
}
function receive(state: ConversationState, incoming: RunnerStreamEvent): ConversationState {
  return conversationReducer(state, { type: "ambient_event", event: incoming });
}
const message = (id: string, text: string, type = "agent_user_message") => ({
  message_id: id, attachment_type: type, created_at: 1, payload: { text },
});
function step(attachments: Record<string, any>[] = []): ActionStep {
  return { step_num: 1, model_output: "", thought: "", tool_calls: [], code_action: "", reasoning_content: "",
    observations: [], observation_images: [], attachments, error: null, round_outcome: "continue", output: null };
}
function report(agent: string, steps: ActiveAgentsReport["agents"][number]["steps"]): ActiveAgentsReport {
  return { runner_id: "runner", permission_mode: "default", agent_mode: "agent", root_agent_name: "root", agents: [{
    agent_id: agent, agent_name: agent, agent_role: agent === "root" ? "root" : "subagent", agent_kind: "persistent",
    status: "idle", is_root: agent === "root", current_async_task_id: "", metadata: {}, session: {}, steps,
  }] };
}

test("accepted root input adopts optimistic rows and remains unique through input events and snapshots", () => {
  const input = message("first", "inspect", "user_message");
  let state = conversationReducer(createConversationState(), { type: "stream_started", generation: 1, token: "request", message: "inspect" });
  state = receive(state, event("message_received", { attachments: [input] }));
  const identity = state.messages[0].id;
  assert.ok(identity);
  state = receive(state, event("round_start", { input: { task: "inspect", attachments: [input] } }));
  state = receive(state, event("step_end", { step: step([input]) }));
  state = conversationReducer(state, { type: "snapshot_received", report: report("root", [
    { type: "task", task: "inspect", round_id: "round", attachments: [input] },
    { ...step([input]), type: "action", round_id: "round" },
  ]) });
  assert.deepEqual(state.messages.filter((block) => block.kind === "user").map(({ id, text }) => ({ id, text })), [{ id: identity, text: "inspect" }]);
  // Repeated identical prompts have distinct backend IDs and remain separate.
  state = conversationReducer(state, { type: "stream_started", generation: 2, token: "next", message: "inspect" });
  state = receive(state, event("message_received", { attachments: [message("second", "inspect", "user_message")] }, "root", "next"));
  assert.equal(state.messages.filter((block) => block.kind === "user").length, 2);
});

test("child events create a selectable transcript before snapshots and keep its messages private", () => {
  let state = receive(createConversationState(), event("round_start", { input: { task: "research weather", attachments: [] } }, "general"));
  assert.equal(state.agentReport?.agents[0].agent_name, "general");
  assert.equal(state.agentReport?.agents[0].status, "unknown");
  assert.equal(state.agentMessages.general[0].text, "research weather");
  const childInput = message("child-input", "include humidity");
  state = receive(state, event("message_received", { attachments: [childInput] }, "general"));
  state = receive(state, event("step_end", { step: step([childInput]) }, "general"));
  state = receive(state, event("round_end", { result: { round_id: "round", status: "completed", output: "weather report", reason: "", error: null } }, "general"));
  const saved = report("general", [
    { type: "task", task: "research weather", round_id: "round", attachments: [] },
    { ...step([childInput]), type: "action", round_id: "round" },
    { type: "round_result", round_id: "round", result: { round_id: "round", status: "completed", output: "weather report", reason: "", error: null } },
  ]);
  state = conversationReducer(state, { type: "snapshot_received", report: saved });
  assert.deepEqual(state.agentMessages.general.filter((block) => block.kind === "user").map((block) => block.text), ["research weather", "include humidity"]);
  assert.deepEqual(state.agentMessages.general.filter((block) => block.kind === "final").map((block) => block.text), ["weather report"]);
  assert.deepEqual(state.messages, []);
});

test("async completion is visible immediately after a foreground request and replays once", () => {
  const notification = { message_id: "completed-task", attachment_type: "async_task_notification", payload: {
    async_task_id: "weather-task", status: "completed", summary: "agent general completed", result: "raw tool output",
  } };
  let state = receive(createConversationState(), event("round_end", { result: {
    round_id: "round", status: "completed", output: "started", reason: "", error: null,
  } }));
  state = receive(state, event("message_received", { attachments: [notification] }, "root", "notification-round"));
  assert.equal(state.streamInFlight, false);
  assert.equal(state.streaming, false);
  assert.equal(state.messages.at(-1)?.text, "weather-task: completed · agent general completed");
  state = receive(state, event("round_start", { input: { task: "", attachments: [notification] } }, "root", "notification-round"));
  state = conversationReducer(state, { type: "snapshot_received", report: report("root", [
    { type: "task", task: "", round_id: "notification-round", attachments: [notification] },
  ]) });
  assert.equal(state.messages.filter((block) => block.text.includes("weather-task: completed")).length, 1);
  assert.deepEqual(state.messages.filter((block) => block.kind === "final").map((block) => block.text), ["started"]);
  assert.equal(state.messages.some((block) => block.text === "raw tool output"), false);
});

test("saved boundary messages and async failures render in both history paths without exposing prompt state", () => {
  const attachments = [message("control", "review the result"),
    { message_id: "team", attachment_type: "inbox_message", payload: { from: "researcher", text: "source found" } },
    { message_id: "failed-task", attachment_type: "async_task_notification", payload: { async_task_id: "fetch", status: "failed", summary: "connection failed" } },
    { message_id: "internal", attachment_type: "goal_state", payload: { text: "private runtime state" } }];
  const saved = report("general", [{ ...step(attachments), type: "action", round_id: "round" }]);
  for (const blocks of [presentActiveAgent(saved.agents[0]), presentAgentSession(saved, saved.agents[0])]) {
    assert.ok(blocks.some((block) => block.kind === "user" && block.text === "review the result"));
    assert.ok(blocks.some((block) => block.kind === "system" && block.text === "researcher: source found"));
    assert.ok(blocks.some((block) => block.kind === "error" && block.text === "fetch: failed · connection failed"));
    assert.equal(blocks.some((block) => block.text.includes("private runtime state")), false);
  }
  const live = presentStreamEvent(event("message_received", { attachments }, "general"));
  const restored = presentAgentSession(saved, saved.agents[0]);
  for (const block of live) assert.equal(restored.filter((item) => item.id === block.id).length, 1);
});

test("scheduled aggregate task text does not duplicate its individual messages", () => {
  const attachments = [message("one", "first", "user_message"), message("two", "second", "task_assignment")];
  const blocks = presentAgentSession(report("general", []), report("general", [
    { type: "task", task: "first\nsecond", round_id: "round", attachments },
  ]).agents[0]);
  assert.deepEqual(blocks.filter((block) => block.kind === "user").map((block) => block.text), ["first", "second"]);
  const functionalInput = presentStreamEvent(event("round_start", {
    input: { task: "original research task", attachments: [message("later", "include humidity")] },
  }, "general"));
  assert.deepEqual(functionalInput.map((block) => block.text), ["original research task", "include humidity"]);
});

test("refresh recovers accepted pending messages before a model call commits its next step", () => {
  const pending = [message("waiting", "new instructions"), {
    message_id: "pending-notification", attachment_type: "async_task_notification",
    payload: { async_task_id: "weather", status: "completed", summary: "result ready" },
  }];
  const saved = report("general", []);
  saved.agents[0].pending_messages = pending;
  saved.agents[0].status = "running";
  saved.agents[0].session.round_checkpoint = { round_id: "round", status: "active" };
  for (const blocks of [presentActiveAgent(saved.agents[0]), presentAgentSession(saved, saved.agents[0])]) {
    assert.ok(blocks.some((block) => block.text === "new instructions"));
    assert.ok(blocks.some((block) => block.text === "weather: completed · result ready"));
  }
  let state = conversationReducer(createConversationState(), { type: "snapshot_received", report: saved });
  state = receive(state, event("message_received", { attachments: pending }, "general"));
  state = receive(state, event("step_end", { step: step(pending) }, "general"));
  const committed = report("general", [{ ...step(pending), type: "action", round_id: "round" }]);
  state = conversationReducer(state, { type: "snapshot_received", report: committed });
  assert.equal(state.agentMessages.general.filter((block) => block.text === "new instructions").length, 1);
  assert.equal(state.agentMessages.general.filter((block) => block.text === "weather: completed · result ready").length, 1);
  assert.deepEqual(state.messages, []);
});

test("message IDs are scoped to the receiving Agent and old Runner events remain excluded", () => {
  const attachment = message("same-producer-id", "hello");
  let state = receive(createConversationState(), event("message_received", { attachments: [attachment] }, "first"));
  state = receive(state, event("message_received", { attachments: [attachment] }, "second"));
  assert.notEqual(state.agentMessages.first[0].id, state.agentMessages.second[0].id);
  state = conversationReducer(state, { type: "scope_changed", runnerId: "new-runner" });
  state = receive(state, event("message_received", { attachments: [attachment] }, "first"));
  assert.deepEqual(state.agentMessages, {});
});
