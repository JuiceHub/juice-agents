import assert from "node:assert/strict";
import test from "node:test";

import { getIntroBannerDisplayLines, getIntroBannerLineColors } from "./IntroPanel.js";

test("restarts the intro banner gradient after section breaks", () => {
  const colors = getIntroBannerLineColors({
    lines: ["JUICE-1", "JUICE-2", "", "AGENTS-1", "AGENTS-2"],
    gradient: ["top", "bottom"],
    fallbackColor: "fallback",
    hasBannerLines: true,
  });

  assert.deepEqual(colors, ["top", "bottom", "fallback", "top", "bottom"]);
});

test("uses the fallback wordmark color when no banner art is available", () => {
  const colors = getIntroBannerLineColors({
    lines: ["JUICE AGENTS"],
    gradient: ["top", "bottom"],
    fallbackColor: "fallback",
    hasBannerLines: false,
  });

  assert.deepEqual(colors, ["fallback"]);
});

test("displays separated intro banner sections on one row when the terminal is wide enough", () => {
  const lines = ["AA", "BB", "", "11", "22"];

  assert.deepEqual(
    getIntroBannerDisplayLines({
      lines,
      terminalColumns: 12,
      reservedColumns: 2,
      sectionGap: "  ",
    }),
    ["AA  11", "BB  22"]
  );
});

test("keeps intro banner sections stacked when the terminal is too narrow", () => {
  const lines = ["AA", "BB", "", "11", "22"];

  assert.deepEqual(
    getIntroBannerDisplayLines({
      lines,
      terminalColumns: 7,
      reservedColumns: 2,
      sectionGap: "  ",
    }),
    lines
  );
});
