import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { delimiter, join } from "node:path";
import test from "node:test";
import type { RunnerStreamEvent } from "../../frontend/shared/src/gateway/types.js";
import { conversationReducer, createConversationState } from "../../frontend/shared/src/conversation/state.js";
import { presentStreamEvent as cliPresent } from "../../frontend/cli/src/lib/presenter.js";
import { presentStreamEvent as webPresent } from "../../frontend/shared/src/presenter/stream.js";

/** Exercise production SDK objects and gateway JSON through the TS reducer. */
test("Python public envelopes and committed SDK steps feed identical CLI/Web rows", () => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const python = process.env.JUICE_PYTHON_PATH || "python";
  const output = execFileSync(python, ["-c", `
import json
from types import SimpleNamespace
from juice_agents.core.agent.agents import MultiStepAgent
from juice_agents.core.agent.contracts import RoundResult
from juice_agents.core.agent.context.steps import ActionStep
from juice_agents.core.managers.agents import AgentManager
from adapters.stdio_gateway.serialization import serialize_runner_stream_event

manager = AgentManager.__new__(AgentManager)
manager.runner_id = "python-runner"
events = []
manager.callbacks = SimpleNamespace(on_event=lambda item, event: events.append(serialize_runner_stream_event(event)))
item = SimpleNamespace(ephemeral=False, agent_id="python-root", agent_name="root", role="root", round_checkpoint={"round_id": "round-python"})
manager._emit_round_event(item, {"kind": "round_start", "data": {}})
agent = SimpleNamespace(_started_tool_call_ids=set(), _active_step_num=1, event_handler=lambda event: manager._emit_internal_event(item, event))
MultiStepAgent._emit_tool_start(agent, SimpleNamespace(name="read", args={"path": "README.md"}, order=0), "python-call")
step = ActionStep(step_num=1, model_output="", thought="checking", observations=["Python result"], round_id="round-python", step_id="python-step", tool_calls=[{"name": "read", "args": {"path": "README.md"}, "mode": "foreground", "status": "completed", "call_id": "python-call", "observation_index": 0, "is_terminal": False}])
manager._emit_round_event(item, {"kind": "step_end", "step_num": 1, "data": {"step": step}})
manager._emit_round_event(item, {"kind": "round_end", "data": {"result": RoundResult("round-python", "completed", output=0)}})
print(json.dumps(events))
`], {
    cwd: root, encoding: "utf8",
    env: { ...process.env, PYTHONPATH: [join(root, "sdk/src"), root, process.env.PYTHONPATH].filter(Boolean).join(delimiter) },
  });
  const events = JSON.parse(output) as RunnerStreamEvent[];
  for (const event of events) {
    assert.equal(typeof event.event_id, "string");
    assert.equal(typeof event.timestamp, "number");
    assert.equal(event.agent_id, "python-root");
    assert.equal(event.round_id, "round-python");
    assert.deepEqual(cliPresent(event), webPresent(event));
  }
  let state = events.reduce((state, event) => conversationReducer(state, { type: "ambient_event", event }), createConversationState());
  state = conversationReducer(state, { type: "ambient_event", event: events[2] });
  assert.equal(state.streaming, false);
  assert.equal(state.messages.filter((block) => block.kind === "tool").length, 1);
  assert.equal(state.messages.find((block) => block.kind === "tool")?.toolCall?.observation, "Python result");
  assert.deepEqual(state.messages.filter((block) => block.kind === "final").map((block) => block.text), ["0"]);
});
