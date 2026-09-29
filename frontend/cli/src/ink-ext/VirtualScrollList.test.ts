import assert from "node:assert/strict";
import test from "node:test";

import {
  computeWindow,
  applyScrollDelta,
} from "./VirtualScrollList.js";

// ---- computeWindow ----

test("computeWindow: 内容短于视口时全部可见", () => {
  const w = computeWindow(3, 10, 0);
  assert.deepEqual(w, { start: 0, end: 3, clampedScrollTop: 0 });
});

test("computeWindow: 内容长于视口时按 scrollTop 切出恰好 viewportHeight 行", () => {
  const w = computeWindow(100, 10, 20);
  assert.equal(w.start, 20);
  assert.equal(w.end, 30);
  assert.equal(w.end - w.start, 10, "可见行数必须恰好等于视口高度");
});

test("computeWindow: scrollTop 超过最大值被钳到底部", () => {
  // 总 100 行，视口 10，最大 scrollTop = 90
  const w = computeWindow(100, 10, 999);
  assert.equal(w.clampedScrollTop, 90);
  assert.equal(w.start, 90);
  assert.equal(w.end, 100);
});

test("computeWindow: 负 scrollTop 钳到 0", () => {
  const w = computeWindow(100, 10, -5);
  assert.equal(w.clampedScrollTop, 0);
  assert.equal(w.start, 0);
});

test("computeWindow: 边界 —— 空内容 / 零视口返回空窗口", () => {
  assert.deepEqual(computeWindow(0, 10, 0), { start: 0, end: 0, clampedScrollTop: 0 });
  assert.deepEqual(computeWindow(50, 0, 0), { start: 0, end: 0, clampedScrollTop: 0 });
});

test("computeWindow: 恰好填满视口", () => {
  const w = computeWindow(10, 10, 0);
  assert.deepEqual(w, { start: 0, end: 10, clampedScrollTop: 0 });
});

// ---- applyScrollDelta ----

test("applyScrollDelta: 向下滚到底触发 sticky", () => {
  // 总 100，视口 10，最大 90；当前 85，下滚 10 → 钳到 90 且 sticky
  const r = applyScrollDelta({ totalRows: 100, viewportHeight: 10, current: 85, delta: 10 });
  assert.equal(r.scrollTop, 90);
  assert.equal(r.sticky, true);
});

test("applyScrollDelta: 向上滚离开底部解除 sticky", () => {
  const r = applyScrollDelta({ totalRows: 100, viewportHeight: 10, current: 90, delta: -5 });
  assert.equal(r.scrollTop, 85);
  assert.equal(r.sticky, false);
});

test("applyScrollDelta: 向上滚到顶被钳到 0", () => {
  const r = applyScrollDelta({ totalRows: 100, viewportHeight: 10, current: 3, delta: -10 });
  assert.equal(r.scrollTop, 0);
  assert.equal(r.sticky, false);
});

test("applyScrollDelta: 内容短于视口时 maxScroll=0，任何滚动都 sticky", () => {
  const r = applyScrollDelta({ totalRows: 3, viewportHeight: 10, current: 0, delta: 5 });
  assert.equal(r.scrollTop, 0);
  assert.equal(r.sticky, true);
});
