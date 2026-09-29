import assert from "node:assert/strict";
import test from "node:test";

import {
  getCommandExecutionPolicy,
  getCommandToken,
  shouldDeferModeSwitch,
  shouldQueueSubmission,
} from "./commandScheduling.js";

test("safe command tokens bypass a busy stream and existing ordinary FIFO", () => {
  for (const input of [
    "/tasks",
    "/tasks ",
    "/actors",
    "/permissions",
    "/permissions accept",
    "/help",
    "/status",
    "/clear",
    "/exit",
  ]) {
    assert.equal(getCommandExecutionPolicy(input), "immediate", input);
    assert.equal(
      shouldQueueSubmission({
        input,
        streaming: true,
        queuedCount: 2,
      }),
      false,
      input,
    );
  }
});

test("ordinary messages, reconfiguration, and execution commands stay FIFO", () => {
  for (const input of [
    "explain the runner",
    "/team inspect this",
    "/group inspect this",
    "/mode team",
    "/mode accept",
    "/model doubao_seed",
    "/config",
    "/agent-type codeact",
    "/graph run demo {}",
  ]) {
    assert.equal(getCommandExecutionPolicy(input), "fifo", input);
    assert.equal(
      shouldQueueSubmission({
        input,
        streaming: true,
        queuedCount: 1,
      }),
      true,
      input,
    );
  }
});

test("the classifier uses an exact first token instead of slash prefixes", () => {
  assert.equal(getCommandExecutionPolicy("/tasks-extra"), "fifo");
  assert.equal(getCommandExecutionPolicy("/actors/list"), "fifo");
  assert.equal(getCommandExecutionPolicy("/unknown"), "fifo");
  assert.equal(getCommandToken("  /tasks  "), "/tasks");
  assert.equal(getCommandToken("ordinary text"), null);
});

test("a dequeued FIFO item executes even if newer items remain queued", () => {
  assert.equal(
    shouldQueueSubmission({
      input: "already selected by the consumer",
      fromQueue: true,
      streaming: false,
      queuedCount: 3,
    }),
    false,
  );
});

test("only plan-involved mode switches wait for round completion", () => {
  assert.equal(
    shouldDeferModeSwitch({
      currentMode: "default",
      targetMode: "accept",
      streamInFlight: true,
    }),
    false,
  );
  assert.equal(
    shouldDeferModeSwitch({
      currentMode: "accept",
      targetMode: "default",
      streamInFlight: true,
    }),
    false,
  );
  assert.equal(
    shouldDeferModeSwitch({
      currentMode: "default",
      targetMode: "plan",
      streamInFlight: true,
    }),
    true,
  );
  assert.equal(
    shouldDeferModeSwitch({
      currentMode: "plan",
      targetMode: "accept",
      streamInFlight: true,
    }),
    true,
  );
  assert.equal(
    shouldDeferModeSwitch({
      currentMode: "plan",
      targetMode: "default",
      streamInFlight: false,
    }),
    false,
  );
});
