import assert from "node:assert/strict";
import test from "node:test";

import {
  closeConfigPanelLoad,
  createConfigPanelLoadState,
  openConfigPanelLoad,
  resolveConfigPanelLoad,
} from "./configPanelLoad.js";

test("Esc closes config loading immediately and rejects its late RPC result", () => {
  const loading = openConfigPanelLoad<string>(1);
  const closed = closeConfigPanelLoad<string>(2);

  assert.deepEqual(loading, { phase: "loading", generation: 1, data: null });
  assert.deepEqual(closed, { phase: "closed", generation: 2, data: null });
  assert.equal(resolveConfigPanelLoad(closed, 1, "stale snapshot"), closed);
});

test("only the latest config loading generation may open the panel", () => {
  const firstLoading = openConfigPanelLoad<string>(1);
  const secondLoading = openConfigPanelLoad<string>(2);

  assert.equal(resolveConfigPanelLoad(secondLoading, 1, "first snapshot"), secondLoading);
  assert.deepEqual(resolveConfigPanelLoad(secondLoading, 2, "second snapshot"), {
    phase: "ready",
    generation: 2,
    data: "second snapshot",
  });
  assert.deepEqual(createConfigPanelLoadState<string>(), {
    phase: "closed",
    generation: 0,
    data: null,
  });
});
