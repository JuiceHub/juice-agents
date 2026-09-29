import assert from "node:assert/strict";
import test from "node:test";

import { getStatusIconChar } from "./StatusIcon.js";

// 验证 6 种状态都映射到非空字符。具体字符由 figures 库决定（跨平台会回退），
// 这里只断言非空 + 有意义的语义匹配，避免锁死在某个平台上的具体字符。
test("getStatusIconChar returns a non-empty char for every status", () => {
  for (const status of [
    "success",
    "error",
    "warning",
    "info",
    "pending",
    "running",
  ] as const) {
    const char = getStatusIconChar(status);
    assert.equal(typeof char, "string");
    assert.ok(char.length > 0, `${status} should map to a non-empty icon`);
  }
});

test("running status uses ellipsis (animated semantic)", () => {
  // running 状态固定为 …，与 claude-code 的 loading 语义对齐
  assert.equal(getStatusIconChar("running"), "…");
});
