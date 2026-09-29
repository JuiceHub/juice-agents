import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { buildComposerDockModel } from "./ComposerDock.js";

test("stays quiet by default when the composer is ready", () => {
  const model = buildComposerDockModel({
    permissionMode: "default",
    agentMode: "agent",
    value: "",
    disabled: false,
    loading: false,
    candidates: [],
    selectedIndex: 0,
    completionVisible: false,
    ghostText: null,
    inlineHint: null,
  });

  assert.equal(model.transientHint, null);
  assert.equal(model.modeLine, "agent · default");
  assert.equal(model.overlayVisible, false);
});

test("shows the current agent mode below the composer", () => {
  const model = buildComposerDockModel({
    permissionMode: "default", agentMode: "plan",
    value: "",
    disabled: false,
    loading: false,
    candidates: [],
    selectedIndex: 0,
    completionVisible: false,
    ghostText: null,
    inlineHint: null,
  });

  assert.equal(model.modeLine, "plan · default");
  assert.equal(model.transientHint, null);
});

test("shows the agent mode next to the permission mode", () => {
  const model = buildComposerDockModel({
    permissionMode: "default",
    agentMode: "team",
    value: "",
    disabled: false,
    loading: false,
    candidates: [],
    selectedIndex: 0,
    completionVisible: false,
    ghostText: null,
    inlineHint: null,
  });

  assert.equal(model.modeLine, "team · default");
});

test("shows the latest mode target while a safe-boundary switch is pending", () => {
  const model = buildComposerDockModel({
    permissionMode: "default",
    pendingMode: "accept",
    agentMode: "team",
    value: "",
    disabled: false,
    loading: false,
    candidates: [],
    selectedIndex: 0,
    completionVisible: false,
    ghostText: null,
    inlineHint: null,
  });

  assert.equal(model.modeLine, "team · default → accept pending");
});

test("mode indicator uses the same warning color for every mode", () => {
  const source = readFileSync(
    fileURLToPath(new URL("./ComposerDock.tsx", import.meta.url)),
    "utf-8",
  );

  assert.match(source, /<Text color=\{TUI_THEME\.state\.warning\}>/);
  assert.doesNotMatch(source, /props\.mode === "plan"/);
});

test("shows completion overlay and ghost text only when slash completion is active", () => {
  const model = buildComposerDockModel({
    permissionMode: "default",
    agentMode: "agent",
    value: "/mo",
    disabled: false,
    loading: false,
    candidates: [
      { label: "/mode", description: "Switch execution mode" },
      { label: "/models", description: "List available models" },
    ],
    selectedIndex: 0,
    completionVisible: true,
    ghostText: "de",
    inlineHint: null,
  });

  assert.equal(model.overlayVisible, true);
  assert.equal(model.overlayItems.length, 2);
  assert.equal(model.ghostText, "de");
  assert.equal(model.transientHint, null);
});

test("surfaces actor view hint as transient text under the composer", () => {
  const model = buildComposerDockModel({
    permissionMode: "default",
    agentMode: "agent",
    value: "",
    disabled: false,
    loading: false,
    candidates: [],
    selectedIndex: 0,
    completionVisible: false,
    ghostText: null,
    inlineHint: null,
    actorViewHint: "Viewing: researcher · interactive · Esc return",
  });

  assert.equal(
    model.transientHint,
    "Viewing: researcher · interactive · Esc return"
  );
});
