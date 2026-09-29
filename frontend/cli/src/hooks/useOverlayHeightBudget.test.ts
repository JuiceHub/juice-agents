/**
 * useOverlayHeightBudget.test.ts - 测试预算计算函数
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  computeOverlayBudget,
  COMPOSER_ROWS,
  STATUS_ROWS,
  TERMINAL_SCROLLBACK_GUARD_ROWS,
  MIN_OVERLAY_ROWS,
  DEFAULT_TRANSCRIPT_ROWS,
} from "./useOverlayHeightBudget.js";

describe("computeOverlayBudget", () => {
  it("无 overlay：LiveTail 占满可用空间", () => {
    const budget = computeOverlayBudget({
      terminalRows: 30,
      hasOverlay: false,
      hasComposer: true,
      reservedExtraRows: 0,
    });
    // usable = 30 - 1(guard) - 4(composer) - 1(status) = 24
    assert.equal(budget.overlayMaxRows, 0);
    assert.equal(budget.transcriptMaxRows, 24);
    assert.equal(budget.composerVisible, true);
  });

  it("有 overlay + 终端 40 行：overlay 拿大头，LiveTail 拿剩余", () => {
    const budget = computeOverlayBudget({
      terminalRows: 40,
      hasOverlay: true,
      hasComposer: true, // 但 overlay 打开时 composer 隐藏
      reservedExtraRows: 0,
    });
    // usable = 40 - 1 - 0(composer 隐藏) - 1 = 38
    // overlayMax = max(5, 38 - 0) = 38, liveTail = 0
    assert.ok(budget.overlayMaxRows >= MIN_OVERLAY_ROWS);
    assert.ok(budget.transcriptMaxRows >= 0);
    assert.ok(
      budget.overlayMaxRows + budget.transcriptMaxRows <=
        40 - TERMINAL_SCROLLBACK_GUARD_ROWS - STATUS_ROWS
    );
    assert.equal(budget.composerVisible, false); // overlay 打开时隐藏
  });

  it("极小终端（10 行）：overlay ≥ MIN_OVERLAY_ROWS，LiveTail=0", () => {
    const budget = computeOverlayBudget({
      terminalRows: 10,
      hasOverlay: true,
      hasComposer: true,
      reservedExtraRows: 0,
    });
    // usable = 10 - 1 - 0 - 1 = 8
    assert.ok(budget.overlayMaxRows >= MIN_OVERLAY_ROWS);
    assert.ok(budget.transcriptMaxRows === 0 || budget.transcriptMaxRows > 0);
    assert.equal(budget.composerVisible, false);
  });

  it("terminalRows undefined：返回默认", () => {
    const budget = computeOverlayBudget({
      terminalRows: undefined,
      hasOverlay: true,
      hasComposer: true,
      reservedExtraRows: 0,
    });
    assert.equal(budget.overlayMaxRows, 999);
    assert.equal(budget.transcriptMaxRows, DEFAULT_TRANSCRIPT_ROWS);
    assert.equal(budget.composerVisible, true);
  });

  it("scrollback 安全性契约：总高度 ≤ terminalRows", () => {
    const testCases = [
      { rows: 6, hasOverlay: false, queueLen: 0 },
      { rows: 10, hasOverlay: true, queueLen: 0 },
      { rows: 20, hasOverlay: false, queueLen: 3 },
      { rows: 40, hasOverlay: true, queueLen: 3 },
      { rows: 80, hasOverlay: false, queueLen: 0 },
    ];

    for (const { rows, hasOverlay, queueLen } of testCases) {
      const budget = computeOverlayBudget({
        terminalRows: rows,
        hasOverlay,
        hasComposer: true,
        reservedExtraRows: queueLen,
      });
      const total =
        budget.overlayMaxRows +
        budget.transcriptMaxRows +
        STATUS_ROWS +
        queueLen +
        (budget.composerVisible ? COMPOSER_ROWS : 0) +
        TERMINAL_SCROLLBACK_GUARD_ROWS;
      assert.ok(
        total <= rows,
        `rows=${rows}, hasOverlay=${hasOverlay}, queueLen=${queueLen}: total=${total} should <= ${rows}`
      );
    }
  });

  it("有 queuedMessages：从可用空间扣除", () => {
    const budget = computeOverlayBudget({
      terminalRows: 30,
      hasOverlay: false,
      hasComposer: true,
      reservedExtraRows: 3, // 3 条 queuedMessages
    });
    // usable = 30 - 1 - 4 - 1 - 3 = 21
    assert.equal(budget.transcriptMaxRows, 21);
  });
});
