import assert from "node:assert/strict";
import test from "node:test";
import type { SessionStatus } from "../../frontend/shared/src/gateway/types.js";
import type { WebGatewayClient } from "../../frontend/web/src/gateway/client.js";
import { executeWebCommand } from "../../frontend/web/src/lib/commands.js";
import { isClearCommand, startWebSession, webActorMessageTarget, WebComposerQueue } from "../../frontend/web/src/lib/session.js";

function status(runnerId: string, overrides: Partial<SessionStatus> = {}): SessionStatus {
  return {
    runner_id: runnerId,
    permission_mode: "accept",
    agent_mode: "plan",
    root_agent_name: "root",
    base_dir: "/workspace",
    started: true,
    resumed: false,
    agent_type: "codeact",
    model_name: "current-model",
    model_effort: "high",
    backend: "openai",
    provider_model_name: "provider-model",
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((success, failure) => { resolve = success; reject = failure; });
  return { promise, resolve, reject };
}

test("Web /clear requests a session and leaves clearing to a successful transition", async () => {
  const result = await executeWebCommand("/clear", {} as WebGatewayClient, {
    baseDir: "/workspace", status: status("old"), sessions: [], models: [],
  });
  assert.equal(result.newSession, true);
  assert.equal(result.clearMessages, false);
  assert.deepEqual(result.blocks, []);
});

test("Web session creation preserves live configuration and commits after startup", async () => {
  const created = deferred<SessionStatus>();
  const calls: unknown[] = [];
  let messages = ["old transcript"];
  let activeActor: string | null = "researcher";
  let active = status("old");
  const client = {
    command: async (command: string, payload: unknown) => {
      calls.push([command, payload]);
      return created.promise;
    },
    describeSession: async () => { throw new Error("unexpected reconciliation"); },
  } as unknown as WebGatewayClient;

  const transition = startWebSession(client, {
    baseDir: "/workspace", status: active,
    config: { runtime: { permission_mode: "default", agent_mode: "agent", agent_type: "react" } },
    modelName: "default-model", modelEffort: "low",
  }, {
    commit: (next) => { active = next; messages = []; activeActor = null; },
    reconcile: () => assert.fail("successful creation must not reconcile"),
  });
  assert.deepEqual(messages, ["old transcript"]);
  assert.equal(activeActor, "researcher");
  assert.equal(active.runner_id, "old");
  assert.deepEqual(calls, [["start_session", {
    base_dir: "/workspace", permission_mode: "accept", agent_mode: "plan", agent_type: "codeact",
    model_name: "current-model", model_effort: "high",
  }]]);

  created.resolve(status("new"));
  await transition;
  assert.equal(active.runner_id, "new");
  assert.deepEqual(messages, []);
  assert.equal(activeActor, null);
});

test("Web /clear creates immediately with workspace choices when no session is active", async () => {
  let payload: unknown;
  let committed = false;
  const client = {
    command: async (_: string, value: unknown) => { payload = value; return status("first"); },
    describeSession: async () => assert.fail("unexpected reconciliation"),
  } as unknown as WebGatewayClient;
  const result = await startWebSession(client, {
    baseDir: "/workspace", status: null,
    config: { runtime: { permission_mode: "accept", agent_mode: "team", agent_type: "codeact" } },
    modelName: "chosen-model", modelEffort: "max",
  }, { commit: () => { committed = true; }, reconcile: () => assert.fail("unexpected reconciliation") });
  assert.equal(result.runner_id, "first");
  assert.equal(committed, true);
  assert.deepEqual(payload, {
    base_dir: "/workspace", permission_mode: "accept", agent_mode: "team", agent_type: "codeact",
    model_name: "chosen-model", model_effort: "max",
  });
});

test("Web command FIFO waits for the round and routes post-clear messages to the new root", async () => {
  let busy = true;
  let active = status("old");
  let actor: string | null = "researcher";
  const trace: unknown[] = [];
  let pending: string[] = [];
  const startup = deferred<SessionStatus>();
  const client = {
    command: async (command: string) => { trace.push(command); return startup.promise; },
    describeSession: async () => active,
  } as unknown as WebGatewayClient;
  const queue = new WebComposerQueue({
    isBusy: () => busy,
    onChange: (items) => { pending = items; },
    execute: async (text) => {
      // The App dispatch uses this same global-command check before routing
      // ordinary child input to send_agent_message.
      if (isClearCommand(text)) {
        const command = await executeWebCommand(text, client, {
          baseDir: "/workspace", status: active, sessions: [], models: [],
        });
        assert.equal(command.newSession, true);
        await startWebSession(client, {
          baseDir: "/workspace", status: active, config: {}, modelName: "fallback", modelEffort: "low",
        }, { commit: (next) => { active = next; actor = null; }, reconcile: () => {} });
      } else {
        trace.push([active.runner_id, actor || "root", text]);
      }
      return "consumed";
    },
  });

  await queue.submit("/clear", true);
  await queue.submit("first follow-up", false);
  await queue.flush();
  assert.deepEqual(trace, []);
  assert.deepEqual(pending, ["/clear", "first follow-up"]);

  busy = false;
  const switching = queue.flush();
  await queue.submit("second follow-up", false);
  await queue.flush();
  assert.deepEqual(trace, ["start_session"]);
  startup.resolve(status("new"));
  await switching;
  await queue.flush();
  await queue.flush();
  assert.deepEqual(trace, ["start_session", ["new", "root", "first follow-up"], ["new", "root", "second follow-up"]]);
  assert.deepEqual(pending, []);
});

test("Web inbox messages still send during a round until a command establishes a queue", async () => {
  const sent: string[] = [];
  const queue = new WebComposerQueue({
    isBusy: () => true,
    execute: async (text) => { sent.push(text); return "consumed"; },
    onChange: () => {},
  });
  await queue.submit("inbox follow-up", false);
  await queue.submit("/clear", true);
  await queue.submit("next session", false);
  await queue.flush();
  assert.deepEqual(sent, ["inbox follow-up"]);
});

test("Web /clear waits for a cold first message's creation and first round", async () => {
  const initialCreation = deferred<SessionStatus>();
  const firstRoundStarted = deferred<void>();
  const firstRoundFinished = deferred<void>();
  let active: SessionStatus | null = null;
  let busy = false;
  let creations = 0;
  const trace: unknown[] = [];
  const client = {
    command: async (command: string) => {
      trace.push(command);
      return ++creations === 1 ? initialCreation.promise : status("after-clear");
    },
    describeSession: async () => active,
  } as unknown as WebGatewayClient;
  const create = async () => {
    const release = queue.hold();
    try {
      return await startWebSession(client, {
        baseDir: "/workspace", status: active, config: {}, modelName: "current-model", modelEffort: "high",
      }, { commit: (next) => { active = next; }, reconcile: (actual) => { active = actual; } });
    } finally {
      release();
    }
  };
  const queue = new WebComposerQueue({
    isBusy: () => busy,
    onChange: () => {},
    execute: async (text) => {
      if (isClearCommand(text)) {
        await create();
      } else {
        if (!active) await create();
        trace.push([active?.runner_id, text]);
        if (text === "first message") {
          busy = true;
          firstRoundStarted.resolve();
          await firstRoundFinished.promise;
          busy = false;
        }
      }
      return "consumed";
    },
  });

  const first = queue.submit("first message", false);
  await queue.submit("/clear", true);
  await queue.submit("follow-up", false);
  await queue.flush();
  assert.deepEqual(trace, ["start_session"]);

  initialCreation.resolve(status("first-session"));
  await firstRoundStarted.promise;
  await queue.flush();
  assert.deepEqual(trace, ["start_session", ["first-session", "first message"]]);

  firstRoundFinished.resolve();
  await first;
  await queue.flush();
  await queue.flush();
  assert.deepEqual(trace, [
    "start_session", ["first-session", "first message"], "start_session", ["after-clear", "follow-up"],
  ]);
});

test("Web ordinary messages still enter an active inbox while another ordinary request is pending", async () => {
  const firstRound = deferred<void>();
  const sent: string[] = [];
  const queue = new WebComposerQueue({
    isBusy: () => true,
    onChange: () => {},
    execute: async (text) => {
      sent.push(text);
      if (text === "first") await firstRound.promise;
      return "consumed";
    },
  });
  const first = queue.submit("first", false);
  await queue.submit("inbox follow-up", false);
  assert.deepEqual(sent, ["first", "inbox follow-up"]);
  firstRound.resolve();
  await first;
});

test("Web external session creation holds commands and input until the Runner is ready", async () => {
  const sent: string[] = [];
  const queue = new WebComposerQueue({
    isBusy: () => false,
    onChange: () => {},
    execute: async (text) => { sent.push(text); return "consumed"; },
  });
  const release = queue.hold();
  await queue.submit("/clear", true);
  await queue.submit("new session input", false);
  await queue.flush();
  assert.deepEqual(sent, []);
  release();
  release(); // Releasing twice cannot corrupt the barrier count.
  await queue.flush();
  await queue.flush();
  assert.deepEqual(sent, ["/clear", "new session input"]);
});

test("a failed Web /clear releases FIFO and later input cannot use a stale Runner", async () => {
  let active: SessionStatus | null = status("old");
  const messages = ["old transcript"];
  const trace: unknown[] = [];
  let attempts = 0;
  const client = {
    command: async (command: string) => {
      trace.push(command);
      if (++attempts === 1) throw new Error("startup failed after stopping old Runner");
      return status("recovered");
    },
    describeSession: async () => { throw new Error("no active session"); },
  } as unknown as WebGatewayClient;
  const create = () => startWebSession(client, {
    baseDir: "/workspace", status: active, config: {}, modelName: "current-model", modelEffort: "high",
  }, {
    commit: (next) => { active = next; },
    reconcile: (actual) => { active = actual; },
  });
  const queue = new WebComposerQueue({
    isBusy: () => false,
    onChange: () => {},
    execute: async (text) => {
      if (isClearCommand(text)) {
        try {
          await create();
          messages.length = 0;
        } catch (error) {
          // App reports command failures as consumed so follow-up input can
          // recover instead of getting stuck behind the failed /clear.
          messages.push((error as Error).message);
        }
      } else {
        if (!active?.runner_id) await create();
        trace.push([active?.runner_id, text]);
      }
      return "consumed";
    },
  });

  await queue.submit("/clear", true);
  await queue.submit("continue", false);
  await queue.flush();
  assert.equal(messages[0], "old transcript");
  assert.match(messages[1], /startup failed/);
  assert.equal(active, null);
  await queue.flush();
  assert.deepEqual(trace, ["start_session", "start_session", ["recovered", "continue"]]);
});

test("failed Web /clear in a child view cannot send the next input to an old Runner's actor", async () => {
  let active = status("old");
  const actorName = "researcher";
  const report = { runner_id: "old" };
  const transcript = ["child transcript"];
  const client = {
    command: async () => { throw new Error("creation failed"); },
    describeSession: async () => status("different-live-runner"),
  } as unknown as WebGatewayClient;
  assert.equal(webActorMessageTarget("hello", actorName, report.runner_id, active.runner_id), actorName);
  await assert.rejects(startWebSession(client, {
    baseDir: "/workspace", status: active, config: {}, modelName: "current-model", modelEffort: "high",
  }, {
    commit: () => { transcript.length = 0; },
    reconcile: (actual) => { active = actual!; },
  }), /creation failed/);

  assert.deepEqual(transcript, ["child transcript"]);
  assert.equal(active.runner_id, "different-live-runner");
  assert.equal(webActorMessageTarget("follow-up", actorName, report.runner_id, active.runner_id), null);
  assert.equal(webActorMessageTarget("/clear", actorName, active.runner_id, active.runner_id), null);
});

for (const recovery of ["same", "different", "empty", "missing", "unavailable"] as const) {
  test(`failed Web /clear preserves transcript and reconciles ${recovery} gateway state`, async () => {
    const messages = ["old transcript"];
    let active: SessionStatus | null = status("old");
    const actual = recovery === "different" ? status("actual") : status(recovery === "missing" ? "" : "old", {
      started: recovery !== "empty",
    });
    const originalError = new Error("startup failed");
    const client = {
      command: async () => { throw originalError; },
      describeSession: async () => {
        if (recovery === "unavailable") throw new Error("disconnected");
        return actual;
      },
    } as unknown as WebGatewayClient;
    await assert.rejects(startWebSession(client, {
      baseDir: "/workspace", status: active, config: {}, modelName: "fallback", modelEffort: "disabled",
    }, {
      commit: () => { messages.length = 0; },
      reconcile: (next) => { active = next; },
    }), (error) => error === originalError);
    assert.deepEqual(messages, ["old transcript"]);
    assert.equal(active?.runner_id ?? "", recovery === "same" || recovery === "empty" ? "old" : recovery === "different" ? "actual" : "");
  });
}

test("Web recognizes only the complete /clear command in a child view", () => {
  assert.equal(isClearCommand("  /clear  "), true);
  assert.equal(isClearCommand("/clear extra"), true);
  assert.equal(isClearCommand("/clearer"), false);
  assert.equal(isClearCommand("ask /clear"), false);
});
