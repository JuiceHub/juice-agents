/**
 * overlayWindowing.test.ts - 测试通用 windowing 算法
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  pickWindowAroundSelected,
  type WindowResult,
} from "./overlayWindowing.js";

describe("pickWindowAroundSelected", () => {
  it("全部装得下 → 无 hidden", () => {
    const items = ["a", "b", "c"];
    const result = pickWindowAroundSelected({
      items,
      rowsPerItem: () => 1,
      selectedIndex: 1,
      budgetRows: 10,
    });
    assert.deepEqual(result, {
      startIndex: 0,
      endIndex: 2,
      topHidden: 0,
      bottomHidden: 0,
    });
  });

  it("selected 居中 + 双向溢出", () => {
    // 10 项，每项 1 行，budget 5 行（含指示行预留）
    const items = Array.from({ length: 10 }, (_, i) => `item${i}`);
    const result = pickWindowAroundSelected({
      items,
      rowsPerItem: () => 1,
      selectedIndex: 5,
      budgetRows: 5,
    });
    // 预期：selected=5 居中，上下各取 1~2 项，预留 2 行给指示行
    // 实际窗口：3 项 + 2 行指示 = 5 行
    assert.ok(result.startIndex <= 5);
    assert.ok(result.endIndex >= 5);
    assert.ok(result.topHidden > 0);
    assert.ok(result.bottomHidden > 0);
    assert.ok(result.endIndex - result.startIndex + 1 <= 5);
  });

  it("selected 在头部", () => {
    const items = Array.from({ length: 10 }, (_, i) => `item${i}`);
    const result = pickWindowAroundSelected({
      items,
      rowsPerItem: () => 1,
      selectedIndex: 0,
      budgetRows: 5,
    });
    // selected 在顶部，向下扩展，bottomHidden > 0, topHidden = 0
    assert.equal(result.startIndex, 0);
    assert.equal(result.topHidden, 0);
    assert.ok(result.bottomHidden > 0);
    assert.ok(result.endIndex < 9);
  });

  it("selected 在尾部", () => {
    const items = Array.from({ length: 10 }, (_, i) => `item${i}`);
    const result = pickWindowAroundSelected({
      items,
      rowsPerItem: () => 1,
      selectedIndex: 9,
      budgetRows: 5,
    });
    // selected 在底部，向上扩展
    assert.equal(result.endIndex, 9);
    assert.equal(result.bottomHidden, 0);
    assert.ok(result.topHidden > 0);
    assert.ok(result.startIndex > 0);
  });

  it("极小 budget（仅够 selected 单项）", () => {
    const items = Array.from({ length: 10 }, (_, i) => `item${i}`);
    const result = pickWindowAroundSelected({
      items,
      rowsPerItem: () => 2, // 每项 2 行
      selectedIndex: 5,
      budgetRows: 2, // budget 仅 2 行
    });
    // 至少包含 selected
    assert.ok(result.startIndex <= 5);
    assert.ok(result.endIndex >= 5);
  });

  it("有 description 的项占 2 行", () => {
    interface Item {
      label: string;
      description?: string;
    }
    const items: Item[] = [
      { label: "a" },
      { label: "b", description: "desc" },
      { label: "c" },
      { label: "d", description: "desc" },
    ];
    const result = pickWindowAroundSelected({
      items,
      rowsPerItem: (item) => (item.description ? 2 : 1),
      selectedIndex: 1,
      budgetRows: 5,
    });
    // selected=1 (2 行) + 上下各 1 项 = 至多 5 行
    assert.ok(result.startIndex <= 1);
    assert.ok(result.endIndex >= 1);
  });

  it("空数组", () => {
    const result = pickWindowAroundSelected({
      items: [],
      rowsPerItem: () => 1,
      selectedIndex: 0,
      budgetRows: 10,
    });
    assert.deepEqual(result, {
      startIndex: 0,
      endIndex: -1,
      topHidden: 0,
      bottomHidden: 0,
    });
  });

  it("selectedIndex 越界", () => {
    const items = ["a", "b"];
    const result = pickWindowAroundSelected({
      items,
      rowsPerItem: () => 1,
      selectedIndex: 5,
      budgetRows: 10,
    });
    assert.deepEqual(result, {
      startIndex: 0,
      endIndex: -1,
      topHidden: 0,
      bottomHidden: 0,
    });
  });
});
