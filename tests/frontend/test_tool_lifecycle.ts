import assert from "node:assert/strict";
import test from "node:test";
import { presentActiveAgent, presentStreamEvent, presentStreamStep } from "../../frontend/cli/src/lib/presenter.js";
import { conversationReducer, createConversationState, type ConversationState, } from "../../frontend/shared/src/conversation/state.js";
import type { ActionStep, ActiveAgentSnapshot, RunnerStreamEvent, ToolCallDisplay, } from "../../frontend/shared/src/gateway/types.js";
/** Exercise the public presentation/reducer path used by both stream transports. */
function event(overrides: Partial<RunnerStreamEvent> = {}): RunnerStreamEvent {
    return {
        event_id: String(Math.random()),
        timestamp: 1,
        kind: "tool_start",
        permission_mode: "default",
        agent_mode: "agent",
        runner_id: "runner-1",
        agent_id: "agent-1",
        agent_name: "root",
        agent_role: "root",
        round_id: "round-1",
        step_num: 1,
        data: {
            call_id: "read-1",
            tool_name: "read",
            tool_index: 0,
            tool_args: { path: "README.md" }
        },
        ...overrides
    } as RunnerStreamEvent;
}
function action(overrides: Partial<ActionStep> = {}): ActionStep {
    return {
        step_num: 1,
        model_output: "",
        thought: "",
        code_action: "",
        tool_calls: [],
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
function call(overrides: Partial<ToolCallDisplay> = {}): ToolCallDisplay {
    return {
        call_id: "read-1",
        name: "read",
        args: { path: "README.md" },
        mode: "foreground",
        status: "success",
        observation_index: 0,
        is_terminal: false,
        ...overrides
    } as ToolCallDisplay;
}
function receive(state: ConversationState, next: RunnerStreamEvent): ConversationState {
    return conversationReducer(state, {
        type: "ambient_event",
        event: next,
        blocks: presentStreamEvent(next)
    });
}
function receiveAll(events: RunnerStreamEvent[], state = createConversationState()): ConversationState {
    return events.reduce(receive, state);
}
function toolMessages(state: ConversationState) {
    return state.messages.filter((block) => block.kind === "tool");
}
test("one tool record follows start, terminal result, and saved step without duplicate results", () => {
    const start = event({ event_id: "start-1" });
    let state = receive(createConversationState(), start);
    const id = toolMessages(state)[0]?.id;
    assert.ok(id, "the running record needs a stable identity before completion");
    assert.equal(toolMessages(state)[0]?.toolCall?.status, "running");
    assert.equal(toolMessages(state)[0]?.toolCall?.parameters.path, "README.md");
    state = receive(state, event({
        kind: "tool_end",
        event_id: "end-1",
        data: {
            call_id: "read-1", tool_name: "read", tool_index: 0,
            status: "completed", observation: "PRIVATE FILE CONTENTS", error: null,
            execution_time: 0.25
        }
    }));
    assert.equal(toolMessages(state).length, 1);
    assert.equal(toolMessages(state)[0]?.id, id);
    assert.equal(toolMessages(state)[0]?.toolCall?.status, "completed");
    // Some transports omit repeated arguments on completion. The start payload
    // remains the authoritative input rather than disappearing on replacement.
    assert.equal(toolMessages(state)[0]?.toolCall?.parameters.path, "README.md");
    state = receive(state, event({
        kind: "step_end",
        event_id: "step-1",
        data: {
            step: action({ tool_calls: [call()], observations: ["PRIVATE FILE CONTENTS"] })
        }
    }));
    assert.equal(toolMessages(state).length, 1);
    assert.equal(toolMessages(state)[0]?.id, id);
    assert.equal(toolMessages(state)[0]?.toolCall?.status, "completed");
    assert.equal(state.messages.filter((block) => block.text.includes("PRIVATE FILE CONTENTS")).length, 1);
    assert.equal(toolMessages(state)[0]?.toolCall?.observation, "PRIVATE FILE CONTENTS");
});
test("same-name concurrent tools finish independently in their original positions", () => {
    const second = {
        call_id: "read-2", tool_name: "read", tool_index: 1,
        tool_args: { path: "sdk/README.md" }
    };
    let state = receiveAll([event(), event({ data: second })]);
    const ids = toolMessages(state).map((block) => block.id);
    assert.notEqual(ids[0], ids[1]);
    state = receive(state, event({
        kind: "tool_end",
        data: { ...second, status: "completed", error: null }
    }));
    assert.deepEqual(toolMessages(state).map((block) => block.toolCall?.status), ["running", "completed"]);
    state = receive(state, event({
        kind: "tool_end",
        data: { ...event().data, status: "failed", error: "file is missing" }
    }));
    state = receive(state, event({
        kind: "step_end",
        data: {
            step: action({ tool_calls: [
                    call({ status: "failed", error: "file is missing" } as Partial<ToolCallDisplay>),
                    call({ call_id: "read-2", args: { path: "sdk/README.md" } } as Partial<ToolCallDisplay>),
                ] })
        }
    }));
    assert.deepEqual(toolMessages(state).map((block) => block.id), ids);
    assert.deepEqual(toolMessages(state).map((block) => block.toolCall?.status), ["failed", "completed"]);
    assert.equal(toolMessages(state)[0]?.toolCall?.error, "file is missing");
});
test("tool identity separates rounds, agents, and runner scopes", () => {
    const first = event();
    const nextRound = event({ round_id: "round-2" });
    const otherAgent = event({ agent_id: "agent-2", agent_name: "worker" });
    const state = receiveAll([first, nextRound, otherAgent]);
    assert.equal(toolMessages(state).length, 3);
    assert.equal(new Set(toolMessages(state).map((block) => block.id)).size, 3);
    const otherRunner = event({ runner_id: "runner-2" });
    assert.notEqual(presentStreamEvent(first)[0]?.id, presentStreamEvent(otherRunner)[0]?.id);
    // A stream from a previously selected Runner cannot add records to this one.
    assert.equal(receive(state, otherRunner), state);
});
test("replayed terminal events and a delayed start never reopen a completed call", () => {
    const end = event({
        kind: "tool_end",
        event_id: "read-completed",
        data: { ...event().data, status: "completed", error: null }
    });
    let state = receiveAll([event({ event_id: "read-started" }), end]);
    state = receive(state, end);
    state = receive(state, event({ event_id: "late-start" }));
    assert.equal(toolMessages(state).length, 1);
    assert.equal(toolMessages(state)[0]?.toolCall?.status, "completed");
});
for (const status of ["failed", "denied", "cancelled", "background"] as const) {
    test(`terminal ${status} survives action-step reconciliation with its error or task handle`, () => {
        const error = status === "background" ? null : `reason: ${status}`;
        const state = receiveAll([
            event(),
            event({
                kind: "tool_end",
                data: { ...event().data, status, error, task_id: status === "background" ? "task-1" : null }
            }),
            event({
                kind: "step_end",
                data: {
                    step: action({ tool_calls: [call({
                                status, error,
                                task_id: status === "background" ? "task-1" : ""
                            } as Partial<ToolCallDisplay>)] })
                }
            }),
        ]);
        assert.equal(toolMessages(state).length, 1);
        const record = toolMessages(state)[0];
        assert.equal(record?.toolCall?.status, status);
        if (error)
            assert.equal(record?.toolCall?.error, error);
        else
            assert.equal(record?.toolCall?.taskId, "task-1");
    });
}
for (const kind of ["round_end"] as const) {
    test(`${kind} cancels only unfinished tools in that round`, () => {
        const other = event({
            round_id: "round-2", data: { ...event().data, call_id: "other-call" }
        });
        let state = receiveAll([
            event({ kind: "round_start", data: {} }),
            event({ kind: "round_start", round_id: "round-2", data: {} }),
            event(),
            event({ data: { ...event().data, call_id: "already-done", tool_index: 1 } }),
            event({
                kind: "tool_end",
                data: { ...event().data, call_id: "already-done", tool_index: 1, status: "completed" }
            }),
            other,
        ]);
        state = receive(state, event({ kind, data: {
                result: {
                    round_id: "round-1",
                    status: "cancelled",
                    output: null,
                    reason: "",
                    error: null
                }
            } }));
        assert.deepEqual(toolMessages(state).map((block) => block.toolCall?.status), ["cancelled", "completed", "running"]);
        assert.equal(state.streaming, true, "the other active round keeps the conversation busy");
    });
}
test("a transport failure preserves unfinished calls until authoritative reconciliation", () => {
    let state = conversationReducer(createConversationState(), {
        type: "stream_started", generation: 1, token: "request-1", message: "read the file"
    });
    state = receive(state, event());
    state = conversationReducer(state, {
        type: "stream_failed", generation: 1, message: "connection lost"
    });
    assert.equal(toolMessages(state)[0]?.toolCall?.status, "running");
    assert.equal(state.messages.some((block) => block.kind === "error" && block.text === "connection lost"), true);
});
test("a round ending without a tool result does not leave that tool permanently running", () => {
    const state = receiveAll([
        event(),
        event({ kind: "round_end", data: {
                result: {
                    round_id: "round-1",
                    status: "completed",
                    output: "done",
                    reason: "",
                    error: null
                }
            } }),
    ]);
    assert.equal(toolMessages(state)[0]?.toolCall?.status, "unknown");
    assert.deepEqual(state.messages.filter((block) => block.kind === "final").map((block) => block.text), ["done"]);
});
test("CodeAct has one Python lifecycle record and does not repeat its internal tools", () => {
    const code = 'read(path="README.md")';
    const python = { call_id: "python-1", tool_name: "Python", tool_index: 0, tool_args: { code } };
    const state = receiveAll([
        event({ data: python }),
        event({ kind: "tool_end", data: { ...python, status: "completed", observation: "HIDDEN CONTENTS" } }),
        event({
            kind: "step_end",
            data: {
                step: action({ code_action: code, tool_calls: [call({ call_id: "python-1", name: "Python", args: { code } })], observations: ["HIDDEN CONTENTS"] })
            }
        }),
    ]);
    const records = toolMessages(state);
    assert.equal(records.length, 1);
    assert.equal(records[0]?.toolCall?.name, "Python");
    assert.equal(records[0]?.toolCall?.status, "completed");
    assert.equal(records[0]?.toolCall?.parameters.code, code);
    assert.equal(state.messages.filter((block) => block.text.includes("HIDDEN CONTENTS")).length, 1);
});
test("saved history uses the same terminal statuses, error details, and Python granularity", () => {
    const actor: ActiveAgentSnapshot = {
        agent_id: "agent-1", agent_name: "root", agent_kind: "root", agent_role: "root",
        status: "idle", is_root: true, current_async_task_id: "", metadata: {}, session: {},
        steps: [
            { type: "task", task: "inspect the workspace" },
            { ...action({
                    tool_calls: [call({ status: "denied", error: "outside workspace" } as Partial<ToolCallDisplay>)],
                    observations: ["outside workspace"]
                }),
                type: "action" },
            { ...action({
                    step_num: 2, code_action: 'read(path="README.md")', tool_calls: [call({ name: "Python", args: { code: 'read(path="README.md")' }, is_terminal: true })],
                    observations: ["HIDDEN CONTENTS"], round_outcome: "submitted", output: "Final answer"
                }),
                type: "action" },
        ]
    };
    const blocks = presentActiveAgent(actor);
    const records = blocks.filter((block) => block.kind === "tool");
    assert.deepEqual(records.map((block) => block.toolCall?.name), ["read", "Python"]);
    assert.deepEqual(records.map((block) => block.toolCall?.status), ["denied", "completed"]);
    assert.equal(records[0]?.toolCall?.error, "outside workspace");
    assert.deepEqual(blocks.filter((block) => block.kind === "final").map((block) => block.text), ["Final answer"]);
    // A submitted CodeAct wrapper leaves its final content to the final reply.
    assert.equal(blocks.filter((block) => block.text.includes("HIDDEN CONTENTS")).length, 0);
});
test("thinking and submit_output do not duplicate the final reply or step transcript", () => {
    const thought = "The file has enough information.";
    const output = "Final answer";
    const submit = { tool_name: "submit_output", tool_index: 0, call_id: "submit-1", tool_args: { output } };
    const state = receiveAll([
        event({ kind: "thinking", data: { thought } }),
        event({ data: submit }),
        event({ kind: "tool_end", data: { ...submit, status: "completed", observation: output } }),
        event({
            kind: "step_end",
            data: {
                step: action({
                    thought, tool_calls: [call({ name: "submit_output", call_id: "submit-1", args: { output }, is_terminal: true } as Partial<ToolCallDisplay>)],
                    round_outcome: "submitted", output, observations: [output]
                })
            }
        }),
        event({ kind: "round_end", data: {
                result: {
                    round_id: "round-1",
                    status: "completed",
                    output,
                    reason: "",
                    error: null
                }
            } }),
    ]);
    assert.equal(toolMessages(state).length, 0);
    assert.equal(state.messages.filter((block) => block.kind === "thinking").length, 1);
    assert.deepEqual(state.messages.filter((block) => block.kind === "final").map((block) => block.text), [output]);
});
// call_id identifies the invocation even when transport envelopes omit or
// disagree on the enclosing step number. The presenter must never fall back
// to names or array positions when two calls run concurrently.
test("call identity survives missing or different step numbers", () => {
    const state = receiveAll([
        event({ step_num: 1 }),
        event({
            kind: "tool_end",
            step_num: null,
            data: { ...event().data, status: "failed", error: "not readable" }
        }),
        event({ step_num: 99 }),
    ]);
    assert.equal(toolMessages(state).length, 1);
    assert.equal(toolMessages(state)[0].toolCall?.status, "failed");
    assert.equal(toolMessages(state)[0].toolCall?.error, "not readable");
});
test("committed step restores missing tool lifecycle rows", () => {
    const blocks = presentStreamEvent(event({
        kind: "step_end",
        data: {
            step: action({ thought: "The read is finished.", tool_calls: [call()] })
        }
    }));
    assert.equal(blocks.some((block) => block.kind === "tool"), true);
    assert.equal(blocks.find((block) => block.kind === "thinking")?.text, "The read is finished.");
});
test("uncorrelated tool events do not guess a call identity from tool name or index", () => {
    for (const eventType of ["tool_start", "tool_end"]) {
        assert.deepEqual(presentStreamEvent(event({
            kind: eventType as "tool_start" | "tool_end", data: { tool_name: "read", tool_index: 0, status: "completed" }
        })), []);
    }
});
test("a failed result and a caught exception stay together in the original tool record", () => {
    const observation = "process exited before producing its report";
    const error = "RuntimeError: report unavailable";
    const state = receiveAll([
        event(),
        event({
            kind: "tool_end",
            data: { ...event().data, status: "failed", observation, error }
        }),
        event({
            kind: "step_end",
            data: {
                step: action({
                    tool_calls: [call({ status: "failed", error })], observations: [observation]
                })
            }
        }),
        event({ event_id: "delayed-start" }),
    ]);
    assert.equal(toolMessages(state).length, 1);
    const record = toolMessages(state)[0];
    assert.equal(record.toolCall?.status, "failed");
    assert.equal(record.toolCall?.observation, observation);
    assert.equal(record.toolCall?.error, error);
    assert.match(record.text, /process exited before producing its report/);
    assert.match(record.text, /RuntimeError: report unavailable/);
    assert.equal(state.messages.some((block) => block.kind === "error"), false);
});
test("explicit failed status preserves a returned error without an exception field", () => {
    const observation = "access to this path was denied by the tool";
    const state = receiveAll([
        event(),
        event({ kind: "tool_end", data: { ...event().data, status: "failed", observation, error: null } }),
    ]);
    assert.equal(toolMessages(state).length, 1);
    assert.equal(toolMessages(state)[0].toolCall?.status, "failed");
    assert.equal(toolMessages(state)[0].toolCall?.observation, observation);
    assert.equal(state.messages.some((block) => block.kind === "error"), false);
});
test("tool output text never overrides its explicit execution status", () => {
    const blocks = presentStreamEvent(event({
        kind: "tool_end",
        data: { ...event().data, status: "completed", observation: "Error: a sample log message", error: null }
    }));
    assert.equal(blocks[0]?.toolCall?.status, "completed");
    assert.equal(blocks[0]?.toolCall?.observation, "Error: a sample log message");
    assert.match(blocks[0]?.text, /Error: a sample log message/);
    assert.equal(blocks.some((block) => block.kind === "error"), false);
});
test("missing tool status stays unknown even when an error field is present", () => {
    const blocks = presentStreamEvent(event({
        kind: "tool_end",
        data: { ...event().data, error: "this transport did not include a status" }
    }));
    assert.equal(blocks[0]?.toolCall?.status, "unknown");
    assert.equal(blocks[0]?.toolCall?.error, "this transport did not include a status");
});
test("CodeAct process errors remain separate from completed tools in live and restored history", () => {
    const error = "Submitted output did not match the required schema";
    const code = "submit_output(output={})";
    const failed = action({ code_action: code, error });
    // A validation error can arrive before a saved wrapper is available in the
    // live action step. It must not be mistaken for an old Python exception.
    const live = presentStreamEvent(event({ kind: "step_end", data: {
            step: failed
        } }));
    assert.deepEqual(live.filter((block) => block.kind === "error").map(({kind, text}) => ({kind, text})), [{ kind: "error", text: error }]);
    const restored = presentStreamStep({ ...failed, tool_calls: [call({
                name: "Python", args: { code }, status: "completed", is_terminal: true
            })] });
    assert.equal(restored.find((block) => block.kind === "tool")?.toolCall?.status, "completed");
    assert.deepEqual(restored.filter((block) => block.kind === "error"), [{ kind: "error", text: error }]);
});
test("saved CodeAct wrapper preserves status and error result without duplicating inner calls", () => {
    const code = 'read(path="private.md")';
    const observation = "the wrapped command could not read private.md";
    const error = "PermissionError: private.md";
    const wrapper = call({
        call_id: "python-1", name: "Python", args: { code }, status: "failed", error
    });
    const actor: ActiveAgentSnapshot = {
        agent_id: "agent-1", agent_name: "root", agent_kind: "root", agent_role: "root",
        status: "idle", is_root: true, current_async_task_id: "", metadata: {}, session: {},
        steps: [{ ...action({
                    code_action: code, tool_calls: [call(), wrapper], observations: [observation]
                }),
                type: "action" }]
    };
    const restored = presentActiveAgent(actor);
    const tools = restored.filter((block) => block.kind === "tool");
    assert.equal(tools.length, 1);
    assert.equal(tools[0].toolCall?.callId, "python-1");
    assert.equal(tools[0].toolCall?.name, "Python");
    assert.equal(tools[0].toolCall?.status, "failed");
    assert.equal(tools[0].toolCall?.error, error);
    assert.equal(tools[0].toolCall?.observation, observation);
    assert.equal(restored.some((block) => block.kind === "error"), false);
});
