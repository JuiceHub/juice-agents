import assert from "node:assert/strict";
import test from "node:test";
import { presentStreamEvent as cliPresent } from "../../frontend/cli/src/lib/presenter.js";
import { presentStreamEvent as sharedPresent, presentStreamStep } from "../../frontend/shared/src/presenter/stream.js";
import { conversationReducer, createConversationState } from "../../frontend/shared/src/conversation/state.js";
import type { ActionStep, RunnerStreamEvent } from "../../frontend/shared/src/gateway/types.js";
const base = { timestamp: 1, runner_id: "runner", agent_id: "root", agent_name: "root", agent_role: "root", round_id: "round", step_num: 1 };
const step: ActionStep = { step_num: 1, model_output: "", thought: "", code_action: "", tool_calls: [{ name: "read", args: { path: "README.md" }, call_id: "read", status: "completed", mode: "foreground", observation_index: 0, is_terminal: false }], observations: ["file contents"], reasoning_content: "", observation_images: [], attachments: [], error: null, round_outcome: "continue", output: null };
const events: RunnerStreamEvent[] = [
  { ...base, kind: "tool_start", event_id: "start", data: { call_id: "read", tool_name: "read", tool_args: { path: "README.md" } } },
  { ...base, kind: "tool_end", event_id: "end", data: { call_id: "read", tool_name: "read", status: "completed", observation: "file contents" } },
  { ...base, kind: "step_end", event_id: "step", data: { step } },
];
test("CLI and Web share the exact presenter for tool results and committed snapshots", () => {
  assert.equal(cliPresent, sharedPresent);
  for (const event of events) assert.deepEqual(cliPresent(event), sharedPresent(event));
});
test("tool snapshots enrich one existing row with its observation", () => {
  const state = events.reduce((state, event) => conversationReducer(state, { type: "stream_event", event }), createConversationState());
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].toolCall?.observation, "file contents");
  assert.equal(state.messages[0].toolCall?.parameters.path, "README.md");
});
for (const observation of ["tool output", ""]) {
  test(`explicit tool errors remain visible with ${observation ? "nonempty" : "empty"} results`, () => {
    const event: RunnerStreamEvent = { ...base, event_id: "error", kind: "tool_end", data: { call_id: "read", tool_name: "read", status: "failed", observation, error: "Permission denied" } };
    const block = cliPresent(event)[0];
    assert.equal(block.toolCall?.status, "failed");
    assert.match(block.text, /Permission denied/);
  });
}
for (const name of ["submit_output", "Python"]) {
  test(`${name} submits final output only at the round boundary`, () => {
    const output = "Final business result";
    const blocks = [
      cliPresent({ ...base, event_id: "tool", kind: "tool_end", data: { call_id: "output", tool_name: name, status: "completed", is_terminal: true, observation: output } }),
      cliPresent({ ...base, event_id: "result", kind: "round_end", data: { result: { round_id: "round", status: "completed", output, reason: "", error: null } } }),
    ].flat();
    assert.deepEqual(blocks.filter((block) => block.kind === "final").map((block) => block.text), [output]);
    assert.equal(blocks.filter((block) => block.text.includes(output)).length, 1);
  });
}
test("restored tools preserve their associated observations", () => {
  const block = presentStreamStep(step)[0];
  assert.equal(block.toolCall?.observation, "file contents");
});
