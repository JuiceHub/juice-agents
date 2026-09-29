import test from "node:test";
import assert from "node:assert/strict";
import { WebGatewayClient } from "./client.js";

test("browser asset urls are resolved against the web gateway", () => {
  const client = new WebGatewayClient("http://127.0.0.1:8003");
  assert.equal(
    client.browserAssetUrl("/api/browser/asset?path=x"),
    "http://127.0.0.1:8003/api/browser/asset?path=x"
  );
  assert.equal(
    client.browserLiveSocketUrl("/tmp/work", "run-123"),
    "ws://127.0.0.1:8003/ws/browser/live?base_dir=%2Ftmp%2Fwork&runner_id=run-123"
  );
  assert.equal(
    client.fileAssetUrl("/api/files/asset?path=x"),
    "http://127.0.0.1:8003/api/files/asset?path=x"
  );
  assert.equal(
    client.browserExternalSocketUrl("/tmp/work", "run-123"),
    "ws://127.0.0.1:8003/ws/browser/live?base_dir=%2Ftmp%2Fwork&stream=0&runner_id=run-123"
  );
});

test("saveWorkspaceConfig persists a runtime patch without a websocket mode switch", async () => {
  const requests: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    requests.push(`${init?.method || "GET"} ${String(input)} ${init?.body || ""}`);
    return new Response(JSON.stringify({ saved: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

  try {
    const client = new WebGatewayClient("http://127.0.0.1:8003");
    await client.saveWorkspaceConfig("/tmp/work", { runtime: { agent_type: "codeact" } });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(requests, [
    'PUT http://127.0.0.1:8003/api/workspace-config {"base_dir":"/tmp/work","config":{"runtime":{"agent_type":"codeact"}}}',
  ]);
});

test("live browser preview can be scoped to a runner id", async () => {
  const paths: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    paths.push(String(input));
    return new Response(JSON.stringify({ connected: false, title: "", url: "", image_url: "" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

  try {
    const client = new WebGatewayClient("http://127.0.0.1:8003");
    await client.browserLivePreview("/tmp/work", "run-123");
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(paths[0], "http://127.0.0.1:8003/api/browser/live/preview?base_dir=%2Ftmp%2Fwork&runner_id=run-123");
});

test("live browser HTTP API exposes preview, new real tabs, and open external", async () => {
  const paths: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    paths.push(`${init?.method || "GET"} ${String(input)} ${init?.body || ""}`);
    return new Response(JSON.stringify({ connected: true, title: "Baidu", url: "https://www.baidu.com", image_url: "/asset.png" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

  try {
    const client = new WebGatewayClient("http://127.0.0.1:8003");
    await client.browserLivePreview("/tmp/work", "run-123");
    await client.browserLiveNewTab("/tmp/work", "run-123", "https://docs.example", true);
    await client.browserLiveOpenExternal("/tmp/work", "run-123", "https://example.com");
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.match(paths[0], /GET http:\/\/127\.0\.0\.1:8003\/api\/browser\/live\/preview\?base_dir=%2Ftmp%2Fwork&runner_id=run-123/);
  assert.match(paths[1], /POST http:\/\/127\.0\.0\.1:8003\/api\/browser\/live\/new-tab/);
  assert.match(paths[1], /"runner_id":"run-123"/);
  assert.match(paths[1], /"url":"https:\/\/docs.example"/);
  assert.match(paths[1], /"make_active":true/);
  assert.match(paths[2], /POST http:\/\/127\.0\.0\.1:8003\/api\/browser\/live\/open-external/);
  assert.match(paths[2], /"url":"https:\/\/example.com"/);
  assert.equal(paths.length, 3);
});

test("gateway client surfaces structured HTTP error details", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ detail: "无法打开外部可见 Chromium：当前 gateway 进程没有 DISPLAY/WAYLAND_DISPLAY 图形环境。" }), {
      status: 409,
      statusText: "Conflict",
      headers: { "content-type": "application/json" },
    })) as typeof fetch;

  try {
    const client = new WebGatewayClient("http://127.0.0.1:8003");
    await assert.rejects(
      client.browserLiveOpenExternal("/tmp/work", "run-123", "https://example.com"),
      /DISPLAY\/WAYLAND_DISPLAY/
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("describeActorSessions sends the gateway command", async () => {
  const sent: string[] = [];
  class FakeSocket {
    readyState = WebSocket.OPEN;
    send(raw: string) {
      sent.push(raw);
    }
  }

  const client = new WebGatewayClient("http://127.0.0.1:8003");
  Object.defineProperty(client, "socket", { value: new FakeSocket() });
  void client.describeActorSessions();

  assert.match(sent[0], /"type":"describe_actor_sessions"/);
});

test("actor interaction helpers send gateway commands", async () => {
  const sent: string[] = [];
  class FakeSocket {
    readyState = WebSocket.OPEN;
    send(raw: string) {
      sent.push(raw);
    }
  }

  const client = new WebGatewayClient("http://127.0.0.1:8003");
  Object.defineProperty(client, "socket", { value: new FakeSocket() });
  void client.sendActorMessage({ actor_name: "researcher", message: "focus tests" });
  void client.interruptActor({ actor_name: "researcher" });

  assert.match(sent[0], /"type":"send_actor_message"/);
  assert.match(sent[0], /"actor_name":"researcher"/);
  assert.match(sent[0], /"message":"focus tests"/);
  assert.match(sent[1], /"type":"interrupt_actor"/);
  assert.match(sent[1], /"actor_name":"researcher"/);
});

test("listSkills sends the gateway command", async () => {
  const sent: string[] = [];
  class FakeSocket {
    readyState = WebSocket.OPEN;
    send(raw: string) {
      sent.push(raw);
    }
  }

  const client = new WebGatewayClient("http://127.0.0.1:8003");
  Object.defineProperty(client, "socket", { value: new FakeSocket() });
  void client.listSkills({ category: "docs" });

  assert.match(sent[0], /"type":"list_skills"/);
  assert.match(sent[0], /"category":"docs"/);
});

test("plugin query commands are sent through the gateway", async () => {
  const sent: string[] = [];
  class FakeSocket {
    readyState = WebSocket.OPEN;
    send(raw: string) {
      sent.push(raw);
    }
  }

  const client = new WebGatewayClient("http://127.0.0.1:8003");
  Object.defineProperty(client, "socket", { value: new FakeSocket() });
  void client.listPlugins();
  void client.viewPlugin("review-pack", "skills/review/SKILL.md");

  assert.match(sent[0], /"type":"list_plugins"/);
  assert.match(sent[1], /"type":"view_plugin"/);
  assert.match(sent[1], /"file_path":"skills\/review\/SKILL.md"/);
});

test("listAvailableAgents sends the gateway command", async () => {
  const sent: string[] = [];
  class FakeSocket {
    readyState = WebSocket.OPEN;
    send(raw: string) {
      sent.push(raw);
    }
  }

  const client = new WebGatewayClient("http://127.0.0.1:8003");
  Object.defineProperty(client, "socket", { value: new FakeSocket() });
  void client.listAvailableAgents({ name: "general", mode_id: "agent" });

  assert.match(sent[0], /"type":"list_available_agents"/);
  assert.match(sent[0], /"name":"general"/);
  assert.match(sent[0], /"mode_id":"agent"/);
});

test("Team manifest queries send read-only gateway commands", async () => {
  const sent: string[] = [];
  class FakeSocket {
    readyState = WebSocket.OPEN;
    send(raw: string) {
      sent.push(raw);
    }
  }

  const client = new WebGatewayClient("http://127.0.0.1:8003");
  Object.defineProperty(client, "socket", { value: new FakeSocket() });
  void client.listTeams({ base_dir: "/tmp/work" });
  void client.getTeam({ team_name: "default", base_dir: "/tmp/work" });

  assert.match(sent[0], /"type":"list_team_configs"/);
  assert.match(sent[0], /"base_dir":"\/tmp\/work"/);
  assert.match(sent[1], /"type":"get_team_config"/);
  assert.match(sent[1], /"team_name":"default"/);
});

test("skills config commands are sent through the gateway", async () => {
  const sent: string[] = [];
  class FakeSocket {
    readyState = WebSocket.OPEN;
    send(raw: string) {
      sent.push(raw);
    }
  }

  const client = new WebGatewayClient("http://127.0.0.1:8003");
  Object.defineProperty(client, "socket", { value: new FakeSocket() });
  void client.skillsConfigStatus();
  void client.setSkillsConfig({ enabled: false, disabled: ["joke-expert"] });
  void client.setImageConfig({ enabled: true });

  assert.match(sent[0], /"type":"skills_config_status"/);
  assert.match(sent[1], /"type":"set_skills_config"/);
  assert.match(sent[1], /"enabled":false/);
  assert.match(sent[1], /"joke-expert"/);
  assert.match(sent[2], /"type":"set_image_config"/);
  assert.match(sent[2], /"enabled":true/);
});

test("slash command helpers send cli-aligned gateway commands", async () => {
  const sent: string[] = [];
  class FakeSocket {
    readyState = WebSocket.OPEN;
    send(raw: string) {
      sent.push(raw);
    }
  }

  const client = new WebGatewayClient("http://127.0.0.1:8003");
  Object.defineProperty(client, "socket", { value: new FakeSocket() });
  void client.listModels("/tmp/work");
  void client.viewSkill("code-review", "README.md");
  void client.memorySearch("runner");
  void client.runDream();
  void client.setGoal("finish slash commands");
  void client.createCronTask("/tmp/work", "*/5 * * * *", "check deploy", true);

  assert.match(sent[0], /"type":"list_models"/);
  assert.match(sent[1], /"type":"view_skill"/);
  assert.match(sent[1], /"file_path":"README.md"/);
  assert.match(sent[2], /"type":"memory_search"/);
  assert.match(sent[3], /"type":"run_dream"/);
  assert.match(sent[4], /"type":"set_goal"/);
  assert.match(sent[5], /"type":"create_cron_task"/);
});

test("runner archive and restore commands are sent through the gateway", async () => {
  const sent: string[] = [];
  class FakeSocket {
    readyState = WebSocket.OPEN;
    send(raw: string) {
      sent.push(raw);
    }
  }

  const client = new WebGatewayClient("http://127.0.0.1:8003");
  Object.defineProperty(client, "socket", { value: new FakeSocket() });
  void client.archiveRunner("/tmp/work", "run-123");
  void client.listArchivedSessions("/tmp/work");
  void client.restoreRunner("/tmp/work", "run-123");
  void client.stopSession();

  assert.match(sent[0], /"type":"archive_runner"/);
  assert.match(sent[0], /"runner_id":"run-123"/);
  assert.match(sent[1], /"type":"list_archived_sessions"/);
  assert.match(sent[2], /"type":"restore_runner"/);
  assert.match(sent[3], /"type":"stop_session"/);
});

test("streamMessage routes envelopes only to their matching request", async () => {
  const sent: any[] = [];
  const socket = {
    readyState: WebSocket.OPEN,
    send: (raw: string) => sent.push(JSON.parse(raw)),
  };
  const client = new WebGatewayClient();
  Object.defineProperty(client, "socket", { value: socket, writable: true });
  const received: any[] = [];

  const pending = client.streamMessage("hello", (event) => received.push(event));
  assert.equal(sent[0].id, "1");
  const cancel = client.cancelStream();
  assert.equal(sent[1].payload.request_id, "1");
  (client as any).handleMessage(JSON.stringify({
    id: "unknown",
    type: "stream_step",
    payload: { kind: "action_step" },
  }));
  (client as any).handleMessage(JSON.stringify({
    id: "1",
    type: "stream_step",
    payload: { kind: "action_step" },
  }));
  (client as any).handleMessage(JSON.stringify({
    id: "1",
    type: "result",
    payload: { done: true },
  }));
  (client as any).handleMessage(JSON.stringify({
    id: "2",
    type: "result",
    payload: { cancelled: true },
  }));

  await Promise.all([pending, cancel]);
  assert.equal(received.length, 1);
});

test("empty-id stream events use ambient handler", () => {
  const client = new WebGatewayClient();
  const received: any[] = [];
  client.setAmbientStreamHandler((event) => received.push(event));
  (client as any).handleMessage(JSON.stringify({
    id: "",
    type: "stream_step",
    payload: { kind: "action_step" },
  }));
  assert.equal(received.length, 1);
});

test("disconnect rejects every pending command and clears handlers", async () => {
  const client = new WebGatewayClient();
  const socket = {
    readyState: WebSocket.OPEN,
    send: () => {},
  };
  Object.defineProperty(client, "socket", { value: socket, writable: true });
  const one = client.command("describe_session");
  const two = client.streamMessage("hello", () => {});

  (client as any).handleDisconnect(new Error("socket closed"));

  await assert.rejects(one, /socket closed/);
  await assert.rejects(two, /socket closed/);
  assert.equal((client as any).pending.size, 0);
  assert.equal((client as any).streamHandlers.size, 0);
});

test("ask envelopes route only to their originating stream request", () => {
  const client = new WebGatewayClient();
  const first: any[] = [];
  const second: any[] = [];
  (client as any).askHandlers.set("1", (request: any) => first.push(request));
  (client as any).askHandlers.set("2", (request: any) => second.push(request));

  (client as any).handleMessage(JSON.stringify({
    id: "2",
    type: "ask_request",
    payload: { request_id: "ask-2", question: "continue?" },
  }));
  (client as any).handleMessage(JSON.stringify({
    id: "unknown",
    type: "ask_request",
    payload: { request_id: "late", question: "late" },
  }));

  assert.equal(first.length, 0);
  assert.equal(second.length, 1);
});

test("socket close before open rejects connect instead of hanging", async () => {
  const OriginalWebSocket = globalThis.WebSocket;
  class ClosingSocket {
    static OPEN = 1;
    readyState = 0;
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    constructor() {
      queueMicrotask(() => this.onclose?.());
    }
  }
  globalThis.WebSocket = ClosingSocket as unknown as typeof WebSocket;
  try {
    const client = new WebGatewayClient();
    const result = await Promise.race([
      client.connect().then(() => "connected", (error) => String(error)),
      new Promise<string>((resolve) => setTimeout(() => resolve("timeout"), 50)),
    ]);
    assert.match(result, /socket closed/);
  } finally {
    globalThis.WebSocket = OriginalWebSocket;
  }
});
