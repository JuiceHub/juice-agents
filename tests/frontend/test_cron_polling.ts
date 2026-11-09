import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { PassThrough } from "node:stream";
import test, { type TestContext } from "node:test";
import { pathToFileURL } from "node:url";
import { useCronPolling } from "../../frontend/cli/src/hooks/useCronPolling.js";
import type { AsyncTaskState, CronTask, CronTaskFireResponse } from "../../frontend/shared/src/gateway/types.js";
import type { MessageBlock } from "../../frontend/shared/src/presenter/stream.js";
// Tests live outside the frontend workspace. Resolve its installed React/Ink
// from the CLI package so the harness uses the same React instance as the hook.
const requireCli = createRequire(new URL("../../frontend/cli/package.json", import.meta.url));
const { createElement, useCallback, useState } = requireCli("react");
const ink = import(pathToFileURL(requireCli.resolve("ink")).href);
type PollingOptions = Parameters<typeof useCronPolling>[0];
type PollingClient = PollingOptions["client"];
type TaskUpdate = Parameters<PollingOptions["setAsyncTasks"]>[0];
type QueueUpdate = Parameters<PollingOptions["setQueuedMessages"]>[0];
function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: Error) => void;
    const promise = new Promise<T>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}
// Ink schedules passive effects through setImmediate. Leave that scheduler and
// its terminal-output timers real; only the cron interval uses the fake clock.
async function flushEffects() {
    for (let turn = 0; turn < 4; turn += 1) {
        await new Promise<void>((resolve) => setImmediate(resolve));
    }
}
async function mountPolling(t: TestContext, client: PollingClient) {
    const { render } = await ink;
    t.mock.timers.enable({ apis: ["setInterval"] });
    const stdout = new PassThrough();
    Object.assign(stdout, { columns: 80, rows: 24, isTTY: false });
    stdout.resume();
    const messages: MessageBlock[] = [];
    const writes = { tasks: 0, queue: 0, renders: 0 };
    let snapshot = { tasks: [] as AsyncTaskState[], queue: ["existing prompt"] };
    function Probe({ enabled, runnerId }: {
        enabled: boolean;
        revision: number;
        runnerId?: string;
    }) {
        writes.renders += 1;
        const [tasks, updateTasks] = useState([] as AsyncTaskState[]);
        const [queue, updateQueue] = useState(["existing prompt"]);
        const addMessage = useCallback((message: MessageBlock) => messages.push(message), []);
        const setAsyncTasks = useCallback((update: TaskUpdate) => {
            writes.tasks += 1;
            updateTasks(update);
        }, []);
        const setQueuedMessages = useCallback((update: QueueUpdate) => {
            writes.queue += 1;
            updateQueue(update);
        }, []);
        snapshot = { tasks, queue };
        // A fresh options object on every render reproduces the original context:
        // listAsyncTasks returns a fresh array, which causes another React render.
        useCronPolling({
            client,
            baseDir: "/workspace",
            runnerId,
            enabled,
            addMessage,
            setAsyncTasks,
            setQueuedMessages
        });
        return null;
    }
    const instance = render(createElement(Probe, { enabled: true, revision: 0 }), {
        stdout,
        patchConsole: false,
        exitOnCtrlC: false
    });
    let mounted = true;
    const unmount = async () => {
        if (!mounted)
            return;
        mounted = false;
        instance.unmount();
        await flushEffects();
        instance.cleanup();
    };
    t.after(async () => {
        await unmount();
        t.mock.timers.reset();
        stdout.destroy();
    });
    return {
        messages,
        writes,
        snapshot: () => snapshot,
        rerender: async (enabled = true, revision = 0, runnerId?: string) => {
            instance.rerender(createElement(Probe, { enabled, revision, runnerId }));
            await flushEffects();
        },
        advance: async (ms: number) => {
            t.mock.timers.tick(ms);
            await flushEffects();
        },
        unmount
    };
}
const firedTask: CronTask = {
    id: "cron-one",
    cron: "* * * * *",
    prompt: "scheduled prompt",
    created_at: 1
};
test("switching sessions discards a late task badge response from the previous Runner", async (t) => {
    const pending = deferred<AsyncTaskState[]>();
    const harness = await mountPolling(t, {
        fireDueCronTasks: async () => ({ fired: [] }),
        listAsyncTasks: async () => pending.promise
    });
    await flushEffects();
    await harness.rerender(true, 1, "target-session");
    pending.resolve([{ async_task_id: "old-task", description: "previous session" } as AsyncTaskState]);
    await flushEffects();
    assert.deepEqual(harness.snapshot().tasks, []);
    assert.equal(harness.writes.tasks, 0);
});
test("cron polling keeps its one-second interval across state and parent rerenders", async (t) => {
    let fires = 0;
    let lists = 0;
    const harness = await mountPolling(t, {
        async fireDueCronTasks(params) {
            assert.deepEqual(params, { base_dir: "/workspace" });
            fires += 1;
            return { fired: [] };
        },
        async listAsyncTasks(params) {
            assert.deepEqual(params, { statuses: ["pending", "running"] });
            lists += 1;
            return []; // Deliberately return a new array on every heartbeat.
        }
    });
    await flushEffects();
    assert.equal(fires, 1);
    assert.equal(lists, 1);
    assert.ok(harness.writes.renders >= 2, "the fresh task array must actually trigger a render");
    for (let revision = 1; revision <= 5; revision += 1) {
        await harness.rerender(true, revision);
    }
    assert.equal(fires, 1, "UI renders must not restart the immediate heartbeat");
    await harness.advance(999);
    assert.equal(fires, 1);
    await harness.advance(1);
    assert.equal(fires, 2);
    assert.equal(lists, 2);
    await harness.advance(999);
    assert.equal(fires, 2);
    await harness.advance(1);
    assert.equal(fires, 3);
    assert.equal(lists, 3);
    assert.equal(harness.writes.tasks, 3);
    assert.deepEqual(harness.messages, []);
});
test("cron polling does not overlap slow cron or async-task requests", async (t) => {
    const fire = deferred<CronTaskFireResponse>();
    const tasks = deferred<AsyncTaskState[]>();
    let fires = 0;
    let lists = 0;
    const harness = await mountPolling(t, {
        async fireDueCronTasks() {
            fires += 1;
            return fires === 1 ? fire.promise : { fired: [] };
        },
        async listAsyncTasks() {
            lists += 1;
            return lists === 1 ? tasks.promise : [];
        }
    });
    await flushEffects();
    await harness.advance(3000);
    assert.equal(fires, 1);
    assert.equal(lists, 0);
    fire.resolve({ fired: [] });
    await flushEffects();
    await harness.advance(3000);
    assert.equal(fires, 1, "the heartbeat remains in flight while task refresh waits");
    assert.equal(lists, 1);
    tasks.resolve([]);
    await flushEffects();
    await harness.advance(1000);
    assert.equal(fires, 2);
    assert.equal(lists, 2);
});
test("pausing prevents new requests while already-claimed cron tasks remain queued", async (t) => {
    const first = deferred<CronTaskFireResponse>();
    let fires = 0;
    let lists = 0;
    const harness = await mountPolling(t, {
        async fireDueCronTasks() {
            fires += 1;
            return fires === 1 ? first.promise : { fired: [] };
        },
        async listAsyncTasks() {
            lists += 1;
            return [];
        }
    });
    await flushEffects();
    await harness.rerender(false);
    await harness.advance(5000);
    assert.equal(fires, 1, "paused polling must not send new requests");
    await harness.rerender(true);
    await harness.advance(1000);
    assert.equal(fires, 1, "resuming must respect the still-pending request");
    await harness.rerender(false);
    first.resolve({ fired: [firedTask] });
    await flushEffects();
    assert.deepEqual(harness.snapshot().queue, ["existing prompt", "scheduled prompt"]);
    assert.equal(harness.messages.length, 1);
    assert.equal(harness.writes.queue, 1);
    assert.equal(harness.writes.tasks, 0);
    assert.equal(lists, 0);
    await harness.rerender(true);
    await harness.advance(1000);
    assert.equal(fires, 2);
    assert.equal(lists, 1);
});
for (const stop of ["pause", "unmount"] as const) {
    test(`${stop} ignores a late async-task response`, async (t) => {
        const tasks = deferred<AsyncTaskState[]>();
        let lists = 0;
        const harness = await mountPolling(t, {
            async fireDueCronTasks() {
                return { fired: [] };
            },
            async listAsyncTasks() {
                lists += 1;
                return tasks.promise;
            }
        });
        await flushEffects();
        assert.equal(lists, 1);
        if (stop === "pause")
            await harness.rerender(false);
        else
            await harness.unmount();
        tasks.resolve([]);
        await flushEffects();
        assert.equal(harness.writes.tasks, 0);
        assert.equal(harness.writes.queue, 0);
        assert.deepEqual(harness.messages, []);
    });
}
test("unmounted polling ignores late request errors", async (t) => {
    const fire = deferred<CronTaskFireResponse>();
    const harness = await mountPolling(t, {
        async fireDueCronTasks() {
            return fire.promise;
        },
        async listAsyncTasks() {
            assert.fail("a failed cron request must not refresh tasks");
        }
    });
    await flushEffects();
    await harness.unmount();
    fire.reject(new Error("gateway disconnected"));
    await flushEffects();
    assert.deepEqual(harness.messages, []);
    assert.equal(harness.writes.tasks, 0);
    assert.equal(harness.writes.queue, 0);
});
test("unmounted polling ignores late due-task responses", async (t) => {
    const fire = deferred<CronTaskFireResponse>();
    const harness = await mountPolling(t, {
        async fireDueCronTasks() {
            return fire.promise;
        },
        async listAsyncTasks() {
            assert.fail("an unmounted hook must not start a task refresh");
        }
    });
    await flushEffects();
    await harness.unmount();
    fire.resolve({ fired: [firedTask] });
    await flushEffects();
    assert.deepEqual(harness.messages, []);
    assert.equal(harness.writes.tasks, 0);
    assert.equal(harness.writes.queue, 0);
});
test("due cron tasks append their prompts and messages exactly once", async (t) => {
    let fires = 0;
    const harness = await mountPolling(t, {
        async fireDueCronTasks() {
            fires += 1;
            return { fired: [firedTask, { ...firedTask, id: "empty-prompt", prompt: "" }] };
        },
        async listAsyncTasks() {
            assert.fail("a firing heartbeat queues work instead of refreshing task badges");
        }
    });
    await flushEffects();
    assert.equal(fires, 1);
    assert.deepEqual(harness.snapshot().queue, ["existing prompt", "scheduled prompt"]);
    assert.equal(harness.writes.queue, 1);
    assert.deepEqual(harness.messages.map(({ kind, title, text }) => ({ kind, title, text })), [
        { kind: "system", title: "Cron", text: "scheduled task fired: cron-one · * * * * *" },
        { kind: "system", title: "Cron", text: "scheduled task fired: empty-prompt · * * * * *" },
    ]);
    await harness.rerender(true, 1);
    assert.equal(fires, 1, "queue/message updates must not immediately refire cron");
});
