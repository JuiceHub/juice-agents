import assert from "node:assert/strict";
import test from "node:test";

import type { ActionStep, RunnerStreamEvent } from "@juice-agents/shared/gateway/types";
import {
  buildIntroPanel,
  presentDreamReceipt,
  presentActorSession,
  presentMemorySearch,
  presentMemoryStatus,
  presentMemoryView,
  presentStatus,
  presentStreamEvent,
  presentStreamStep,
  shouldShowIntroPanel,
} from "./presenter.js";

function makeStep(overrides: Partial<ActionStep> = {}): ActionStep {
  return {
    step_num: 1,
    model_output: "",
    thought: "",
    tool_calls: [],
    code_action: "",
    reasoning_content: "",
    observations: [],
    observation_images: [],
    attachments: [],
    error: null,
    round_outcome: "continue",
    output: null,
    ...overrides,
  };
}

function makeEvent(overrides: Partial<RunnerStreamEvent> = {}): RunnerStreamEvent {
  return {
    kind: "action_step",
    permission_mode: "default",
    agent_mode: "agent",
    runner_id: "agent-001",
    actor_name: "root",
    actor_role: "root",
    step_num: 1,
    action_step: makeStep(),
    team_event: null,
    round_id: "round-1",
    ...overrides,
  };
}

test("renders thinking block for non-terminal step from thought", () => {
  const blocks = presentStreamStep(
    makeStep({
      thought: "先检查文件结构",
    })
  );

  assert.deepEqual(blocks, [{ kind: "thinking", text: "先检查文件结构" }]);
});

test("renders runner action_step events through the action step presenter", () => {
  const blocks = presentStreamEvent(
    makeEvent({
      action_step: makeStep({
        thought: "统一 stream 事件",
      }),
    })
  );

  assert.deepEqual(blocks, [{ kind: "thinking", text: "统一 stream 事件" }]);
});

test("actor session presenter renders subagent task and action transcript", () => {
  const blocks = presentActorSession({
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
    ],
  });

  assert.deepEqual(blocks, [
    { kind: "system", text: "researcher: active · teammate · teammate_researcher_0001" },
    { kind: "user", text: "research async state" },
    { kind: "thinking", text: "reading store" },
  ]);
});

test("actor session presenter shows running status when subagent has no transcript yet", () => {
  const blocks = presentActorSession({
    actor_id: "runner:worker",
    actor_name: "worker",
    actor_kind: "group_worker",
    actor_role: "worker",
    status: "active",
    is_root: false,
    current_async_task_id: "agent_dispatch_root_abcd",
    metadata: {},
    session: {},
    steps: [],
  });

  assert.deepEqual(blocks, [
    { kind: "system", text: "worker: active · group_worker · agent_dispatch_root_abcd" },
    { kind: "system", text: "No transcript entries for worker yet." },
  ]);
});

test("renders phase team update events as compact system blocks", () => {
  const blocks = presentStreamEvent(
    makeEvent({
      kind: "team_update",
      permission_mode: "default",
      agent_mode: "team",
      actor_name: "runtime",
      actor_role: "runtime",
      action_step: null,
      team_event: {
        actor: "runtime",
        update: { phase: "running" },
      },
    })
  );

  assert.deepEqual(blocks, [
    { kind: "system", text: "runtime: running" },
  ]);
});

test("hides ordinary team update summaries by default", () => {
  const blocks = presentStreamEvent(
    makeEvent({
      kind: "team_update",
      permission_mode: "default",
      agent_mode: "team",
      actor_name: "teamlead",
      actor_role: "teamlead",
      action_step: null,
      team_event: {
        actor: "teamlead",
        update: { summary: "ordinary round summary" },
      },
    })
  );

  assert.deepEqual(blocks, []);
});

test("renders critical team update status blocks", () => {
  const phaseBlocks = presentStreamEvent(
    makeEvent({
      kind: "team_update",
      permission_mode: "default",
      agent_mode: "team",
      actor_name: "runtime",
      actor_role: "runtime",
      action_step: null,
      team_event: {
        actor: "runtime",
        update: { phase: "finished" },
      },
    })
  );
  const stopBlocks = presentStreamEvent(
    makeEvent({
      kind: "team_update",
      permission_mode: "default",
      agent_mode: "team",
      actor_name: "runtime",
      actor_role: "runtime",
      action_step: null,
      team_event: {
        actor: "runtime",
        update: { stop_reason: "max_cycles" },
      },
    })
  );
  const errorBlocks = presentStreamEvent(
    makeEvent({
      kind: "team_update",
      permission_mode: "default",
      agent_mode: "team",
      actor_name: "teamlead",
      actor_role: "teamlead",
      action_step: null,
      team_event: {
        actor: "teamlead",
        update: { error: "teamlead failed" },
      },
    })
  );

  assert.deepEqual(phaseBlocks, [{ kind: "system", text: "runtime: finished" }]);
  assert.deepEqual(stopBlocks, [{ kind: "system", text: "runtime: max_cycles" }]);
  assert.deepEqual(errorBlocks, [{ kind: "system", text: "teamlead: teamlead failed" }]);
});

