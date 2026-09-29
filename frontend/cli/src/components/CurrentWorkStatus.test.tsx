import assert from "node:assert/strict";
import test from "node:test";

import { buildCurrentWorkStatusModel, pickElapsedTip } from "./CurrentWorkStatus.js";
import { TUI_COPY } from "../lib/theme.js";

test("stays hidden when no work status is active", () => {
  const model = buildCurrentWorkStatusModel({ streaming: false });

  assert.equal(model.visible, false);
  assert.equal(model.summary, "");
  assert.deepEqual(model.items, []);
});

test("shows thinking while model internals are running", () => {
  const model = buildCurrentWorkStatusModel({ streaming: true });

  assert.equal(model.visible, true);
  assert.equal(model.summary, TUI_COPY.currentWorkStatus);
  assert.deepEqual(model.items, []);
});

test("hides while an interactive panel owns the UI", () => {
  const model = buildCurrentWorkStatusModel({ streaming: true, interactive: true });

  assert.equal(model.visible, false);
  assert.equal(model.summary, "");
  assert.deepEqual(model.items, []);
});

test("keeps future status items in a stable view model", () => {
  const model = buildCurrentWorkStatusModel({
    streaming: true,
    summary: "Working...",
    items: [
      { label: "todo", value: "2/5 complete" },
      { label: "hint" },
    ],
  });

  assert.equal(model.visible, true);
  assert.equal(model.summary, "Working...");
  assert.deepEqual(model.items, [
    { label: "todo", value: "2/5 complete" },
    { label: "hint" },
  ]);
});

test("shows deep research progress without adding transcript messages", () => {
  const model = buildCurrentWorkStatusModel({
    streaming: true,
    lifecycleEvent: {
      event: "node_completed",
      node: "research_topic",
      completed: 2,
      total: 3,
    },
  });

  assert.equal(model.visible, true);
  assert.equal(model.summary, "Researching sources (2/3)...");
  assert.deepEqual(model.items, []);
});

test("maps final report lifecycle nodes to concise status text", () => {
  const model = buildCurrentWorkStatusModel({
    streaming: true,
    lifecycleEvent: {
      event: "node_started",
      node: "write_final_report",
    },
  });

  assert.equal(model.summary, "Writing final report...");
});

test("pickElapsedTip stays silent under 30 seconds", () => {
  assert.equal(pickElapsedTip(0), null);
  assert.equal(pickElapsedTip(29_000), null);
});

test("pickElapsedTip surfaces interrupt hint after 30 seconds", () => {
  const tip = pickElapsedTip(30_000);
  assert.ok(tip);
  assert.match(tip!, /Esc/);
});

test("pickElapsedTip switches to /clear hint after 5 minutes", () => {
  const tip = pickElapsedTip(5 * 60_000);
  assert.ok(tip);
  assert.match(tip!, /clear/);
});
