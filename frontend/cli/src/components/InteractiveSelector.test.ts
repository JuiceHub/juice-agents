import assert from "node:assert/strict";
import test from "node:test";

import {
  buildInteractiveSelectorModel,
  cycleModelEffort,
  SELECTOR_EFFORT_LABEL_COLOR,
  SELECTOR_EFFORT_VALUE_COLOR,
  type SelectorEffort,
} from "./InteractiveSelector.js";
import { TUI_THEME } from "../lib/theme.js";

test("cycles model effort left and right without changing selected item", () => {
  const source = { supported_efforts: ["disabled", "low", "medium", "high", "auto"] };

  assert.equal(cycleModelEffort("disabled", "right", source), "low");
  assert.equal(cycleModelEffort("low", "right", source), "medium");
  assert.equal(cycleModelEffort("medium", "right", source), "high");
  assert.equal(cycleModelEffort("high", "right", source), "auto");
  assert.equal(cycleModelEffort("auto", "right", source), "disabled");

  assert.equal(cycleModelEffort("disabled", "left", source), "auto");
  assert.equal(cycleModelEffort("auto", "left", source), "high");
});

test("cycles model effort using the selected model supported efforts", () => {
  const source = { supported_efforts: ["disabled", "low", "medium", "high", "max"] };

  assert.equal(cycleModelEffort("high", "right", source), "max");
  assert.equal(cycleModelEffort("max", "right", source), "disabled");
  assert.equal(cycleModelEffort("xhigh", "right", source), "low");
});

test("builds model selector guidance with effort controls", () => {
  const model = buildInteractiveSelectorModel({
    title: "Select Model",
    items: [
      { label: "doubao_lite", value: "doubao_lite", active: true },
      { label: "doubao_pro", value: "doubao_pro" },
    ],
    selectedIndex: 1,
    effort: "high" satisfies SelectorEffort,
  });

  assert.equal(model.selectedItem?.value, "doubao_pro");
  assert.equal(model.effortLine, "Effort: high  ← → to adjust");
  assert.equal(model.helpLine, "↑↓ 选择 · ←→ 调 effort · Enter 确认 · Esc 取消");
});

test("uses readable colors for the model effort selector", () => {
  assert.equal(SELECTOR_EFFORT_LABEL_COLOR, TUI_THEME.surface.text);
  assert.equal(SELECTOR_EFFORT_VALUE_COLOR, TUI_THEME.brand.wordmark);
  assert.notEqual(SELECTOR_EFFORT_LABEL_COLOR, TUI_THEME.surface.muted);
  assert.notEqual(SELECTOR_EFFORT_VALUE_COLOR, TUI_THEME.surface.muted);
});

test("keeps non-effort selectors on the original guidance", () => {
  const model = buildInteractiveSelectorModel({
    title: "Select Mode",
    items: [{ label: "agent", value: "agent" }],
    selectedIndex: 0,
  });

  assert.equal(model.effortLine, null);
  assert.equal(model.helpLine, "↑↓ 选择 · Enter 确认 · Esc 取消");
});

test("pickVisibleItems: 25 项 / maxRows=10 windowing", async (t) => {
  const { pickVisibleItems } = await import("./InteractiveSelector.js");
  const items = Array.from({ length: 25 }, (_, i) => ({
    label: `model${i}`,
    value: `model${i}`,
  }));

  const window = pickVisibleItems({
    items,
    selectedIndex: 12,
    maxRows: 10,
    hasEffort: false,
  });

  // 窗口应包含 selected
  assert.ok(window.startIndex <= 12);
  assert.ok(window.startIndex + window.visibleItems.length - 1 >= 12);
  // 应有上下溢出
  assert.ok(window.topHidden > 0 || window.bottomHidden > 0);
  // 可见项数应 <= maxRows - chrome
  assert.ok(window.visibleItems.length <= 10 - 5);
});

test("pickVisibleItems: selected 切到末尾，窗口跟随", async (t) => {
  const { pickVisibleItems } = await import("./InteractiveSelector.js");
  const items = Array.from({ length: 20 }, (_, i) => ({
    label: `item${i}`,
    value: `item${i}`,
  }));

  const window = pickVisibleItems({
    items,
    selectedIndex: 19,
    maxRows: 10,
    hasEffort: false,
  });

  // selected=19 应在窗口内
  assert.equal(window.startIndex + window.visibleItems.length - 1, 19);
  assert.equal(window.bottomHidden, 0);
  assert.ok(window.topHidden > 0);
});

test("pickVisibleItems: maxRows 缺省时全量渲染", async (t) => {
  const { pickVisibleItems } = await import("./InteractiveSelector.js");
  const items = Array.from({ length: 10 }, (_, i) => ({
    label: `item${i}`,
    value: `item${i}`,
  }));

  const window = pickVisibleItems({
    items,
    selectedIndex: 5,
    maxRows: 999,
    hasEffort: false,
  });

  assert.equal(window.visibleItems.length, 10);
  assert.equal(window.topHidden, 0);
  assert.equal(window.bottomHidden, 0);
});
