import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { PassThrough } from "node:stream";
import test, { type TestContext } from "node:test";
import { pathToFileURL } from "node:url";
import { useConversationStream } from "../../frontend/shared/src/conversation/useConversationStream.js";
import type { ConversationStreamClient } from "../../frontend/shared/src/conversation/client.js";
import type { AskRequest, RunnerStreamEvent, StreamMessageResult } from "../../frontend/shared/src/gateway/types.js";
const requireCli = createRequire(new URL("../../frontend/cli/package.json", import.meta.url));
const { createElement } = requireCli("react");
const ink = import(pathToFileURL(requireCli.resolve("ink")).href);
function event(kind: RunnerStreamEvent["kind"], runnerId: string, roundId = "round"): RunnerStreamEvent {
    return {
        agent_id: "agent-1",
        timestamp: 1,
        kind, runner_id: runnerId, round_id: roundId, event_id: `${runnerId}:${roundId}:${kind}`,
        permission_mode: "default", agent_mode: "agent", agent_name: "root", agent_role: "root",
        step_num: null,
        data: kind === "round_end" ? { result: { round_id: roundId, status: "completed", output: null, reason: "", error: null } } : {}
    } as RunnerStreamEvent;
}
async function flushEffects() {
    for (let turn = 0; turn < 4; turn++)
        await new Promise<void>((resolve) => setImmediate(resolve));
}
async function mountConversation(t: TestContext, runnerId: string) {
    let ambient: ((value: RunnerStreamEvent) => void) | null = null;
    let ask: ((value: AskRequest) => void) | null = null;
    let foreground: ((value: RunnerStreamEvent) => void) | null = null;
    let complete: ((result: StreamMessageResult) => void) | null = null;
    let cancelled: {
        runner_id: string;
        round_id: string;
    } | undefined;
    let askCount = 0;
    let displayedAsk: AskRequest | null = null;
    const client: ConversationStreamClient = {
        streamMessage: async (_message, onEvent) => {
            foreground = onEvent;
            return new Promise<StreamMessageResult>((resolve) => { complete = resolve; });
        },
        cancelStream: async (target) => { cancelled = target; return { cancelled: true }; },
        setAmbientStreamHandler: (handler) => { ambient = handler; },
        setAmbientAskHandler: (handler) => { ask = handler; }
    };
    let snapshot!: ReturnType<typeof useConversationStream>;
    function Probe({ scope }: {
        scope: string;
    }) {
        snapshot = useConversationStream(client, { runnerId: scope, onAskRequest: (request) => { askCount++; displayedAsk = request; } });
        return null;
    }
    const stdout = new PassThrough();
    Object.assign(stdout, { columns: 80, rows: 24, isTTY: false });
    stdout.resume();
    const { render } = await ink;
    const instance = render(createElement(Probe, { scope: runnerId }), {
        stdout, patchConsole: false, exitOnCtrlC: false
    });
    t.after(async () => {
        instance.unmount();
        await flushEffects();
        instance.cleanup();
        stdout.destroy();
    });
    await flushEffects();
    return {
        current: () => snapshot,
        ambient: (value: RunnerStreamEvent) => ambient?.(value),
        foreground: (value: RunnerStreamEvent) => foreground?.(value),
        complete: (result?: StreamMessageResult) => complete?.(result ?? { done: true, result: { round_id: "round", status: "completed", output: null, reason: "", error: null } }),
        ask: (request?: Partial<AskRequest>) => ask?.({ request_id: "old-ask", question: "late", options: [], multiple: false, allow_custom: false, ...request }),
        askCount: () => askCount,
        displayedAsk: () => displayedAsk,
        cancelled: () => cancelled,
        rerender: async (scope: string) => {
            instance.rerender(createElement(Probe, { scope }));
            await flushEffects();
        }
    };
}
test("detaching after failed replacement preserves history and rejects old session events", async (t) => {
    const harness = await mountConversation(t, "old");
    harness.current().addMessage({ kind: "assistant", text: "saved transcript" });
    harness.ambient(event("round_start", "old"));
    await flushEffects();
    assert.equal(harness.current().streaming, true);
    await harness.rerender("");
    harness.ambient(event("round_start", "old", "late"));
    harness.ask();
    await flushEffects();
    assert.deepEqual(harness.current().messages.map((block) => block.text), ["saved transcript"]);
    assert.equal(harness.current().streaming, false);
    assert.equal(harness.askCount(), 0);
    assert.deepEqual(await harness.current().cancelCurrentStream(), { cancelled: false });
});
test("first prompt binds a new session before React commits its status", async (t) => {
    const harness = await mountConversation(t, "");
    const outcome = harness.current().sendMessage("fresh prompt", undefined, "new");
    harness.foreground(event("round_start", "new"));
    await harness.rerender("new");
    assert.equal(harness.current().streaming, true);
    assert.equal(harness.current().streamInFlight, true);
    harness.foreground(event("round_end", "new"));
    harness.complete();
    assert.equal(await outcome, "completed");
    await flushEffects();
    assert.equal(harness.current().streamInFlight, false);
    assert.equal(harness.current().streaming, false);
    assert.deepEqual(harness.current().messages.filter((block) => block.kind === "user").map((block) => block.text), ["fresh prompt"]);
});
test("detaching also releases a request that has not emitted its first event", async (t) => {
    const harness = await mountConversation(t, "old");
    const outcome = harness.current().sendMessage("pending prompt", undefined, "old");
    await flushEffects();
    assert.equal(harness.current().streamInFlight, true);
    await harness.rerender("");
    assert.equal(harness.current().streamInFlight, false);
    assert.equal(harness.current().streaming, false);
    harness.foreground(event("round_start", "old"));
    harness.complete();
    assert.equal(await outcome, "superseded");
    await flushEffects();
    assert.deepEqual(harness.current().messages.map((block) => block.text), ["pending prompt"]);
});
test("new session accepts its events and cancellation while rejecting the previous session", async (t) => {
    const harness = await mountConversation(t, "old");
    harness.ambient(event("round_start", "old"));
    await flushEffects();
    await harness.rerender("new");
    harness.current().clearMessages();
    harness.ambient(event("round_start", "old", "late"));
    harness.ambient(event("round_start", "new", "fresh"));
    await flushEffects();
    assert.deepEqual(Object.keys(harness.current().activeRounds), ["fresh"]);
    await harness.current().cancelCurrentStream();
    assert.deepEqual(harness.cancelled(), { runner_id: "new", round_id: "fresh" });
});
test("resume binds the target Runner before React commits status and rejects late old output", async (t) => {
    const harness = await mountConversation(t, "old");
    harness.current().bindRunner("target");
    harness.current().clearMessages();
    harness.ambient({
        ...event("round_end", "old"),
        data: {
            result: {
                round_id: "round-1",
                status: "completed",
                output: "old answer",
                reason: "",
                error: null
            }
        } });
    harness.ambient(event("round_start", "target"));
    await harness.rerender("target");
    assert.deepEqual(harness.current().messages, []);
    assert.equal(harness.current().streaming, true);
    await harness.current().cancelCurrentStream();
    assert.deepEqual(harness.cancelled(), { runner_id: "target", round_id: "round" });
});