test("renders submitted round_end events as final blocks", () => {
  const blocks = presentStreamEvent(
    makeEvent({
      kind: "round_end",
      permission_mode: "default",
      agent_mode: "team",
      action_step: null,
      outcome: "submitted",
      output: "Final summary",
    })
  );

  assert.deepEqual(blocks, [{ kind: "final", text: "Final summary" }]);
});

test("renders team terminal action steps as thinking-only blocks", () => {
  const blocks = presentStreamEvent(
    makeEvent({
      permission_mode: "default",
      agent_mode: "team",
      actor_name: "teamlead",
      actor_role: "teamlead",
      action_step: makeStep({
        round_outcome: "submitted",
        output: "最终答案",
        thought: "整理最终回复",
        observations: ["<result_of_action_0>\n最终答案\n</result_of_action_0>"],
      }),
    })
  );

  assert.deepEqual(blocks, [{ kind: "thinking", text: "整理最终回复" }]);
});

test("falls back to raw model_output thought extraction for non-terminal steps", () => {
  const blocks = presentStreamStep(
    makeStep({
      model_output: '<thought>先继续分析</thought><actions>[{"name":"read"}]</actions>',
    })
  );

  assert.deepEqual(blocks, [{ kind: "thinking", text: "先继续分析" }]);
});

test("submitted action step renders thought while round_end owns final output", () => {
  const blocks = presentStreamStep(
    makeStep({
      round_outcome: "submitted",
      output: "最终答案",
      thought: "已经可以给结论了",
      observations: ["最终答案"],
    })
  );

  assert.deepEqual(blocks, [{ kind: "thinking", text: "已经可以给结论了" }]);
});

test("renders terminal thought before sanitized fallback output", () => {
  const blocks = presentStreamStep(
    makeStep({
      round_outcome: "submitted",
      output: "最终答案",
      thought: "整理最终回复",
      model_output:
        "<thought>整理最终回复</thought>\n<actions>[{\"name\":\"submit_output\"}]</actions>\n最终答案",
    })
  );

  assert.deepEqual(blocks, [{ kind: "thinking", text: "整理最终回复" }]);
});

test("keeps current terminal final-only behavior when no thought exists", () => {
  const blocks = presentStreamStep(
    makeStep({
      round_outcome: "submitted",
      output: "只有最终答案",
      observations: ["只有最终答案"],
    })
  );

  assert.deepEqual(blocks, []);
});

test("renders only error blocks for errored steps", () => {
  const blocks = presentStreamStep(
    makeStep({
      error: "something broke",
      thought: "这条不应该显示",
      round_outcome: "failed",
      observations: ["也不应该显示"],
    })
  );

  assert.deepEqual(blocks, [{ kind: "error", text: "something broke" }]);
});

test("never leaks actions or code blocks into rendered output", () => {
  const blocks = presentStreamStep(
    makeStep({
      round_outcome: "submitted",
      output: "最终答案",
      model_output:
        "<thought>先想一下</thought><code>print('debug')</code><actions>[{\"name\":\"submit_output\"}]</actions>\n最终答案",
    })
  );

  assert.deepEqual(blocks, [{ kind: "thinking", text: "先想一下" }]);
});

test("builds an intro panel model for the transcript header", () => {
  const hero = buildIntroPanel({
    runner_id: "runner-123",
    permission_mode: "default",
    agent_mode: "agent",
    root_actor_name: "root",
    base_dir: "/tmp/workspace",
    started: true,
    resumed: false,
    agent_type: "react",
    model_name: "doubao-1.5-pro",
    model_effort: "disabled",
    backend: "doubao",
    provider_model_name: "doubao-1.5-pro",
  });

  assert.equal(hero.wordmark, "JUICE AGENTS");
  assert.equal("eyebrow" in hero, false);
  assert.ok(hero.bannerLines.length > 8);
  assert.ok(hero.bannerLines.some((line) => line.includes("██")));
  assert.equal(hero.bannerLines.some((line) => /[▓▒░]/.test(line)), false);
  assert.match(hero.tagline, /lightweight terminal surface/i);
  assert.deepEqual(hero.metaLines, ["/tmp/workspace", "doubao-1.5-pro · disabled"]);
});

