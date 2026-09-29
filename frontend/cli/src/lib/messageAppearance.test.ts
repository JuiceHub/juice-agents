import assert from "node:assert/strict";
import test from "node:test";

import { getMessageAppearance } from "./messageAppearance.js";
import { formatDisplayAsText } from "./presenter.js";

test("renders assistant replies with a gold dot anchor", () => {
  const appearance = getMessageAppearance({ kind: "final", text: "Done." });

  assert.equal(appearance.variant, "assistant");
  assert.equal(appearance.showLabel, false);
  assert.equal(appearance.useDot, true);
  assert.equal(appearance.prefix, "●");
  assert.equal(appearance.accentColor, "#f5d18b");
  assert.equal(appearance.dotColor, "#f5d18b");
});

test("renders user prompts with a teal dot anchor", () => {
  const appearance = getMessageAppearance({ kind: "user", text: "ship it" });

  assert.equal(appearance.variant, "user");
  assert.equal(appearance.showLabel, false);
  assert.equal(appearance.useDot, true);
  assert.equal(appearance.prefix, "●");
  assert.equal(appearance.accentColor, "#7bc4d6");
  assert.equal(appearance.dotColor, "#7bc4d6");
});

test("renders error messages with the cross prefix in red", () => {
  const appearance = getMessageAppearance({ kind: "error", text: "boom" });

  assert.equal(appearance.variant, "response");
  assert.equal(appearance.showLabel, false);
  assert.equal(appearance.useDot, false);
  assert.equal(appearance.prefix, "✗");
  assert.equal(appearance.accentColor, "#ff8a7a");
});

test("renders tool blocks with a gold dot (assistant-initiated action)", () => {
  const appearance = getMessageAppearance({ kind: "tool", text: "Read(...)" });

  assert.equal(appearance.variant, "tool");
  assert.equal(appearance.useDot, true);
  assert.equal(appearance.dotColor, "#d7a84d");
});

test("renders thinking blocks with the asterisk symbol, no dot", () => {
  const appearance = getMessageAppearance({ kind: "thinking", text: "..." });

  assert.equal(appearance.variant, "thinking");
  assert.equal(appearance.useDot, false);
  assert.equal(appearance.prefix, "✻");
});

test("formats structured table and key-value blocks for terminal fallback", () => {
  const tableText = formatDisplayAsText({
    type: "table",
    columns: [
      { key: "name", label: "NAME" },
      { key: "backend", label: "BACKEND" },
    ],
    rows: [{ name: "gpt4o_mini", backend: "openai" }],
  });
  const kvText = formatDisplayAsText({
    type: "kv",
    items: [
      { label: "runner_id", value: "runner-1" },
      { label: "mode", value: "default" },
    ],
  });

  assert.match(tableText, /NAME\s+BACKEND/);
  assert.match(tableText, /gpt4o_mini\s+openai/);
  assert.equal(kvText, "runner_id: runner-1\nmode: default");
});
