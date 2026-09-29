import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_INLINE_PARAM_CHARS,
  MAX_TOOL_PREVIEW_LINES,
  formatToolHeader,
  formatToolOutputPreview,
  pickPrimaryToolParameter,
} from "./toolOutputFormatter.js";

const baseToolCall = {
  name: "read",
  mode: "tool",
  status: "success",
  parameters: { path: "/workspace/app.tsx" },
} as const;

test("formatToolHeader renders ▸ Ran <name>(arg=val) for success", () => {
  const header = formatToolHeader({ ...baseToolCall });
  assert.equal(header.glyph, "▸");
  assert.equal(header.label, "Ran read");
  assert.equal(header.inlineParams, "(path: /workspace/app.tsx)");
  assert.equal(header.failed, false);
});

test("formatToolHeader switches to ✗ Failed and marks failed when status=error", () => {
  const header = formatToolHeader({ ...baseToolCall, status: "error" });
  assert.equal(header.glyph, "✗");
  assert.equal(header.label, "Failed read");
  assert.equal(header.failed, true);
});

test("formatToolHeader truncates long inline params with an ellipsis", () => {
  const longValue = "a".repeat(MAX_INLINE_PARAM_CHARS + 20);
  const header = formatToolHeader({
    ...baseToolCall,
    parameters: { command: longValue },
  });
  assert.ok(header.inlineParams.endsWith("…)"));
  // 整体不超过 `(command: ` + MAX_INLINE_PARAM_CHARS + `)` 长度。
  assert.ok(header.inlineParams.length <= 11 + MAX_INLINE_PARAM_CHARS + 1);
});

test("formatToolHeader keeps only the first line of multi-line param values", () => {
  const header = formatToolHeader({
    ...baseToolCall,
    name: "shell",
    parameters: { command: "echo first\necho second" },
  });
  assert.equal(header.inlineParams, "(command: echo first)");
});

test("formatToolHeader returns empty inlineParams when no non-empty parameters exist", () => {
  const header = formatToolHeader({
    ...baseToolCall,
    parameters: { path: "" },
  });
  assert.equal(header.inlineParams, "");
});

test("formatToolOutputPreview keeps all lines when under the limit", () => {
  const preview = formatToolOutputPreview("a\nb\nc");
  assert.deepEqual(preview.lines, ["a", "b", "c"]);
  assert.equal(preview.truncatedCount, 0);
});

test("formatToolOutputPreview folds extra lines into truncatedCount", () => {
  const text = Array.from({ length: 10 }, (_, i) => `line-${i}`).join("\n");
  const preview = formatToolOutputPreview(text);
  assert.equal(preview.lines.length, MAX_TOOL_PREVIEW_LINES);
  assert.equal(preview.truncatedCount, 10 - MAX_TOOL_PREVIEW_LINES);
  assert.equal(preview.lines[0], "line-0");
});

test("formatToolOutputPreview returns empty for empty input and trims trailing whitespace", () => {
  const empty = formatToolOutputPreview("");
  assert.deepEqual(empty.lines, []);
  assert.equal(empty.truncatedCount, 0);

  const trailing = formatToolOutputPreview("a\nb\n\n   \n");
  assert.deepEqual(trailing.lines, ["a", "b"]);
  assert.equal(trailing.truncatedCount, 0);
});

test("pickPrimaryToolParameter returns first multiline or long param, else null", () => {
  const single = pickPrimaryToolParameter({
    ...baseToolCall,
    parameters: { path: "/short" },
  });
  assert.equal(single, null);

  const multiline = pickPrimaryToolParameter({
    ...baseToolCall,
    parameters: { command: "echo a\necho b" },
  });
  assert.deepEqual(multiline, { key: "command", value: "echo a\necho b" });

  const longSingleLine = pickPrimaryToolParameter({
    ...baseToolCall,
    parameters: { code: "x".repeat(MAX_INLINE_PARAM_CHARS + 5) },
  });
  assert.ok(longSingleLine && longSingleLine.key === "code");
});
