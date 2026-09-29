import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  getRichComposerModel,
  isActorsHotkey,
  isTasksHotkey,
  isModeCycleKey,
  resolveDeleteEdit,
} from "./RichComposer.js";

test("renders multiple draft lines with a prompt gutter", () => {
  const model = getRichComposerModel({
    value: "/mode default\nExplain the tradeoff",
    disabled: false,
  });

  assert.equal(model.lineCount, 2);
  assert.equal(model.lines[0], "/mode default");
  assert.equal(model.lines[1], "Explain the tradeoff");
  assert.equal(model.showCursor, true);
});

test("falls back to the placeholder when the draft is empty", () => {
  const model = getRichComposerModel({
    value: "",
    disabled: false,
  });

  assert.deepEqual(model.lines, []);
  assert.equal(model.placeholder, "Ask for code, docs, or a workflow change");
  assert.equal(model.showCursor, true);
});

test("hides the cursor while the composer is disabled", () => {
  const model = getRichComposerModel({
    value: "busy draft",
    disabled: true,
  });

  assert.equal(model.showCursor, false);
});

test("keeps the cursor visible while streaming submissions remain available", () => {
  const model = getRichComposerModel({
    value: "prepare the next prompt",
    disabled: false,
  });

  assert.equal(model.showCursor, true);
});

test("detects Ctrl+S as the actors hotkey", () => {
  assert.equal(isActorsHotkey("s", { ctrl: true }), true);
  assert.equal(isActorsHotkey("S", { ctrl: true }), true);
  assert.equal(isActorsHotkey("\x13", {}), true);
  assert.equal(isActorsHotkey("s", { ctrl: false }), false);
  assert.equal(isActorsHotkey("t", { ctrl: true }), false);
});

test("detects Ctrl+T as the tasks hotkey", () => {
  assert.equal(isTasksHotkey("t", { ctrl: true }), true);
  assert.equal(isTasksHotkey("T", { ctrl: true }), true);
  assert.equal(isTasksHotkey("\x14", {}), true);
  assert.equal(isTasksHotkey("t", { ctrl: false }), false);
  assert.equal(isTasksHotkey("s", { ctrl: true }), false);
});

test("detects Shift+Tab as mode cycling without matching plain Tab", () => {
  assert.equal(isModeCycleKey("", { tab: true, shift: true }), true);
  assert.equal(isModeCycleKey("\x1b[Z", {}), true);
  assert.equal(isModeCycleKey("", { tab: true, shift: false }), false);
});

test("treats Ink delete events as backspace for common terminal backspace bytes", () => {
  assert.deepEqual(
    resolveDeleteEdit("abc", 3, { delete: true }),
    { value: "ab", cursor: 2 },
  );
});

test("separates input listener ownership from composer disabled visuals", () => {
  const source = readFileSync(
    fileURLToPath(new URL("./RichComposer.tsx", import.meta.url)),
    "utf-8"
  );

  assert.match(source, /isActive:\s*boolean/);
  assert.match(source, /useInput\([\s\S]*\},\s*\{\s*isActive\s*\}\s*\)/);
});

test("handles Shift+Tab before plain Tab completion", () => {
  const source = readFileSync(
    fileURLToPath(new URL("./RichComposer.tsx", import.meta.url)),
    "utf-8"
  );

  assert.match(source, /onCycleMode:\s*\(\)\s*=>\s*void/);
  assert.match(source, /if \(isModeCycleKey\(ch, key\)\)[\s\S]*?onCycleMode\(\)/);
  assert.ok(
    source.indexOf("isModeCycleKey(ch, key)") < source.indexOf("if (key.tab)"),
    "Shift+Tab must be handled before plain Tab completion",
  );
});

test("keeps history navigation state synchronous across repeated arrow keys", () => {
  const source = readFileSync(
    fileURLToPath(new URL("./RichComposer.tsx", import.meta.url)),
    "utf-8"
  );

  assert.match(source, /const controllerRef = useRef\(controller\)/);
  assert.match(source, /controllerRef\.current = next/);
  assert.match(source, /cycleHistoryUp\(controllerRef\.current, value, cursor\)/);
  assert.match(source, /cycleHistoryDown\(controllerRef\.current\)/);
});

test("prioritizes completion, active history browsing, then multiline movement", () => {
  const source = readFileSync(
    fileURLToPath(new URL("./RichComposer.tsx", import.meta.url)),
    "utf-8"
  );

  const upBranch = source.slice(source.indexOf("if (key.upArrow)"));
  const downBranch = source.slice(source.indexOf("if (key.downArrow)"));
  const upAfterMultiline = upBranch.slice(upBranch.indexOf("const nextCursor = moveCursorUp(value, cursor)"));
  const downAfterMultiline = downBranch.slice(downBranch.indexOf("const nextCursor = moveCursorDown(value, cursor)"));

  assert.ok(
    upBranch.indexOf("if (candidatesVisible)") <
      upBranch.indexOf("if (controllerRef.current.historyIndex !== -1)"),
    "Up must move visible completion candidates before history"
  );
  assert.ok(
    upBranch.indexOf("if (controllerRef.current.historyIndex !== -1)") <
      upBranch.indexOf("const nextCursor = moveCursorUp(value, cursor)"),
    "Up must keep browsing history before multiline cursor movement"
  );
  assert.ok(
    upAfterMultiline.indexOf("const nextCursor = moveCursorUp(value, cursor)") <
      upAfterMultiline.indexOf("cycleHistoryUp(controllerRef.current, value, cursor)"),
    "Up must enter history only after multiline cursor movement cannot continue"
  );
  assert.ok(
    downBranch.indexOf("if (candidatesVisible)") <
      downBranch.indexOf("if (controllerRef.current.historyIndex !== -1)"),
    "Down must move visible completion candidates before history"
  );
  assert.ok(
    downBranch.indexOf("if (controllerRef.current.historyIndex !== -1)") <
      downBranch.indexOf("const nextCursor = moveCursorDown(value, cursor)"),
    "Down must keep browsing history before multiline cursor movement"
  );
  assert.ok(
    downAfterMultiline.indexOf("const nextCursor = moveCursorDown(value, cursor)") <
      downAfterMultiline.indexOf("cycleHistoryDown(controllerRef.current)"),
    "Down must enter history only after multiline cursor movement cannot continue"
  );
});
