import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { delimiter, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { GatewayClient } from "../../frontend/cli/src/gateway/client.js";
import { conversationReducer, createConversationState } from "../../frontend/shared/src/conversation/state.js";
import type { AskRequest, RunnerStreamEvent } from "../../frontend/shared/src/gateway/types.js";

/** Exercise the real Python JSON writer, including its omission of request_id.
 * Constructing the server opens neither a Runner nor a model; stdout is the
 * same newline-delimited transport consumed by the CLI's readline handler.
 */
function backendFrames(): string[] {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const output = execFileSync(process.env.JUICE_PYTHON_PATH || "python", ["-c", `
from adapters.stdio_gateway.stdio_server import StdioJsonRpcServer
from juice_agents.core.agent.contracts import RoundResult

server = StdioJsonRpcServer()
for agent in ("root", "worker"):
    is_root = agent == "root"
    attachment = {
        "message_id": f"{agent}-message",
        "attachment_type": "async_task_notification" if is_root else "agent_user_message",
        "payload": ({"async_task_id": "background-task", "status": "completed", "summary": "result ready"}
                    if is_root else {"text": "child instruction"}),
    }
    events = [
        ("message_received", {"attachments": [attachment]}),
        ("round_start", {"input": {"task": "" if is_root else "child task", "attachments": [attachment] if is_root else []}}),
        ("round_end", {"result": RoundResult(f"{agent}-round", "completed", output=f"{agent} result")}),
    ]
    for kind, data in events:
        server._publish_runner_event({
            "kind": kind, "data": data, "event_id": f"{agent}-{kind}", "timestamp": 1,
            "runner_id": "wire-runner", "agent_id": agent, "agent_name": agent,
            "agent_role": "root" if is_root else "subagent", "round_id": f"{agent}-round",
        })

ask = {"request_id": "autonomous-ask", "question": "Continue?", "options": [],
       "multiple": False, "allow_custom": True}
server._write_notification(ask, method="ask_request", request_id=None)
# Keep the same writer for scoped notifications: late RPC frames must never
# reach ambient subscribers, while a matching active request still receives it.
scoped_event = {"kind": "thinking", "data": {"thought": "scoped result"},
                "event_id": "scoped-event", "timestamp": 1, "runner_id": "wire-runner",
                "agent_id": "root", "agent_name": "root", "agent_role": "root"}
for request_id in (71, 99):
    server._write_notification(scoped_event, request_id=request_id)
    server._write_notification(ask, method="ask_request", request_id=request_id)
`], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: [join(root, "sdk/src"), root, process.env.PYTHONPATH].filter(Boolean).join(delimiter) },
  });
  return output.trim().split("\n");
}

// Access only the transport boundary; no child process or fabricated backend
// notification is needed to reproduce how each real JSON line is dispatched.
type TransportInternals = {
  handleResponse(line: string): void;
  streamHandlers: Map<number, (event: RunnerStreamEvent) => void>;
  askHandlers: Map<number, (request: AskRequest) => void>;
};
const frames = backendFrames();
const unscoped = frames.filter((line) => !Object.hasOwn(JSON.parse(line).params, "request_id"));
const scoped = frames.filter((line) => Object.hasOwn(JSON.parse(line).params, "request_id"));

test("real unscoped Python notifications populate the root and child CLI transcripts", () => {
  const client = new GatewayClient();
  const transport = client as unknown as TransportInternals;
  let state = createConversationState();
  let scopedCalls = 0;
  transport.streamHandlers.set(71, () => { scopedCalls += 1; });
  client.setAmbientStreamHandler((event) => {
    state = conversationReducer(state, { type: "ambient_event", event });
  });

  const streamFrames = unscoped.filter((line) => JSON.parse(line).method === "stream_step");
  assert.equal(streamFrames.length, 6);
  for (const line of streamFrames) {
    const { event } = JSON.parse(line).params;
    transport.handleResponse(line);
    if (event.agent_id === "root" && event.kind === "round_start") assert.equal(state.streaming, true);
  }

  assert.equal(scopedCalls, 0, "unscoped events remain ambient even during an active RPC");
  assert.equal(state.streaming, false);
  assert.equal(state.streamInFlight, false);
  assert.equal(state.messages.filter((block) => block.text === "background-task: completed · result ready").length, 1);
  assert.deepEqual(state.messages.filter((block) => block.kind === "final").map((block) => block.text), ["root result"]);
  assert.deepEqual(state.agentMessages.worker.filter((block) => block.kind === "user").map((block) => block.text), ["child instruction", "child task"]);
  assert.deepEqual(state.agentMessages.worker.filter((block) => block.kind === "final").map((block) => block.text), ["worker result"]);
  assert.ok(state.agentReport?.agents.some((agent) => agent.agent_name === "worker"), "the child transcript is selectable");
  assert.equal(state.messages.some((block) => block.text.includes("child") || block.text === "worker result"), false);
  assert.deepEqual(state.seenEventIds.sort(), streamFrames.map((line) => JSON.parse(line).params.event.event_id).sort());
});

test("real unscoped Python ask notifications reach the ambient CLI handler", () => {
  const client = new GatewayClient();
  const transport = client as unknown as TransportInternals;
  const received: AskRequest[] = [];
  let scopedCalls = 0;
  transport.askHandlers.set(71, () => { scopedCalls += 1; });
  client.setAmbientAskHandler((request) => received.push(request));
  const askFrames = unscoped.filter((line) => JSON.parse(line).method === "ask_request");
  assert.equal(askFrames.length, 1);
  for (const line of askFrames) transport.handleResponse(line);
  assert.equal(scopedCalls, 0);
  assert.deepEqual(received, askFrames.map((line) => JSON.parse(line).params.request));
  assert.equal(received[0].request_id, "autonomous-ask", "the Ask identity is separate from an RPC request ID");
});

test("real scoped Python notifications reach their matching RPC and drop unknown IDs", () => {
  const client = new GatewayClient();
  const transport = client as unknown as TransportInternals;
  const streams: RunnerStreamEvent[] = [];
  const asks: AskRequest[] = [];
  let ambientCalls = 0;
  transport.streamHandlers.set(71, (event) => streams.push(event));
  transport.askHandlers.set(71, (request) => asks.push(request));
  client.setAmbientStreamHandler(() => { ambientCalls += 1; });
  client.setAmbientAskHandler(() => { ambientCalls += 1; });
  assert.equal(scoped.length, 4);
  for (const line of scoped) transport.handleResponse(line);
  const expected = scoped.map((line) => JSON.parse(line)).filter((frame) => frame.params.request_id === 71);
  assert.deepEqual(streams, expected.filter((frame) => frame.method === "stream_step").map((frame) => frame.params.event));
  assert.deepEqual(asks, expected.filter((frame) => frame.method === "ask_request").map((frame) => frame.params.request));
  assert.equal(streams.length, 1);
  assert.equal(asks.length, 1);
  assert.equal(ambientCalls, 0, "unknown RPC IDs cannot leak into the current conversation");
});
