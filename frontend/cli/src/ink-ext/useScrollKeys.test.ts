import assert from "node:assert/strict";
import test from "node:test";

import { resolveScrollAction, type ScrollAction } from "./useScrollKeys.js";

// 构造一个全 false 的 key 标志，按需覆盖。
function k(overrides: Partial<Record<string, boolean>> = {}) {
  return {
    downArrow: false,
    upArrow: false,
    pageDown: false,
    pageUp: false,
    ctrl: false,
    ...overrides,
  } as any;
}

test("navOnly 默认：PgUp/PgDn 始终可用（与 composer 无冲突）", () => {
  assert.deepEqual(resolveScrollAction("", k({ pageDown: true }), 8), { type: "by", delta: 8 });
  assert.deepEqual(resolveScrollAction("", k({ pageUp: true }), 8), { type: "by", delta: -8 });
});

test("navOnly 默认：j/k/方向键/g/G 不触发（避免与文本输入冲突）", () => {
  assert.equal(resolveScrollAction("j", k(), 5), null);
  assert.equal(resolveScrollAction("k", k(), 5), null);
  assert.equal(resolveScrollAction("", k({ downArrow: true }), 5), null);
  assert.equal(resolveScrollAction("", k({ upArrow: true }), 5), null);
  assert.equal(resolveScrollAction("g", k(), 5), null);
  assert.equal(resolveScrollAction("G", k(), 5), null);
});

test("navOnly=false：j / ↓ 下滚一行", () => {
  assert.deepEqual(resolveScrollAction("j", k(), 5, false), { type: "by", delta: 1 });
  assert.deepEqual(resolveScrollAction("", k({ downArrow: true }), 5, false), { type: "by", delta: 1 });
});

test("navOnly=false：k / ↑ 上滚一行", () => {
  assert.deepEqual(resolveScrollAction("k", k(), 5, false), { type: "by", delta: -1 });
  assert.deepEqual(resolveScrollAction("", k({ upArrow: true }), 5, false), { type: "by", delta: -1 });
});

test("navOnly=false：Ctrl+D/U 半屏", () => {
  assert.deepEqual(resolveScrollAction("d", k({ ctrl: true }), 8, false), { type: "by", delta: 8 });
  assert.deepEqual(resolveScrollAction("u", k({ ctrl: true }), 8, false), { type: "by", delta: -8 });
});

test("navOnly=false：g 跳顶，G 跳底", () => {
  assert.deepEqual(resolveScrollAction("g", k(), 5, false), { type: "top" } as ScrollAction);
  assert.deepEqual(resolveScrollAction("G", k(), 5, false), { type: "bottom" } as ScrollAction);
});

test("半屏至少 1 行（viewportHeight 极小时不为 0）", () => {
  assert.deepEqual(resolveScrollAction("", k({ pageDown: true }), 0), { type: "by", delta: 1 });
});

test("无关按键返回 null", () => {
  assert.equal(resolveScrollAction("x", k(), 5), null);
  assert.equal(resolveScrollAction("", k(), 5), null);
});
