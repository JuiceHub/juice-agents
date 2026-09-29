import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { PassThrough } from "node:stream";
import { Text, render } from "ink";

import {
  AlternateScreen,
  ENTER_ALT_SCREEN,
  EXIT_ALT_SCREEN,
} from "./AlternateScreen.js";

// 用 createElement 而非 JSX：保持 .test.ts 扩展名以匹配 npm test 的 *.test.ts glob。
const h = React.createElement;

// DEC 1049 序列常量自检：必须以 ESC(\x1b) 开头。
test("DEC 1049 进出序列字节正确", () => {
  assert.equal(ENTER_ALT_SCREEN, "\x1b[?1049h\x1b[2J\x1b[H");
  assert.equal(EXIT_ALT_SCREEN, "\x1b[?1049l");
});

function makeHarness() {
  const events: string[] = [];
  const stdout = new PassThrough() as any;
  Object.assign(stdout, { columns: 40, rows: 8, isTTY: true });
  stdout.on("data", (c: Buffer) => events.push(Buffer.from(c).toString()));
  return { stdout, output: () => events.join("") };
}

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));

test("挂载即进入 alt-screen，卸载恢复主屏", async () => {
  const harness = makeHarness();
  const inst = render(
    h(AlternateScreen, { rows: 8 }, h(Text, null, "hello")),
    { stdout: harness.stdout, patchConsole: false }
  );
  await tick();
  assert.ok(harness.output().includes("\x1b[?1049h"), "应发送进入 alt-screen 序列");
  assert.ok(harness.output().includes("hello"), "应渲染子内容");

  inst.unmount();
  await tick(20);
  assert.ok(
    harness.output().includes(EXIT_ALT_SCREEN),
    "卸载时应发送退出 alt-screen 序列恢复主屏"
  );
});

test("alt-screen 内不触发 clearTerminal 灾难分支（内容未超 rows）", async () => {
  const harness = makeHarness();
  const inst = render(
    h(AlternateScreen, { rows: 8 }, h(Text, null, "line1")),
    { stdout: harness.stdout, patchConsole: false }
  );
  await tick();
  const output = harness.output();
  // 灾难分支特征：完整 clearTerminal "[2J[3J[H"（我们自己的 ENTER 只含 [2J 不含 [3J）
  assert.ok(!/\x1b\[2J\x1b\[3J\x1b\[H/.test(output), "不应触发 Ink 的 clearTerminal");
  assert.ok(!/\x1b\[3J/.test(output), "不应擦除原生 scrollback (CSI 3J)");
  inst.unmount();
});
