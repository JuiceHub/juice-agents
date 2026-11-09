import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire, syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import test, { type TestContext } from "node:test";
import { pathToFileURL } from "node:url";
import { stripVTControlCharacters } from "node:util";
import { executeCommand } from "../../frontend/cli/src/lib/commands.js";
import { getCommandExecutionPolicy, isGlobalCommand } from "../../frontend/cli/src/lib/commandScheduling.js";
import type { ActiveAgentSnapshot, ActiveAgentStep, AsyncTaskState, RunnerStreamEvent, SessionStatus } from "../../frontend/shared/src/gateway/types.js";
// Resolve the workspace's React instance; the tests themselves live outside it.
const requireCli = createRequire(new URL("../../frontend/cli/package.json", import.meta.url));
const { createElement } = requireCli("react");
const ink = import(pathToFileURL(requireCli.resolve("ink")).href);
const runtime = {
    base_dir: "/workspace",
    permission_mode: "accept",
    agent_mode: "team",
    agent_type: "codeact",
    model_name: "current-model",
    model_effort: "high"
};
function status(runnerId: string): SessionStatus {
    return {
        ...runtime,
        runner_id: runnerId,
        root_agent_name: "root",
        started: true,
        resumed: false,
        backend: "test",
        provider_model_name: "current-model"
    };
}
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => { resolve = done; });
    return { promise, resolve };
}
// Ink batches terminal writes on a short timer. Flush its scheduler as well as
// passive React effects before asserting the final rendered transcript.
async function flush() {
    await new Promise<void>((resolve) => setTimeout(resolve, 45));
    for (let turn = 0; turn < 6; turn += 1) {
        await new Promise<void>((resolve) => setImmediate(resolve));
    }
}
function gatewayHarness() {
    let active: SessionStatus | null = null;
    const sessions = new Map<string, SessionStatus>();
    const histories = new Map<string, string[]>();
    const children = new Map<string, ActiveAgentSnapshot[]>();
    const tasks = new Map<string, AsyncTaskState[]>();
    const starts: Record<string, unknown>[] = [];
    const streams: Array<{
        runnerId: string;
        roundId: string;
        message: string;
        finish: () => void;
        settle: () => void;
    }> = [];
    const agentMessages: unknown[] = [];
    let holdNextTurn = false;
    let holdNextResponse = false;
    let nextStart: (() => Promise<void>) | null = null;
    let nextFailure: "keep" | "stop" | "replace" | null = null;
    let ambient: ((event: RunnerStreamEvent) => void) | null = null;
    const client: any = {
        activateSession: async () => ({ activated: true }),
        loadEffectiveConfig: async () => ({ runtime }),
        listModels: async () => [{ model_name: runtime.model_name, supported_efforts: ["high"], backend: "test", provider_model_name: "current-model" }],
        listSkills: async () => ({ skills: [] }),
        listPlugins: async () => ({ plugins: [] }),
        listAvailableAgents: async () => ({ agents: [] }),
        fireDueCronTasks: async () => ({ fired: [] }),
        listAsyncTasks: async () => tasks.get(active?.runner_id || "") || [],
        setAmbientStreamHandler: (handler: typeof ambient) => { ambient = handler; },
        setAmbientAskHandler: () => { },
        async startSession(params: Record<string, unknown>) {
            starts.push(params);
            if (nextStart) {
                const wait = nextStart;
                nextStart = null;
                await wait();
            }
            if (nextFailure) {
                const failure = nextFailure;
                nextFailure = null;
                if (failure === "stop")
                    active = null;
                if (failure === "replace") {
                    active = { ...status("replacement-session"), started: false };
                    sessions.set(active.runner_id, active);
                    histories.set(active.runner_id, []);
                }
                throw new Error("session creation failed");
            }
            active = { ...status(`runner-${starts.length}`), ...params } as SessionStatus;
            sessions.set(active.runner_id, active);
            histories.set(active.runner_id, []);
            return active;
        },
        describeSession: async () => active || { ...status(""), started: false },
        describeActiveAgents: async () => ({
            runner_id: active?.runner_id || "",
            permission_mode: runtime.permission_mode,
            agent_mode: runtime.agent_mode,
            root_agent_name: "root",
            agents: [
                {
                    agent_id: "root-id", agent_name: "root", agent_kind: "root", agent_role: "root",
                    status: "idle", is_root: true, current_async_task_id: "", metadata: {}, session: {},
                    // 模拟真实报告中的持久化步骤，让恢复测试检查界面上的历史，
                    // 而不只检查网关切换到了哪个 runner。
                    steps: (histories.get(active?.runner_id || "") || []).flatMap((message, index): ActiveAgentStep[] => {
                        const roundId = streams.filter((stream) => stream.runnerId === active?.runner_id)[index]?.roundId;
                        return [
                        { type: "task", task: message, round_id: roundId },
                        {
                            type: "action", round_id: roundId, step_num: 1, model_output: "", thought: "", tool_calls: [],
                            code_action: "", reasoning_content: "", observations: [], observation_images: [],
                            attachments: [], error: null, round_outcome: "submitted", output: `answer: ${message}`
                        },
                    ]; })
                },
                ...(children.get(active?.runner_id || "") || [
                    { agent_id: "worker-id", agent_name: "worker", agent_kind: "worker", agent_role: "worker", status: "idle", is_root: false, current_async_task_id: "", metadata: {}, session: {}, steps: [] },
                ]),
            ]
        }),
        async streamMessage(message: string, onEvent: (event: RunnerStreamEvent) => void) {
            assert.ok(active, "ordinary input requires a live session");
            const runnerId = active.runner_id;
            histories.get(runnerId)!.push(message);
            const roundId = `round-${streams.length + 1}`;
            const event = (kind: RunnerStreamEvent["kind"]): RunnerStreamEvent => ({
                agent_id: "root-id",
                timestamp: 1,
                kind, runner_id: runnerId, round_id: roundId, event_id: `${roundId}:${kind}`,
                permission_mode: runtime.permission_mode, agent_mode: runtime.agent_mode,
                agent_name: "root", agent_role: "root", step_num: null,
                data: {}
            });
            const completed = deferred<void>();
            const response = deferred<void>();
            const waitForResponse = holdNextResponse;
            holdNextResponse = false;
            streams.push({ runnerId, roundId, message, finish: () => completed.resolve(), settle: () => response.resolve() });
            onEvent(event("round_start"));
            if (holdNextTurn) {
                holdNextTurn = false;
                await completed.promise;
            }
            onEvent({
                ...event("round_end"),
                data: {
                    result: {
                        round_id: roundId,
                        status: "completed",
                        output: `answer: ${message}`,
                        reason: "",
                        error: null
                    }
                } });
            if (waitForResponse)
                await response.promise;
            return { done: true };
        },
        sendAgentMessage: async (params: unknown) => {
            agentMessages.push(params);
            return { accepted: true };
        },
        resumeSession: async ({ runner_id }: {
            runner_id: string;
        }) => {
            active = sessions.get(runner_id)!;
            return active;
        },
        listSessions: async () => [...sessions.values()].map((session) => ({
            runner_id: session.runner_id,
            permission_mode: session.permission_mode,
            agent_mode: session.agent_mode,
            root_agent_name: session.root_agent_name,
            root_dir: runtime.base_dir,
            updated_at: "2026-11-04T00:00:00Z",
            first_user_request_preview: histories.get(session.runner_id)?.[0] || ""
        })),
        stopSession: async () => ({ stopped: true }),
        cancelStream: async () => ({ cancelled: true })
    };
    return {
        client, starts, streams, sessions, histories, children, tasks, agentMessages,
        active: () => active,
        holdTurn: () => { holdNextTurn = true; },
        holdResponse: () => { holdNextResponse = true; },
        holdCreation: () => {
            const blocked = deferred<void>();
            nextStart = () => blocked.promise;
            return () => blocked.resolve();
        },
        failCreation: (failure: "keep" | "stop" | "replace") => { nextFailure = failure; },
        emit: (event: RunnerStreamEvent) => ambient?.(event)
    };
}
async function mountApp(t: TestContext) {
    // Composer submissions persist history. Redirect its home lookup to a
    // disposable directory without changing the process environment.
    const temporaryHome = mkdtempSync(join(tmpdir(), "juice-cli-clear-"));
    const homedirMock = t.mock.method(requireCli("node:os"), "homedir", () => temporaryHome);
    syncBuiltinESMExports();
    const stdin = new PassThrough();
    Object.assign(stdin, { isTTY: true, setRawMode: () => stdin, ref: () => stdin, unref: () => stdin });
    const stdout = new PassThrough();
    Object.assign(stdout, { columns: 120, rows: 40, isTTY: false });
    const frames: string[] = [];
    stdout.on("data", (chunk) => {
        const frame = stripVTControlCharacters(chunk.toString());
        if (frame.trim())
            frames.push(frame);
    });
    stdout.resume();
    const gateway = gatewayHarness();
    const { render } = await ink;
    const { App } = await import(new URL("../../frontend/cli/src/app.tsx", import.meta.url).href);
    const instance = render(createElement(App, { client: gateway.client, baseDir: runtime.base_dir }), {
        stdin, stdout, debug: true, patchConsole: false, exitOnCtrlC: false
    });
    const { default: inkInstances } = await import(new URL("./instances.js", pathToFileURL(requireCli.resolve("ink"))).href);
    t.after(async () => {
        instance.unmount();
        await flush();
        instance.cleanup();
        stdin.destroy();
        stdout.destroy();
        homedirMock.mock.restore();
        syncBuiltinESMExports();
        rmSync(temporaryHome, { recursive: true, force: true });
    });
    await flush();
    const key = async (input: string) => { stdin.write(input); await flush(); };
    return {
        ...gateway,
        frame: () => frames.at(-1) || "",
        key,
        submit: async (input: string) => { await key(input); await key("\r"); },
        burstSubmit: async (inputs: string[]) => {
            // Ink treats same-tick terminal chunks as one paste. Invoke the actual
            // mounted composer's callback to submit twice before React can commit,
            // rather than accidentally testing the terminal paste parser instead.
            const fibers = [inkInstances.get(stdout).container.current];
            let submit: ((input: string) => Promise<unknown>) | null = null;
            while (fibers.length) {
                const fiber = fibers.pop();
                if (fiber.type?.name === "ComposerDock") {
                    submit = fiber.memoizedProps.onSubmit;
                    break;
                }
                if (fiber.child)
                    fibers.push(fiber.child);
                if (fiber.sibling)
                    fibers.push(fiber.sibling);
            }
            assert.ok(submit, "mounted App exposes its composer submission callback");
            const submissions = inputs.map((input) => submit!(input));
            await flush();
            return submissions;
        }
    };
}
test("/clear creates immediately from pending launch settings", async () => {
    const gateway = gatewayHarness();
    const result = await executeCommand("/clear", gateway.client, runtime.base_dir, {
        sessionStatus: null, pendingRuntime: runtime
    });
    assert.deepEqual(gateway.starts, [runtime]);
    assert.equal(result.sessionStatus?.runner_id, "runner-1");
    assert.equal(result.clearMessages, true);
    assert.deepEqual(gateway.histories.get("runner-1"), []);
});
test("/clear preserves live settings instead of stale pending configuration", async () => {
    const gateway = gatewayHarness();
    const result = await executeCommand("/clear", gateway.client, runtime.base_dir, {
        sessionStatus: status("previous"), pendingRuntime: { ...runtime, model_name: "stale", agent_type: "react" }
    });
    assert.deepEqual(gateway.starts, [runtime]);
    assert.equal(result.pendingRuntime?.agent_type, "codeact");
    assert.equal(getCommandExecutionPolicy("/clear"), "fifo");
    assert.equal(isGlobalCommand("/clear"), true);
    assert.equal(isGlobalCommand("/clear-more"), false);
    assert.equal(getCommandExecutionPolicy("/resume runner-1"), "fifo");
    assert.equal(isGlobalCommand("/resume"), true);
    assert.equal(isGlobalCommand("/resume runner-1"), true);
    assert.equal(isGlobalCommand("/resume-more"), false);
});
test("failed /clear keeps an empty session whose first round has not started", async () => {
    const empty = { ...status("empty-session"), started: false };
    const result = await executeCommand("/clear", {
        startSession: async () => { throw new Error("creation failed"); },
        describeSession: async () => empty
    } as any, runtime.base_dir, { sessionStatus: empty });
    assert.equal(result.sessionStatus, empty);
    assert.equal(result.clearMessages, false);
});
test("failed /resume reconciles a closed old Runner without clearing its readable history", async () => {
    const result = await executeCommand("/resume missing", {
        resumeSession: async () => { throw new Error("runner not found"); },
        describeSession: async () => ({ ...status(""), started: false })
    } as any, runtime.base_dir, { sessionStatus: status("previous"), pendingRuntime: runtime });
    assert.equal(result.sessionStatus, null);
    assert.equal(result.clearMessages, false);
    assert.match(result.blocks[0].text, /runner not found/);
});
test("cold CLI /clear creates before the first prompt with resolved configuration", async (t) => {
    const app = await mountApp(t);
    await app.submit("/clear");
    assert.deepEqual(app.starts, [runtime]);
    assert.deepEqual(app.streams, []);
    assert.deepEqual(app.histories.get("runner-1"), []);
});
test("an empty gateway preview creates a real session before a forwarded prompt and receives its events", async (t) => {
    const app = await mountApp(t);
    // Cold /group queries describe_session before dispatching its prompt. The
    // returned empty-ID launch preview must not become an active session.
    await app.submit("/group first forwarded prompt");
    assert.deepEqual(app.starts, [runtime]);
    assert.deepEqual(app.streams.map(({ runnerId, message }) => [runnerId, message]), [
        ["runner-1", "first forwarded prompt"],
    ]);
    assert.match(app.frame(), /answer: first forwarded prompt/);
    await app.submit("following ordinary prompt");
    assert.equal(app.starts.length, 1);
    assert.equal(app.streams.at(-1)?.runnerId, "runner-1");
    assert.match(app.frame(), /answer: following ordinary prompt/);
});
test("CLI /clear opens an empty session and routes the next prompt there", async (t) => {
    const app = await mountApp(t);
    await app.submit("old conversation");
    assert.match(app.frame(), /old conversation/);
    await app.submit("/clear");
    assert.equal(app.starts.length, 2);
    assert.deepEqual(app.starts[1], runtime);
    assert.deepEqual(app.histories.get("runner-2"), []);
    assert.doesNotMatch(app.frame(), /old conversation/);
    await app.submit("new conversation");
    assert.equal(app.streams.at(-1)?.runnerId, "runner-2");
    assert.match(app.frame(), /new conversation/);
    assert.doesNotMatch(app.frame(), /old conversation/);
    assert.deepEqual(app.histories.get("runner-1"), ["old conversation"]);
    // The old session still exists in the gateway and remains resumable.
    await app.submit("/resume runner-1");
    assert.equal(app.active()?.runner_id, "runner-1");
    await app.submit("continued old conversation");
    assert.equal(app.streams.at(-1)?.runnerId, "runner-1");
});
test("/resume <id> restores old user turns and answers without newer session history", async (t) => {
    const app = await mountApp(t);
    await app.submit("saved first turn");
    await app.submit("saved second turn");
    await app.submit("/clear");
    await app.submit("replacement conversation");
    assert.doesNotMatch(app.frame(), /saved first turn|saved second turn/);
    await app.submit("/resume runner-1");
    assert.equal(app.active()?.runner_id, "runner-1");
    assert.match(app.frame(), /answer: saved first turn/);
    assert.match(app.frame(), /answer: saved second turn/);
    assert.doesNotMatch(app.frame(), /replacement conversation/);
    assert.equal((app.frame().match(/answer: saved first turn/g) || []).length, 1);
    assert.equal((app.frame().match(/answer: saved second turn/g) || []).length, 1);
    assert.equal(app.starts.length, 2);
    await app.submit("continued restored session");
    assert.equal(app.streams.at(-1)?.runnerId, "runner-1");
    assert.match(app.frame(), /answer: continued restored session/);
    assert.doesNotMatch(app.frame(), /replacement conversation/);
});
test("a failed history read reports the target session instead of silently showing an empty conversation", async (t) => {
    const app = await mountApp(t);
    await app.submit("saved root before read failure");
    await app.submit("/clear");
    await app.submit("current root before read failure");
    app.client.describeActiveAgents = async () => { throw new Error("snapshot unavailable"); };
    await app.submit("/resume runner-1");
    assert.equal(app.active()?.runner_id, "runner-1");
    assert.match(app.frame(), /Agent history could not be loaded/);
    assert.doesNotMatch(app.frame(), /current root before read failure/);
});
test("/resume picker restores the selected session's visible history", async (t) => {
    const app = await mountApp(t);
    await app.submit("picker saved conversation");
    await app.submit("/clear");
    await app.submit("picker current conversation");
    await app.submit("/resume");
    assert.match(app.frame(), /Select Session/);
    assert.match(app.frame(), /picker saved conversation/);
    // 当前 runner-2 默认选中；上移选择 runner-1，检验真实按键选择路径。
    await app.key("\u001b[A");
    await app.key("\r");
    assert.equal(app.active()?.runner_id, "runner-1");
    assert.match(app.frame(), /answer: picker saved conversation/);
    assert.doesNotMatch(app.frame(), /picker current conversation|Select Session/);
});
test("/resume picker from a child view returns to the selected root history", async (t) => {
    const app = await mountApp(t);
    await app.submit("saved root conversation");
    await app.submit("/clear");
    await app.submit("current root conversation");
    await app.key("\u0013");
    await app.key("\u001b[B");
    await app.key("\r");
    assert.match(app.frame(), /Viewing: worker/);
    await app.submit("/resume");
    assert.match(app.frame(), /Select Session/);
    await app.key("\u001b[A");
    await app.key("\r");
    assert.equal(app.active()?.runner_id, "runner-1");
    assert.doesNotMatch(app.frame(), /Viewing: worker/);
    assert.match(app.frame(), /answer: saved root conversation/);
    assert.doesNotMatch(app.frame(), /current root conversation/);
    assert.deepEqual(app.agentMessages, []);
});
test("queued /resume in a busy child view sends following input to restored root history", async (t) => {
    const app = await mountApp(t);
    await app.submit("saved session before queue");
    await app.submit("/clear");
    await app.submit("initialize replacement workers");
    app.holdTurn();
    await app.submit("busy replacement round");
    await app.key("\u0013");
    await app.key("\u001b[B");
    await app.key("\r");
    assert.match(app.frame(), /Viewing: worker/);
    await app.submit("/resume runner-1");
    await app.submit("follow restored session");
    // 当前轮次结束前，恢复命令和后续输入都必须留在 FIFO 中；
    // 子视图不能把恢复屏障之后的普通输入直接发给旧 worker。
    assert.equal(app.active()?.runner_id, "runner-2");
    assert.deepEqual(app.agentMessages, []);
    assert.deepEqual(app.streams.map(({ runnerId, message }) => [runnerId, message]), [
        ["runner-1", "saved session before queue"],
        ["runner-2", "initialize replacement workers"],
        ["runner-2", "busy replacement round"],
    ]);
    app.streams[2].finish();
    await flush();
    await flush();
    assert.equal(app.active()?.runner_id, "runner-1");
    assert.equal(app.starts.length, 2);
    assert.deepEqual(app.streams.map(({ runnerId, message }) => [runnerId, message]), [
        ["runner-1", "saved session before queue"],
        ["runner-2", "initialize replacement workers"],
        ["runner-2", "busy replacement round"],
        ["runner-1", "follow restored session"],
    ]);
    assert.match(app.frame(), /answer: saved session before queue/);
    assert.match(app.frame(), /answer: follow restored session/);
    assert.doesNotMatch(app.frame(), /busy replacement round|Viewing: worker/);
    assert.deepEqual(app.agentMessages, []);
});
for (const action of ["select", "cancel"] as const) {
    test(`queued /resume picker holds following input until ${action}`, async (t) => {
        const app = await mountApp(t);
        await app.submit("saved picker session");
        await app.submit("/clear");
        app.holdTurn();
        await app.submit("busy picker session");
        await app.submit("/resume");
        await app.submit("after picker decision");
        app.streams[1].finish();
        await flush();
        await flush();
        assert.match(app.frame(), /Select Session/);
        assert.equal(app.streams.length, 2, "queued input must wait for the session decision");
        if (action === "select") {
            await app.key("\u001b[A");
            await app.key("\r");
        }
        else {
            await app.key("\u001b");
        }
        await flush();
        const targetId = action === "select" ? "runner-1" : "runner-2";
        assert.equal(app.streams.at(-1)?.runnerId, targetId);
        assert.equal(app.streams.at(-1)?.message, "after picker decision");
        assert.doesNotMatch(app.frame(), /Select Session/);
    });
}
test("resuming replaces the whole session including same-named children, tasks, and status", async (t) => {
    const app = await mountApp(t);
    const worker = (id: string, text: string): ActiveAgentSnapshot => ({
        agent_id: id, agent_name: "worker", agent_kind: "worker", agent_role: "worker", status: "idle",
        is_root: false, current_async_task_id: "", metadata: {}, session: {},
        steps: [{ type: "task", task: text }]
    });
    const task = (id: string, description: string): AsyncTaskState => ({
        async_task_id: id, type: "local_bash", status: "running", owner_agent_id: "root", owner_agent_name: "root",
        description, output_dir: "/tmp/test", created_at: 1, started_at: 1, finished_at: null,
        closed_reason: "", error: "", metadata: {}
    });
    await app.submit("saved bound root");
    app.children.set("runner-1", [worker("saved-worker", "saved worker history")]);
    app.tasks.set("runner-1", [task("saved-task", "saved session task")]);
    Object.assign(app.sessions.get("runner-1")!, {
        model_name: "saved-model", permission_mode: "default", agent_mode: "agent", agent_type: "react",
        goal: { objective: "saved objective", status: "paused" }
    });
    await app.submit("/clear");
    app.children.set("runner-2", [worker("other-worker", "other worker history")]);
    app.tasks.set("runner-2", [task("other-task", "other session task")]);
    await app.submit("other bound root");
    await app.key("\u0013");
    await app.key("\u001b[B");
    await app.key("\r");
    assert.match(app.frame(), /other worker history/);
    await app.submit("/resume runner-1");
    assert.match(app.frame(), /saved bound root/);
    assert.match(app.frame(), /saved-model/);
    assert.doesNotMatch(app.frame(), /other bound root|other worker history|Viewing: worker/);
    await app.key("\u0013");
    await app.key("\u001b[B");
    await app.key("\r");
    assert.match(app.frame(), /saved worker history/);
    assert.doesNotMatch(app.frame(), /other worker history/);
    await app.submit("continue saved worker");
    assert.deepEqual(app.agentMessages, [{ agent_name: "worker", message: "continue saved worker" }]);
    assert.equal(app.active()?.runner_id, "runner-1");
    await app.key("\u001b");
    await app.submit("/status");
    assert.match(app.frame(), /saved objective/);
    await app.key("\u0014");
    assert.match(app.frame(), /saved session task/);
    assert.doesNotMatch(app.frame(), /other session task/);
});
test("queued /clear waits for the round and keeps following prompts in FIFO", async (t) => {
    const app = await mountApp(t);
    app.holdTurn();
    await app.submit("busy turn");
    await app.submit("/clear");
    await app.submit("first after clear");
    await app.submit("second after clear");
    assert.equal(app.starts.length, 1);
    assert.deepEqual(app.streams.map((stream) => stream.message), ["busy turn"]);
    app.streams[0].finish();
    await flush();
    await flush();
    assert.equal(app.starts.length, 2);
    assert.deepEqual(app.streams.map(({ runnerId, message }) => [runnerId, message]), [
        ["runner-1", "busy turn"],
        ["runner-2", "first after clear"],
        ["runner-2", "second after clear"],
    ]);
    assert.deepEqual(app.histories.get("runner-1"), ["busy turn"]);
});
test("rapid /clear and ordinary input cannot cross the session barrier", async (t) => {
    const app = await mountApp(t);
    app.holdTurn();
    await app.submit("busy turn");
    await app.burstSubmit(["/clear", "rapid follow-up"]);
    assert.equal(app.starts.length, 1);
    assert.equal(app.streams.length, 1);
    app.streams[0].finish();
    await flush();
    await flush();
    assert.equal(app.starts.length, 2, app.frame());
    assert.deepEqual(app.streams.map(({ runnerId, message }) => [runnerId, message]), [
        ["runner-1", "busy turn"], ["runner-2", "rapid follow-up"],
    ]);
});
test("/clear waits for foreground transport settlement after round_end", async (t) => {
    const app = await mountApp(t);
    app.holdTurn();
    app.holdResponse();
    await app.submit("busy turn");
    await app.submit("/clear");
    app.streams[0].finish();
    await flush();
    assert.equal(app.starts.length, 1);
    app.streams[0].settle();
    await flush();
    assert.equal(app.starts.length, 2);
});
test("/clear submitted during first session creation waits for that first turn", async (t) => {
    const app = await mountApp(t);
    const finishCreation = app.holdCreation();
    app.holdTurn();
    await app.submit("first prompt");
    await app.burstSubmit(["/clear", "after startup clear"]);
    assert.equal(app.starts.length, 1);
    assert.equal(app.streams.length, 0);
    finishCreation();
    await flush();
    assert.equal(app.streams[0]?.runnerId, "runner-1");
    assert.equal(app.starts.length, 1);
    app.streams[0].finish();
    await flush();
    await flush();
    assert.deepEqual(app.streams.map(({ runnerId, message }) => [runnerId, message]), [
        ["runner-1", "first prompt"], ["runner-2", "after startup clear"],
    ]);
});
test("an idle session's prompt dispatch cannot be overtaken by same-tick /clear", async (t) => {
    const app = await mountApp(t);
    await app.submit("/clear");
    app.holdTurn();
    const submissions = await app.burstSubmit(["idle session prompt", "/clear", "after idle clear"]);
    assert.equal(app.starts.length, 1);
    assert.deepEqual(app.streams.map(({ runnerId, message }) => [runnerId, message]), [
        ["runner-1", "idle session prompt"],
    ]);
    app.streams[0].finish();
    await Promise.all(submissions);
    await flush();
    await flush();
    assert.deepEqual(app.streams.map(({ runnerId, message }) => [runnerId, message]), [
        ["runner-1", "idle session prompt"], ["runner-2", "after idle clear"],
    ]);
});
test("/clear remains global in a child view and blocks input during creation", async (t) => {
    const app = await mountApp(t);
    await app.submit("root conversation");
    await app.key("\u0013");
    await app.key("\u001b[B");
    await app.key("\r");
    assert.match(app.frame(), /Viewing: worker/);
    const finishCreation = app.holdCreation();
    await app.submit("/clear");
    assert.equal(app.starts.length, 2);
    assert.deepEqual(app.agentMessages, []);
    await app.submit("after pending creation");
    assert.equal(app.streams.length, 1);
    finishCreation();
    await flush();
    await flush();
    assert.doesNotMatch(app.frame(), /Viewing: worker/);
    assert.equal(app.streams.at(-1)?.runnerId, "runner-2");
    assert.equal(app.streams.at(-1)?.message, "after pending creation");
    assert.deepEqual(app.agentMessages, []);
});
for (const failure of ["keep", "stop"] as const) {
    test(`failed /clear preserves visible records when the gateway ${failure === "keep" ? "keeps" : "stops"} the old session`, async (t) => {
        const app = await mountApp(t);
        await app.submit("keep this history");
        app.failCreation(failure);
        await app.submit("/clear");
        assert.match(app.frame(), /keep this history/);
        assert.match(app.frame(), /session creation failed/);
        assert.equal(app.active()?.runner_id || null, failure === "keep" ? "runner-1" : null);
        await app.submit("after failure");
        assert.equal(app.streams.at(-1)?.runnerId, failure === "keep" ? "runner-1" : "runner-3");
        assert.equal(app.starts.length, failure === "keep" ? 2 : 3);
    });
}
test("failed /clear in a child view keeps its history and returns to root on the next successful creation", async (t) => {
    const app = await mountApp(t);
    await app.submit("root history");
    await app.key("\u0013");
    await app.key("\u001b[B");
    await app.key("\r");
    assert.match(app.frame(), /Viewing: worker/);
    app.failCreation("stop");
    await app.submit("/clear");
    assert.match(app.frame(), /Viewing: worker/);
    assert.match(app.frame(), /session creation failed/);
    await app.submit("new root prompt");
    assert.equal(app.streams.at(-1)?.runnerId, "runner-3");
    assert.equal(app.streams.at(-1)?.message, "new root prompt");
    assert.doesNotMatch(app.frame(), /Viewing: worker/);
    assert.deepEqual(app.agentMessages, []);
});
test("a failed /clear that reveals another Runner cannot send to the old child Agent", async (t) => {
    const app = await mountApp(t);
    await app.submit("old root history");
    await app.key("\u0013");
    await app.key("\u001b[B");
    await app.key("\r");
    app.failCreation("replace");
    await app.submit("/clear");
    assert.match(app.frame(), /Viewing: worker/);
    assert.match(app.frame(), /session creation failed/);
    await app.submit("prompt for actual Runner");
    assert.equal(app.starts.length, 2);
    assert.equal(app.streams.at(-1)?.runnerId, "replacement-session");
    assert.doesNotMatch(app.frame(), /Viewing: worker/);
    assert.deepEqual(app.agentMessages, []);
});
test("child input follows backend message events and keeps send errors in the selected transcript", async (t) => {
    const app = await mountApp(t);
    await app.submit("initialize worker messages");
    await app.key("\u0013");
    await app.key("\u001b[B");
    await app.key("\r");
    assert.match(app.frame(), /Viewing: worker/);
    app.client.sendAgentMessage = async ({ message }: { message: string }) => {
        app.emit({ kind: "message_received", event_id: "accepted-worker-input", timestamp: 1,
            runner_id: app.active()!.runner_id, agent_id: "worker-id", agent_name: "worker", agent_role: "worker",
            round_id: "worker-round", data: { attachments: [{ message_id: "worker-input", attachment_type: "agent_user_message", payload: { text: message } }] } });
        return { accepted: true };
    };
    await app.submit("include humidity in the report");
    assert.match(app.frame(), /include humidity in the report/);
    app.client.sendAgentMessage = async () => ({ accepted: false, error: "worker was released" });
    await app.submit("rejected input");
    assert.match(app.frame(), /Agent message rejected for worker: worker was released/);
    app.client.sendAgentMessage = async () => { throw new Error("gateway disconnected"); };
    await app.submit("unreachable input");
    assert.match(app.frame(), /Agent message failed: gateway disconnected/);
    assert.match(app.frame(), /Viewing: worker/);
});
