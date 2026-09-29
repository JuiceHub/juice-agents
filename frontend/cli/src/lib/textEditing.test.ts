import assert from "node:assert/strict";
import test from "node:test";

import {
  clampCursor,
  deleteBackwardAtCursor,
  deleteForwardAtCursor,
  getCursorLineColumn,
  insertTextAtCursor,
  moveCursorDown,
  moveCursorLeft,
  moveCursorRight,
  moveCursorUp,
} from "./textEditing.js";

test("clamps cursor positions into the draft range", () => {
  assert.equal(clampCursor("hello", -4), 0);
  assert.equal(clampCursor("hello", 99), 5);
});

test("moves across unicode grapheme boundaries instead of raw code units", () => {
  const value = "a🙂b";

  assert.equal(moveCursorRight(value, 1), 3);
  assert.equal(moveCursorLeft(value, 3), 1);
});

test("inserts and deletes text at the cursor position", () => {
  assert.deepEqual(insertTextAtCursor("ab", 1, "X"), {
    value: "aXb",
    cursor: 2,
  });
  assert.deepEqual(deleteBackwardAtCursor("aXb", 2), {
    value: "ab",
    cursor: 1,
  });
  assert.deepEqual(deleteForwardAtCursor("abX", 2), {
    value: "ab",
    cursor: 2,
  });
});

test("inserts newlines at the cursor position", () => {
  assert.deepEqual(insertTextAtCursor("hello world", 5, "\n"), {
    value: "hello\n world",
    cursor: 6,
  });
});

test("moves vertically within multiline drafts and reports edge boundaries", () => {
  const value = "abcd\nef\nghij";

  assert.deepEqual(getCursorLineColumn(value, 6), { line: 1, column: 1 });
  assert.equal(moveCursorUp(value, 6), 1);
  assert.equal(moveCursorDown(value, 6), 9);
  assert.equal(moveCursorUp(value, 1), null);
  assert.equal(moveCursorDown(value, value.length), null);
});
