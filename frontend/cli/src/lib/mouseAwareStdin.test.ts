import assert from "node:assert/strict";
import test from "node:test";

import { parseMouseSequences } from "./mouseAwareStdin.js";

test("纯文本无 mouse 序列：原样输出", () => {
  const r = parseMouseSequences("hello world");
  assert.equal(r.clean, "hello world");
  assert.equal(r.leftover, "");
  assert.deepEqual(r.wheels, []);
});

test("wheel up：emit up 事件，序列从字节流剥离", () => {
  const r = parseMouseSequences("a\x1b[<64;10;5Mb");
  assert.equal(r.clean, "ab");
  assert.equal(r.leftover, "");
  assert.equal(r.wheels.length, 1);
  assert.equal(r.wheels[0].direction, "up");
  assert.equal(r.wheels[0].x, 10);
  assert.equal(r.wheels[0].y, 5);
  assert.equal(r.wheels[0].shift, false);
});

test("wheel down：btn=65", () => {
  const r = parseMouseSequences("\x1b[<65;1;1M");
  assert.equal(r.wheels[0].direction, "down");
  assert.equal(r.clean, "");
});

test("wheel + shift 修饰键（bit 4 = 0b100 = 4）", () => {
  // btn = 64 | 4 = 68 = wheel up + shift
  const r = parseMouseSequences("\x1b[<68;1;1M");
  assert.equal(r.wheels[0].direction, "up");
  assert.equal(r.wheels[0].shift, true);
});

test("普通 mouse click 不产生 wheel 事件，但仍从字节流剥离", () => {
  // btn=0 (左键按下)
  const r = parseMouseSequences("x\x1b[<0;5;5My");
  assert.equal(r.clean, "xy", "click 字节必须从流中剥离，避免泄漏到 ink");
  assert.deepEqual(r.wheels, []);
});

test("mouse release（m 结尾）不产生 wheel，也剥离", () => {
  const r = parseMouseSequences("\x1b[<64;1;1m");
  assert.equal(r.clean, "");
  assert.deepEqual(r.wheels, [], "release 不产 wheel（避免重复）");
});

test("末尾不完整序列进 leftover", () => {
  const r = parseMouseSequences("hello\x1b[<64;1");
  assert.equal(r.clean, "hello");
  assert.equal(r.leftover, "\x1b[<64;1");
  assert.deepEqual(r.wheels, []);
});

test("跨 chunk 拼接：第二段补全后产生事件", () => {
  const r1 = parseMouseSequences("\x1b[<64;");
  assert.equal(r1.clean, "");
  assert.equal(r1.leftover, "\x1b[<64;");

  const r2 = parseMouseSequences(r1.leftover + "10;5Mafter");
  assert.equal(r2.clean, "after");
  assert.equal(r2.wheels.length, 1);
  assert.equal(r2.wheels[0].direction, "up");
});

test("误判保护：ESC[< 后跟非数字非分号，把 ESC 当字符吐出继续解析", () => {
  // 这种情况理论上不该出现，但防御性处理：把 ESC 当普通字符
  const r = parseMouseSequences("\x1b[<abc");
  assert.equal(r.clean, "\x1b[<abc");
  assert.deepEqual(r.wheels, []);
});

test("一段输入含多个 wheel 事件", () => {
  const r = parseMouseSequences(
    "\x1b[<65;1;1M\x1b[<65;1;1M\x1b[<64;1;1M"
  );
  assert.equal(r.wheels.length, 3);
  assert.equal(r.wheels[0].direction, "down");
  assert.equal(r.wheels[1].direction, "down");
  assert.equal(r.wheels[2].direction, "up");
  assert.equal(r.clean, "");
});

test("普通字符与 mouse 序列交错", () => {
  const r = parseMouseSequences("hello\x1b[<64;1;1Mworld\x1b[<65;2;2Mend");
  assert.equal(r.clean, "helloworldend");
  assert.equal(r.wheels.length, 2);
});
