import assert from "node:assert/strict";
import test from "node:test";
import type { ActionStep, ActiveAgentSnapshot, ActiveAgentStep, RunnerStreamEvent, } from "../../frontend/shared/src/gateway/types.js";
import { presentActiveAgent, presentStreamEvent, presentStreamStep, } from "../../frontend/shared/src/presenter/stream.js";
function action(overrides: Partial<ActionStep> = {}): ActionStep {
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
        ...overrides
    };
}
function actor(steps: ActiveAgentStep[]): ActiveAgentSnapshot {
    return {
        agent_id: "runner:root",
        agent_name: "root",
        agent_kind: "root",
        agent_role: "root",
        status: "active",
        is_root: true,
        current_async_task_id: "",
        metadata: {},
        session: {},
        steps
    };
}
test("resumed history keeps user turns, thinking, tool results, and final answers in order", () => {
    const blocks = presentActiveAgent(actor([
        { type: "task", task: "find the working directory" },
        {
            ...action({
                thought: "checking the directory",
                tool_calls: [{
                        name: "shell",
                        args: { command: "pwd" },
                        mode: "sync",
                        status: "success",
                        observation_index: 0,
                        is_terminal: false
                    }],
                observations: ["/workspace"]
            }),
            type: "action"
        },
        {
            ...action({
                step_num: 2,
                thought: "reporting the result",
                tool_calls: [{
                        name: "submit_output",
                        args: { output: "directory: /workspace" },
                        mode: "sync",
                        status: "success",
                        observation_index: null,
                        is_terminal: true
                    }],
                round_outcome: "submitted",
                output: "directory: /workspace"
            }),
            type: "action"
        },
        { type: "task", task: "confirm the result" },
        {
            ...action({ thought: "confirming", round_outcome: "submitted", output: "confirmed" }),
            type: "action"
        },
    ]));
    // 恢复整段会话时，终稿必须位于对应轮次中，不能被下一条用户消息覆盖。
    assert.deepEqual(blocks.map(({ kind, text }) => ({ kind, text })), [
        { kind: "system", text: "root: active · root" },
        { kind: "user", text: "find the working directory" },
        { kind: "thinking", text: "checking the directory" },
        { kind: "tool", text: "Completed shell (command: pwd)\n/workspace" },
        { kind: "thinking", text: "reporting the result" },
        { kind: "final", text: "directory: /workspace" },
        { kind: "user", text: "confirm the result" },
        { kind: "thinking", text: "confirming" },
        { kind: "final", text: "confirmed" },
    ]);
    assert.equal(blocks.find((block) => block.kind === "tool")?.toolCall?.status, "completed");
});
for (const output of [0, false]) {
    test(`resumed submitted history preserves the valid output ${output}`, () => {
        const blocks = presentActiveAgent(actor([
            { ...action({ round_outcome: "submitted", output }), type: "action" },
        ]));
        assert.deepEqual(blocks.filter((block) => block.kind === "final"), [
            { kind: "final", text: String(output) },
        ]);
    });
}
test("resumed history omits empty answers and unfinished action outputs", () => {
    // output 是 unknown；判断空值时不能用真假值过滤，否则会误丢 0 和 false。
    const steps: ActiveAgentStep[] = [null, undefined, "", " \n\t "].map((output) => ({
        ...action({ round_outcome: "submitted", output }),
        type: "action"
    }));
    steps.push({ ...action({ output: "intermediate result" }), type: "action" });
    const blocks = presentActiveAgent(actor(steps));
    assert.deepEqual(blocks.filter((block) => block.kind === "final"), []);
});
test("live action rendering leaves exactly one final answer for round_end", () => {
    const step = action({ thought: "ready to answer", round_outcome: "submitted", output: "done" });
    const event: RunnerStreamEvent = {
        agent_id: "agent-1",
        event_id: String(Math.random()),
        timestamp: 1,
        kind: "step_end",
        permission_mode: "default",
        agent_mode: "agent",
        runner_id: "runner",
        agent_name: "root",
        agent_role: "root",
        step_num: 1,
        round_id: "round",
        data: {
            step: step
        }
    };
    // 实时流程已有 round_end，历史恢复的补全不能让 action_step 重复展示终稿。
    assert.deepEqual(presentStreamStep(step), [{ kind: "thinking", text: "ready to answer" }]);
    const blocks = [
        ...presentStreamEvent(event),
        ...presentStreamEvent({
            ...event,
            kind: "round_end",
            step_num: null,
            data: {
                result: {
                    round_id: "round-1",
                    status: "completed",
                    output: "done",
                    reason: "",
                    error: null
                }
            }
        }),
    ];
    assert.deepEqual(blocks.map(({kind, text}) => ({kind, text})), [
        { kind: "thinking", text: "ready to answer" },
        { kind: "final", text: "done" },
    ]);
});
