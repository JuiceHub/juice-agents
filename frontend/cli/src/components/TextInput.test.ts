import assert from "node:assert/strict";
import test from "node:test";

import { getTextInputModel } from "./TextInput.js";

test("renders the cursor at the requested single-line position", () => {
  const model = getTextInputModel({
    value: "abcd",
    cursor: 2,
    disabled: false,
    placeholder: "Ask",
  });

  assert.equal(model.lines[0].beforeCursor, "ab");
  assert.equal(model.lines[0].afterCursor, "cd");
  assert.equal(model.lines[0].showCursor, true);
});

test("renders the cursor on the active multiline row only", () => {
  const model = getTextInputModel({
    value: "one\ntwo",
    cursor: 1,
    disabled: false,
    placeholder: "Ask",
  });

  assert.equal(model.lines.length, 2);
  assert.equal(model.lines[0].showCursor, true);
  assert.equal(model.lines[1].showCursor, false);
});

test("hides the active cursor while disabled", () => {
  const model = getTextInputModel({
    value: "busy",
    cursor: 2,
    disabled: true,
    placeholder: "Ask",
  });

  assert.equal(model.lines[0].showCursor, false);
});
