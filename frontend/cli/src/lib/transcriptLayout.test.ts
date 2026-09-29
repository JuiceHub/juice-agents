import assert from "node:assert/strict";
import test from "node:test";

import {
  layoutTranscriptMessages,
  splitByDisplayWidth,
} from "./transcriptLayout.js";

test("wraps ASCII, explicit newlines, and wide graphemes by terminal columns", () => {
  // assistant 块带 "● " 前缀（2 列），splitWidth = (8 - 2 padding) - 2 prefix = 4。
  const ascii = layoutTranscriptMessages(
    [{ kind: "assistant", text: "abcdefghij" }],
    8
  );
  // 宽字符场景：terminal=8、padding=2、prefix=2 → 每行可放 4 列 = 2 个汉字。
  const wide = layoutTranscriptMessages(
    [{ kind: "assistant", text: "你好世界" }],
    8
  );
  // user 块前缀同为 "● "（青色圆点），与 assistant 同宽。
  const multiline = layoutTranscriptMessages(
    [{ kind: "user", text: "first\nsecond" }],
    10
  );

  assert.deepEqual(
    ascii.rows.filter((row) => row.type === "content").map((row) => row.text),
    ["abcd", "efgh", "ij"]
  );
  assert.deepEqual(
    wide.rows.filter((row) => row.type === "content").map((row) => row.text),
    ["你好", "世界"]
  );
  assert.deepEqual(
    multiline.rows
      .filter((row) => row.type === "content")
      .map((row) => [row.prefix, row.text]),
    [
      ["● ", "first"],
      ["  ", "second"],
    ]
  );
});

test("lays out tool parameters as bounded visual rows", () => {
  const layout = layoutTranscriptMessages(
    [
      {
        kind: "tool",
        text: "Ran read",
        toolCall: {
          name: "read",
          mode: "tool",
          status: "success",
          parameters: { path: "/workspace/a/very/long/file.txt" },
        },
      },
    ],
    16
  );
  const content = layout.rows.filter((row) => row.type === "content");

  assert.ok(content.length > 2);
  // tool 块头部前缀升级为 "● "（彩色圆点），与 assistant 同色系
  assert.equal(content[0]?.prefix, "● ");
  assert.match(content.map((row) => row.text).join(""), /path:/);
});

test("splitByDisplayWidth handles empty input and trailing whitespace", () => {
  assert.deepEqual(splitByDisplayWidth("", 4), [" "]);
  assert.deepEqual(splitByDisplayWidth("abc", 1), ["a", "b", "c"]);
});
