import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

test("GatewayClient starts the promoted stdio gateway module", () => {
  const sourcePath = fileURLToPath(new URL("./client.ts", import.meta.url));
  const source = readFileSync(sourcePath, "utf-8");

  assert.match(
    source,
    /adapters\.stdio_gateway\.entry/,
    "GatewayClient 应启动 adapters.stdio_gateway.entry",
  );
});

test("GatewayClient exposes ask request notifications and answer_ask RPC", () => {
  const sourcePath = fileURLToPath(new URL("./client.ts", import.meta.url));
  const source = readFileSync(sourcePath, "utf-8");

  assert.match(source, /ask_request/);
  assert.match(source, /answer_ask/);
  assert.match(source, /onAskRequest/);
});

test("GatewayClient exposes memory and dream RPC helpers", () => {
  const sourcePath = fileURLToPath(new URL("./client.ts", import.meta.url));
  const source = readFileSync(sourcePath, "utf-8");

  assert.match(source, /memory_status/);
  assert.match(source, /memory_search/);
  assert.match(source, /memory_view/);
  assert.match(source, /run_dream/);
  assert.match(source, /set_memory_config/);
});

test("GatewayClient exposes browser config RPC helper", () => {
  const sourcePath = fileURLToPath(new URL("./client.ts", import.meta.url));
  const source = readFileSync(sourcePath, "utf-8");

  assert.match(source, /setBrowserConfig/);
  assert.match(source, /set_browser_config/);
});

test("GatewayClient exposes image config RPC helper", () => {
  const sourcePath = fileURLToPath(new URL("./client.ts", import.meta.url));
  const source = readFileSync(sourcePath, "utf-8");

  assert.match(source, /setImageConfig/);
  assert.match(source, /set_image_config/);
});

test("GatewayClient exposes actor interaction RPC helpers", () => {
  const sourcePath = fileURLToPath(new URL("./client.ts", import.meta.url));
  const source = readFileSync(sourcePath, "utf-8");

  assert.match(source, /sendActorMessage/);
  assert.match(source, /send_actor_message/);
  assert.match(source, /interruptActor/);
  assert.match(source, /interrupt_actor/);
});

test("streamMessage uses the same RPC id for the request and its handlers", async () => {
  const { GatewayClient } = await import("./client.js");
  const client = new GatewayClient();
  const sent: any[] = [];
  (client as any).process = {
    stdin: { write: (raw: string) => sent.push(JSON.parse(raw)) },
  };
  const received: any[] = [];

  const promise = client.streamMessage("hello", (event) => received.push(event));
  assert.equal(sent[0].id, 1);
  const cancel = client.cancelStream();
  assert.equal(sent[1].params.request_id, 1);
  (client as any).handleResponse(JSON.stringify({
    jsonrpc: "2.0",
    id: 2,
    result: { cancelled: true },
  }));
  (client as any).handleResponse(JSON.stringify({
    jsonrpc: "2.0",
    method: "stream_step",
    params: { request_id: 1, event: { kind: "action_step" } },
  }));
  (client as any).handleResponse(JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    result: { done: true },
  }));

  await Promise.all([promise, cancel]);
  assert.equal(received.length, 1);
});

test("stream_step falls back to ambient handler only for empty request id", async () => {
  const { GatewayClient } = await import("./client.js");
  const client = new GatewayClient();

  const received: any[] = [];
  client.setAmbientStreamHandler((event) => received.push(event));

  // 无活跃 streamMessage（streamHandlers 为空）时，无 id 的 stream_step 应回落到 ambient。
  // handleResponse 是私有方法，测试通过类型断言直接驱动其行为。
  (client as any).handleResponse(
    JSON.stringify({
      jsonrpc: "2.0",
      method: "stream_step",
      params: { request_id: null, event: { kind: "action_step", step: { step_num: 1 } } },
    }),
  );

  assert.equal(received.length, 1, "ambient handler 应接收到自动续跑推送的 stream_step");
  assert.equal(received[0].kind, "action_step");
});

test("stream_step routes only to the matching request and drops unknown ids", async () => {
  const { GatewayClient } = await import("./client.js");
  const client = new GatewayClient();

  const ambient: any[] = [];
  const first: any[] = [];
  const second: any[] = [];
  client.setAmbientStreamHandler((event) => ambient.push(event));
  (client as any).streamHandlers.set(1, (event: any) => first.push(event));
  (client as any).streamHandlers.set(2, (event: any) => second.push(event));

  (client as any).handleResponse(
    JSON.stringify({
      jsonrpc: "2.0",
      method: "stream_step",
      params: { request_id: 2, event: { kind: "action_step" } },
    }),
  );
  (client as any).handleResponse(JSON.stringify({
    jsonrpc: "2.0",
    method: "stream_step",
    params: { request_id: 999, event: { kind: "action_step" } },
  }));

  assert.equal(first.length, 0);
  assert.equal(second.length, 1);
  assert.equal(ambient.length, 0);
});

test("ask_request routes by originating stream request id", async () => {
  const { GatewayClient } = await import("./client.js");
  const client = new GatewayClient();
  const first: any[] = [];
  const second: any[] = [];
  (client as any).askHandlers.set(1, (request: any) => first.push(request));
  (client as any).askHandlers.set(2, (request: any) => second.push(request));

  (client as any).handleResponse(JSON.stringify({
    jsonrpc: "2.0",
    method: "ask_request",
    params: { request_id: 1, request: { request_id: "ask-1", question: "continue?" } },
  }));

  assert.equal(first.length, 1);
  assert.equal(second.length, 0);
});
