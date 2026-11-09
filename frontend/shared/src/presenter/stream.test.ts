import test from "node:test";
import assert from "node:assert/strict";
import { INTERRUPT_MESSAGE, presentActiveAgent, presentStreamEvent } from "./stream.js";
import type { ActiveAgentSnapshot, ActionStep, RunnerStreamEvent } from "../gateway/types.js";
const step: ActionStep = { step_num: 1, model_output: "", thought: "checking", tool_calls: [], code_action: "", reasoning_content: "", observations: [], observation_images: [], attachments: [], error: null, round_outcome: "submitted", output: "done" };
const base = { event_id: "event", timestamp: 1, runner_id: "runner", agent_id: "root", agent_name: "root", agent_role: "root", round_id: "round", step_num: 1 };

test("committed steps and round results have distinct stable presentation identities", () => {
  const blocks = presentStreamEvent({ ...base, kind: "step_end", data: { step } });
  assert.equal(blocks[0].kind, "thinking");
  assert.equal(blocks[0].text, "checking");
  assert.ok(blocks[0].id);
  const final = presentStreamEvent({ ...base, kind: "round_end", data: { result: { round_id: "round", status: "completed", output: "done", reason: "", error: null } } });
  assert.equal(final[0].kind, "final");
  assert.equal(final[0].text, "done");
});
test("cancelled and failed results never become successful answers", () => {
  const event: RunnerStreamEvent = { ...base, kind: "round_end", data: { result: { round_id: "round", status: "cancelled", output: null, reason: "user", error: null } } };
  assert.equal(presentStreamEvent(event)[0].text, INTERRUPT_MESSAGE);
  event.data.result = { ...event.data.result, status: "failed", reason: "max_steps_exceeded" };
  assert.deepEqual(presentStreamEvent(event).map(({ kind, text }) => ({ kind, text })), [{ kind: "error", text: "max_steps_exceeded" }]);
});
test("workflow progress stays outside the transcript", () => {
  assert.deepEqual(presentStreamEvent({ ...base, kind: "workflow_progress", data: { scope: "workflow", event: "node_started", call_id: "workflow", node: "fetch" } }), []);
});
test("restored Agent history retains task, tool results and submitted output", () => {
  const actor: ActiveAgentSnapshot = { agent_id: "root", agent_name: "root", agent_kind: "persistent", agent_role: "root", status: "idle", is_root: true, current_async_task_id: "", metadata: {}, session: {}, steps: [
    { type: "task", task: "read the file" }, { ...step, type: "action", tool_calls: [{ name: "read", args: { path: "README.md" }, mode: "foreground", status: "completed", call_id: "read", observation_index: 0, is_terminal: false }], observations: ["contents"] },
  ] };
  const blocks = presentActiveAgent(actor);
  assert.equal(blocks.find((block) => block.kind === "user")?.text, "read the file");
  assert.equal(blocks.find((block) => block.kind === "tool")?.toolCall?.observation, "contents");
  assert.equal(blocks.at(-1)?.text, "done");
});
test("committed images remain visible as portable path text", () => {
  const blocks = presentStreamEvent({ ...base, kind: "step_end", data: { step: { ...step, thought: "", observation_images: [{ image_url: ".juice/result.png", description: "Generated image" }] } } });
  assert.equal(blocks[0].text, "Generated image\n.juice/result.png");
  assert.ok(blocks[0].id);
});