test("root completion preserves an independent child Ask until that child actually ends", async (t) => {
    const harness = await mountConversation(t, "runner");
    harness.ask({ runner_id: "runner", agent_id: "background", round_id: "child-round", cancellation_round_id: "child-round" });
    harness.ambient(event("round_end", "runner", "root-round"));
    await flushEffects();
    assert.equal(harness.displayedAsk()?.agent_id, "background");
    harness.ambient({ ...event("round_end", "runner", "child-round"), agent_id: "background", agent_role: "worker" });
    await flushEffects();
    assert.equal(harness.displayedAsk(), null);
});
test("session scope rejects foreign Ask requests and cancellation clears synchronous descendant interactions", async (t) => {
    const harness = await mountConversation(t, "old");
    await harness.rerender("new");
    harness.ask({ runner_id: "old", agent_id: "old-child", round_id: "old-round" });
    assert.equal(harness.askCount(), 0);
    harness.ask({ runner_id: "new", agent_id: "sync-child", round_id: "child-round", cancellation_round_id: "root-round" });
    harness.ambient(event("round_start", "new", "root-round"));
    await flushEffects();
    assert.equal(harness.displayedAsk()?.agent_id, "sync-child");
    harness.ambient(event("round_end", "new", "root-round"));
    await flushEffects();
    assert.equal(harness.displayedAsk(), null);
});
test("reconnect restores the active round for cancellation without an active request token", async (t) => {
    const harness = await mountConversation(t, "runner");
    harness.current().applySnapshot({ runner_id: "runner", permission_mode: "default", agent_mode: "agent", root_agent_name: "root", agents: [{
        agent_id: "root", agent_name: "root", agent_role: "root", agent_kind: "persistent", status: "running", is_root: true,
        current_async_task_id: "", metadata: {}, session: { round_checkpoint: { round_id: "restored-round", status: "active" } }, steps: [],
    }] });
    await flushEffects();
    await harness.current().cancelCurrentStream();
    assert.deepEqual(harness.cancelled(), { runner_id: "runner", round_id: "restored-round" });
});

for (const startsBeforeEnding of [false, true]) {
    test(`cancellation before the first round event settles ${startsBeforeEnding ? "after round_start" : "without round_start"}`, async (t) => {
        const harness = await mountConversation(t, "runner");
        const outcome = harness.current().sendMessage("$", undefined, "runner");
        await harness.current().cancelCurrentStream();
        await flushEffects();
        assert.equal(harness.current().stopping, true);

        // An independent child ending cannot acknowledge the root request.
        harness.ambient({ ...event("round_end", "runner", "child-round"), agent_id: "child", agent_role: "worker" });
        await flushEffects();
        assert.equal(harness.current().stopping, true);
        if (startsBeforeEnding) harness.ambient(event("round_start", "runner"));

        const result: StreamMessageResult = { done: true, result: {
            round_id: "round", status: "cancelled", output: null, reason: "user_cancelled", error: null
        } };
        harness.ambient({ ...event("round_end", "runner"), data: { result: result.result } } as RunnerStreamEvent);
        harness.complete(result);
        await outcome;
        await flushEffects();

        assert.equal(harness.current().streaming, false);
        assert.equal(harness.current().streamInFlight, false);
        assert.equal(harness.current().stopping, false, "the committed result must release the pending cancellation indicator");
        assert.deepEqual(harness.current().stoppingRounds, []);
        assert.equal(harness.current().roundResults.round.status, "cancelled");
    });
}
