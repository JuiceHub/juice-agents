import test from "node:test";
import assert from "node:assert/strict";
import { INTERRUPT_MESSAGE, presentActorSession, presentStreamEvent } from "./stream.js";
import type { ActorSessionSnapshot, RunnerStreamEvent } from "../gateway/types.js";

test("presentStreamEvent keeps action and round-end presentation separate", () => {
  const event: RunnerStreamEvent = {
    kind: "action_step",
    permission_mode: "default",
    agent_mode: "agent",
    runner_id: "abc",
    actor_name: "root",
    actor_role: "root",
    step_num: 1,
    round_id: "round-1",
    team_event: null,
    action_step: {
      step_num: 1,
      model_output: "<thought>checking</thought>",
      thought: "checking",
      tool_calls: [],
      code_action: "",
      reasoning_content: "",
      observations: ["done"],
      observation_images: [],
      attachments: [],
      error: null,
      round_outcome: "submitted",
      output: "done",
    },
  };

  assert.deepEqual(presentStreamEvent(event), [{ kind: "thinking", text: "checking" }]);
  assert.deepEqual(
    presentStreamEvent({
      ...event,
      kind: "round_end",
      action_step: null,
      step_num: null,
      outcome: "submitted",
      output: "done",
    }),
    [{ kind: "final", text: "done" }]
  );
});

test("presentStreamEvent renders stream cancellation marker", () => {
  const event: RunnerStreamEvent = {
    kind: "stream_cancelled",
    permission_mode: "default",
    agent_mode: "agent",
    runner_id: "abc",
    actor_name: "root",
    actor_role: "root",
    step_num: null,
    round_id: "round-2",
    team_event: null,
    action_step: null,
    stop_reason: "user_cancelled",
  };

  assert.deepEqual(presentStreamEvent(event), [{ kind: "system", text: INTERRUPT_MESSAGE }]);
});

test("presentStreamEvent keeps lifecycle progress out of the transcript", () => {
  const event: RunnerStreamEvent = {
    kind: "runner_lifecycle",
    permission_mode: "default",
    agent_mode: "agent",
    runner_id: "abc",
    actor_name: "root",
    actor_role: "root",
    step_num: null,
    round_id: "round-3",
    team_event: null,
    lifecycle_event: {
      scope: "graph",
      event: "node_started",
      node: "research_topic",
      completed: 0,
      total: 2,
    },
    action_step: null,
  };

  assert.deepEqual(presentStreamEvent(event), []);
});

test("presentActorSession renders actor task, action, and status", () => {
  const actor: ActorSessionSnapshot = {
    actor_id: "runner:researcher",
    actor_name: "researcher",
    actor_kind: "teammate",
    actor_role: "worker",
    status: "active",
    is_root: false,
    current_async_task_id: "teammate_researcher_0001",
    metadata: {},
    session: {},
    steps: [
      { type: "task", task: "research async state" },
      {
        type: "action",
        step_num: 1,
        model_output: "<thought>reading store</thought>",
        thought: "reading store",
        tool_calls: [],
        code_action: "",
        reasoning_content: "",
        observations: [],
        observation_images: [],
        attachments: [],
        error: null,
        round_outcome: "continue",
        output: null,
      },
      { type: "summary", content: "worker summary" },
    ],
  };

  assert.deepEqual(presentActorSession(actor), [
    { kind: "system", text: "researcher: active · teammate · teammate_researcher_0001" },
    { kind: "user", text: "research async state" },
    { kind: "thinking", text: "reading store" },
    { kind: "system", text: "worker summary", title: "Summary" },
  ]);
});

test("presentStreamEvent renders react tool calls and codeact python blocks", () => {
  const reactBlocks = presentStreamEvent({
    kind: "action_step",
    permission_mode: "default",
    agent_mode: "agent",
    runner_id: "abc",
    actor_name: "root",
    actor_role: "root",
    step_num: 2,
    round_id: "round-4",
    team_event: null,
    action_step: {
      step_num: 2,
      model_output: "",
      thought: "running shell",
      tool_calls: [
        {
          name: "shell",
          args: { command: "pwd", workdir: "/tmp", background: false },
          mode: "sync",
          status: "success",
          observation_index: 0,
          is_terminal: false,
        },
      ],
      code_action: "",
      reasoning_content: "",
      observations: ["ok"],
      observation_images: [],
      attachments: [],
      error: null,
      round_outcome: "continue",
      output: null,
    },
  });

  assert.deepEqual(reactBlocks, [
    { kind: "thinking", text: "running shell" },
    {
      kind: "tool",
      title: "Ran shell",
      text: "Ran shell (command: pwd)",
      toolCall: {
        name: "shell",
        mode: "sync",
        status: "success",
        parameters: { command: "pwd", workdir: "/tmp", background: "false" },
      },
    },
  ]);

  const codeBlocks = presentStreamEvent({
    kind: "action_step",
    permission_mode: "default",
    agent_mode: "agent",
    runner_id: "abc",
    actor_name: "root",
    actor_role: "root",
    step_num: 3,
    round_id: "round-5",
    team_event: null,
    action_step: {
      step_num: 3,
      model_output: "",
      thought: "",
      tool_calls: [],
      code_action: "print('hi')",
      reasoning_content: "",
      observations: ["hi"],
      observation_images: [],
      attachments: [],
      error: null,
      round_outcome: "continue",
      output: null,
    },
  });

  assert.equal(codeBlocks[0]?.kind, "tool");
  assert.equal(codeBlocks[0]?.toolCall?.name, "Python");
});

test("presentStreamEvent surfaces observation images as clickable path text", () => {
  const blocks = presentStreamEvent({
    kind: "action_step",
    permission_mode: "default",
    agent_mode: "agent",
    runner_id: "abc",
    actor_name: "root",
    actor_role: "root",
    step_num: 4,
    round_id: "round-6",
    team_event: null,
    action_step: {
      step_num: 4,
      model_output: "",
      thought: "",
      tool_calls: [],
      code_action: "",
      reasoning_content: "",
      observations: [],
      observation_images: [{ image_url: ".juice/runners/abc/observation_images/out.png", description: "generated" }],
      attachments: [],
      error: null,
      round_outcome: "continue",
      output: null,
    },
  });

  assert.deepEqual(blocks, [
    {
      kind: "assistant",
      title: "Image",
      text: "generated\n.juice/runners/abc/observation_images/out.png",
    },
  ]);
});
