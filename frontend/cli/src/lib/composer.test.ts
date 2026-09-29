import assert from "node:assert/strict";
import test from "node:test";

import {
  createComposerController,
  cycleHistoryDown,
  cycleHistoryUp,
  pushHistory,
  resolveReturnAction,
  submitDraft,
} from "./composer.js";

test("submits a multiline draft without stripping interior line breaks", () => {
  const submitted = submitDraft("First line\nSecond line\n");

  assert.equal(submitted, "First line\nSecond line");
});

test("plain Enter submits slash commands when old Ink incorrectly reports Shift", () => {
  assert.equal(
    resolveReturnAction("\r", {
      return: true,
      meta: false,
      ctrl: false,
      shift: true,
    }),
    "submit"
  );
  assert.equal(resolveReturnAction("\n", { return: false }), "submit");
  assert.equal(resolveReturnAction("", { return: true, meta: true }), "newline");
  assert.equal(resolveReturnAction("\r", { return: false }), "newline");
});

test("cycles through history and restores an empty draft at the end", () => {
  const history = pushHistory([], "first command");
  const withSecond = pushHistory(history, "second command");
  const state = createComposerController(withSecond);

  const previous = cycleHistoryUp(state, "");
  assert.equal(previous.value, "second command");

  const oldest = cycleHistoryUp(previous, "");
  assert.equal(oldest.value, "first command");

  const backToRecent = cycleHistoryDown(oldest);
  assert.equal(backToRecent.value, "second command");

  const restoredDraft = cycleHistoryDown(backToRecent);
  assert.equal(restoredDraft.value, "");
  assert.equal(restoredDraft.historyIndex, -1);
});

test("keeps traversing older commands when cycling up repeatedly", () => {
  const state = createComposerController([
    "first command",
    "second command",
    "third command",
  ]);

  const third = cycleHistoryUp(state, "");
  const second = cycleHistoryUp(third, "");
  const first = cycleHistoryUp(second, "");
  const stillFirst = cycleHistoryUp(first, "");

  assert.equal(third.value, "third command");
  assert.equal(second.value, "second command");
  assert.equal(first.value, "first command");
  assert.equal(stillFirst.value, "first command");
});

test("cycles down to newer commands and then restores the empty draft", () => {
  const state = createComposerController([
    "first command",
    "second command",
    "third command",
  ]);

  const third = cycleHistoryUp(state, "");
  const second = cycleHistoryUp(third, "");
  const first = cycleHistoryUp(second, "");
  const newer = cycleHistoryDown(first);
  const newest = cycleHistoryDown(newer);
  const restored = cycleHistoryDown(newest);

  assert.equal(newer.value, "second command");
  assert.equal(newest.value, "third command");
  assert.equal(restored.value, "");
  assert.equal(restored.historyIndex, -1);
});