test("intro panel warns when accept mode is restored from persisted config", () => {
  const base = {
    runner_id: "runner-123",
    agent_mode: "agent",
    root_actor_name: "root",
    base_dir: "/tmp/workspace",
    started: true,
    resumed: false,
    agent_type: "react",
    model_name: "doubao-1.5-pro",
    model_effort: "disabled",
    backend: "doubao",
    provider_model_name: "doubao-1.5-pro",
  };

  // accept 可跨会话持久化，必须显式提示当前不再逐次审批。
  const accepted = buildIntroPanel({ ...base, permission_mode: "accept" });
  assert.equal(accepted.metaLines.length, 3);
  assert.match(accepted.metaLines[2], /accept mode/);
  assert.match(accepted.metaLines[2], /without asking/);

  // default 是安全默认值，不加噪音。
  const asked = buildIntroPanel({ ...base, permission_mode: "default" });
  assert.equal(asked.metaLines.length, 2);
  assert.equal(asked.metaLines.some((line) => /accept mode/.test(line)), false);
});

test("shouldShowIntroPanel hides the panel until runtime is ready to avoid unknown-model first frame", () => {
  const introPanel = buildIntroPanel({
    runner_id: "",
    permission_mode: "default",
    agent_mode: "agent",
    root_actor_name: "root_agent",
    base_dir: "/tmp/workspace",
    started: false,
    resumed: false,
    agent_type: "react",
    model_name: "",
    model_effort: "disabled",
    backend: "pending",
    provider_model_name: "pending",
    goal: null,
  });

  // 加载未完成时不显示，避免 "unknown-model · disabled" 闪现
  assert.equal(
    shouldShowIntroPanel({ introPanel, activeActorName: null, runtimeReady: false }),
    false,
  );

  // 加载完成后正常显示
  assert.equal(
    shouldShowIntroPanel({ introPanel, activeActorName: null, runtimeReady: true }),
    true,
  );

  // 用户聚焦到某个 actor 时也不显示（与现有逻辑一致）
  assert.equal(
    shouldShowIntroPanel({ introPanel, activeActorName: "child", runtimeReady: true }),
    false,
  );

  // introPanel 缺失时不显示
  assert.equal(
    shouldShowIntroPanel({ introPanel: null, activeActorName: null, runtimeReady: true }),
    false,
  );
});

test("formats status blocks with English labels", () => {
  const block = presentStatus({
    runner_id: "runner-123",
    permission_mode: "default",
    agent_mode: "agent",
    root_actor_name: "writer",
    base_dir: "/tmp/workspace",
    started: true,
    resumed: false,
    agent_type: "codeact",
    model_name: "gpt4o_mini",
    model_effort: "high",
    backend: "openai",
    provider_model_name: "gpt-4o-mini",
  });

  assert.match(block.text, /runner_id: runner-123/);
  assert.match(block.text, /model_name: gpt4o_mini/);
  assert.match(block.text, /model_effort: high/);
  assert.match(block.text, /backend: openai/);
  assert.match(block.text, /provider_model_name: gpt-4o-mini/);
  assert.match(block.text, /session_kind: new/);
  assert.match(block.text, /started: yes/);
});

test("renders memory status, search, view, and dream receipt blocks", () => {
  const status = presentMemoryStatus({
    enabled: true,
    dream_enabled: false,
    memory_dir: "/tmp/workspace/.juice/memory",
    entrypoint: "/tmp/workspace/.juice/memory/MEMORY.md",
    topic_count: 2,
    last_dream_at: null,
    auto_dream_due: false,
    next_dream_reason: "need 5 sessions, found 2",
    eligible_session_count: 2,
    dream_lock_owner: "",
  });
  const search = presentMemorySearch({
    hits: [{ path: "topics/project.md", line: 3, snippet: "Use conda activate agents" }],
  });
  const view = presentMemoryView({
    enabled: true,
    path: "MEMORY.md",
    content: "# Workspace Memory",
  });
  const dream = presentDreamReceipt({
    status: "launched",
    kind: "memory_dream",
    async_task_id: "memory_dream_1",
    output_dir: "/tmp/memory_dream_1",
  });

  assert.match(status.text, /dream_enabled: no/);
  assert.match(status.text, /auto_dream_due: no/);
  assert.match(status.text, /need 5 sessions, found 2/);
  assert.match(search.text, /topics\/project\.md:3/);
  assert.equal(view.title, "MEMORY.md");
  assert.match(dream.text, /memory_dream_1/);
});
