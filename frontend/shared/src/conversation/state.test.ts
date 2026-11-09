import assert from "node:assert/strict";
import test from "node:test";
import { createConversationState, conversationReducer } from "./state.js";

test("a cancellation request keeps execution and transport tokens until real termination", () => {
  let state = conversationReducer(createConversationState(), { type: "stream_started", generation: 1, token: "old", message: "first" });
  state = conversationReducer(state, { type: "cancel_requested", roundId: "pending" });
  assert.equal(state.streaming, true);
  assert.equal(state.stopping, true);
  assert.equal(state.activeGeneration, 1);
  assert.equal(state.messages.length, 1);
  state = conversationReducer(state, { type: "cancel_rejected", roundId: "pending", message: "not accepted" });
  assert.equal(state.stopping, false);
  assert.equal(state.streaming, true);
});

test("settling an old request cannot clear a newer generation or its token", () => {
  let state = conversationReducer(createConversationState(), { type: "stream_started", generation: 1, token: "old", message: "first" });
  state = conversationReducer(state, { type: "stream_started", generation: 2, token: "new", message: "second" });
  state = conversationReducer(state, { type: "stream_settled", generation: 1, token: "old" });
  assert.equal(state.streaming, true);
  assert.equal(state.streamInFlight, true);
  assert.equal(state.activeGeneration, 2);
  assert.deepEqual(state.inFlightTokens, ["new"]);
  state = conversationReducer(state, { type: "stream_settled", generation: 2, token: "new" });
  assert.equal(state.streamInFlight, false);
});

test("runner replacement isolates old requests and preserves visible messages", () => {
  let state = conversationReducer(createConversationState(), { type: "stream_started", generation: 1, token: "old", message: "first" });
  state = conversationReducer(state, { type: "scope_changed", runnerId: "new-runner" });
  state = conversationReducer(state, { type: "stream_event", generation: 1, blocks: [{ kind: "assistant", text: "late" }] });
  assert.deepEqual(state.messages.map((message) => message.text), ["first"]);
  assert.equal(state.streaming, false);
});

test("message actions add, replace, remove, and clear transcript blocks", () => {
  let state = conversationReducer(createConversationState(), { type: "messages_added", messages: [
    { id: "one", kind: "system", text: "one" }, { id: "two", kind: "system", text: "two" },
  ] });
  state = conversationReducer(state, { type: "message_removed", id: "one" });
  assert.deepEqual(state.messages.map((message) => message.text), ["two"]);
  state = conversationReducer(state, { type: "messages_replaced", messages: [{ kind: "assistant", text: "replacement" }] });
  assert.deepEqual(state.messages.map((message) => message.text), ["replacement"]);
  assert.deepEqual(conversationReducer(state, { type: "messages_cleared" }).messages, []);
});

test("transport errors preserve running state and remain visible", () => {
  let state = conversationReducer(createConversationState(), { type: "stream_started", generation: 2, token: "new", message: "new" });
  state = conversationReducer(state, { type: "transport_failed", message: "connection lost" });
  assert.equal(state.streaming, true);
  assert.equal(state.streamFailed, true);
  assert.equal(state.messages.at(-1)?.text, "connection lost");
  state = conversationReducer(state, { type: "stream_started", generation: 3, token: "retry", message: "retry" });
  assert.equal(state.streamFailed, false);
});
