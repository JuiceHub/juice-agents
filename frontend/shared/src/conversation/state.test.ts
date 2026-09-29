import assert from "node:assert/strict";
import test from "node:test";

import {
  createConversationState,
  conversationReducer,
} from "./state.js";

test("cancel invalidates the active generation immediately and adds one marker", () => {
  let state = createConversationState();
  state = conversationReducer(state, {
    type: "stream_started",
    generation: 1,
    token: "old",
    message: "first",
  });

  state = conversationReducer(state, { type: "stream_cancelled", generation: 1 });
  state = conversationReducer(state, { type: "stream_cancelled", generation: 1 });

  assert.equal(state.streaming, false);
  assert.equal(state.activeGeneration, null);
  assert.equal(state.lifecycleEvent, null);
  assert.deepEqual(
    state.messages.filter((message) => message.text === "[Request interrupted by user]"),
    [{ kind: "system", text: "[Request interrupted by user]" }],
  );
});

test("settling an old request cannot clear a newer generation", () => {
  let state = createConversationState();
  state = conversationReducer(state, {
    type: "stream_started",
    generation: 1,
    token: "old",
    message: "first",
  });
  state = conversationReducer(state, { type: "stream_cancelled", generation: 1 });
  state = conversationReducer(state, {
    type: "stream_started",
    generation: 3,
    token: "new",
    message: "second",
  });

  state = conversationReducer(state, {
    type: "stream_settled",
    generation: 1,
    token: "old",
  });

  assert.equal(state.streaming, true);
  assert.equal(state.streamInFlight, true);
  assert.equal(state.activeGeneration, 3);
  assert.deepEqual(state.inFlightTokens, ["new"]);
});

test("in-flight state remains true until every transport token settles", () => {
  let state = createConversationState();
  state = conversationReducer(state, {
    type: "stream_started",
    generation: 1,
    token: "one",
    message: "one",
  });
  state = conversationReducer(state, {
    type: "stream_started",
    generation: 2,
    token: "two",
    message: "two",
  });
  state = conversationReducer(state, {
    type: "stream_settled",
    generation: 1,
    token: "one",
  });

  assert.equal(state.streamInFlight, true);
  assert.deepEqual(state.inFlightTokens, ["two"]);

  state = conversationReducer(state, {
    type: "stream_settled",
    generation: 2,
    token: "two",
  });
  assert.equal(state.streamInFlight, false);
  assert.equal(state.streaming, false);
});

test("events from a superseded generation are discarded", () => {
  let state = createConversationState();
  state = conversationReducer(state, {
    type: "stream_started",
    generation: 1,
    token: "old",
    message: "first",
  });
  state = conversationReducer(state, { type: "stream_cancelled", generation: 1 });
  state = conversationReducer(state, {
    type: "stream_event",
    generation: 1,
    blocks: [{ kind: "assistant", text: "late" }],
    lifecycleEvent: null,
  });

  assert.equal(state.messages.some((message) => message.text === "late"), false);
});

test("a stale cancellation cannot clear a newer active generation", () => {
  let state = createConversationState();
  state = conversationReducer(state, {
    type: "stream_started",
    generation: 3,
    token: "new",
    message: "new",
  });
  state = conversationReducer(state, { type: "stream_cancelled", generation: 1 });

  assert.equal(state.streaming, true);
  assert.equal(state.activeGeneration, 3);
  assert.equal(state.messages.at(-1)?.text, "new");
});

test("message actions add, replace, remove, and clear transcript blocks", () => {
  let state = createConversationState();
  state = conversationReducer(state, {
    type: "messages_added",
    messages: [
      { id: "one", kind: "system", text: "one" },
      { id: "two", kind: "system", text: "two" },
    ],
  });
  state = conversationReducer(state, { type: "message_removed", id: "one" });
  assert.deepEqual(state.messages.map((message) => message.text), ["two"]);

  state = conversationReducer(state, {
    type: "messages_replaced",
    messages: [{ kind: "assistant", text: "replacement" }],
  });
  assert.deepEqual(state.messages.map((message) => message.text), ["replacement"]);

  state = conversationReducer(state, { type: "messages_cleared" });
  assert.deepEqual(state.messages, []);
});

test("transport failures are visible without changing a newer active stream", () => {
  let state = createConversationState();
  state = conversationReducer(state, {
    type: "stream_started",
    generation: 2,
    token: "new",
    message: "new",
  });
  state = conversationReducer(state, {
    type: "transport_failed",
    message: "cancel failed",
  });

  assert.equal(state.streaming, true);
  assert.equal(state.activeGeneration, 2);
  assert.equal(state.messages.at(-1)?.text, "cancel failed");
  assert.equal(state.streamFailed, true);
});

test("a new stream clears the queue failure guard", () => {
  let state = conversationReducer(createConversationState(), {
    type: "transport_failed",
    message: "disconnected",
  });
  state = conversationReducer(state, {
    type: "stream_started",
    generation: 4,
    token: "retry",
    message: "retry",
  });
  assert.equal(state.streamFailed, false);
});

test("ambient events update lifecycle without requiring an active generation", () => {
  const state = conversationReducer(createConversationState(), {
    type: "ambient_event",
    blocks: [{ kind: "system", text: "background finished" }],
    lifecycleEvent: { scope: "runner", event: "resumed" },
  });
  assert.equal(state.messages.at(-1)?.text, "background finished");
  assert.equal(state.lifecycleEvent?.event, "resumed");
});
