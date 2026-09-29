import assert from "node:assert/strict";
import test from "node:test";
import stringWidth from "string-width";

import { flattenBlockToRows } from "./transcriptRows.js";

test("regular result text wraps by words and preserves paragraph indent", () => {
  const columns = 65;
  const rows = flattenBlockToRows(
    {
      kind: "assistant",
      text:
        "[result]\n" +
        "git add + commit + push complete.\n" +
        "\n" +
        "  Commit 6eaf348 on master (48 files changed, 619 insertions, 152 deletions), pushed to juice-agents/master (12d593d...6eaf348).\n" +
        "  The changes form one coherent feature: propagating agent_type (react/codeact protocol) through subagents, groups, teams, the deep_research graph, evaluation, and the stdio gateway runtime, centralized via a shared normalize_agent_type helper.",
    },
    columns,
    "result"
  );

  const innerWidth = columns - 2;
  for (const row of rows) {
    assert.ok(
      stringWidth(`${row.prefix}${row.text}`) <= innerWidth,
      `row exceeds width: ${row.prefix}${row.text}`
    );
  }

  assert.deepEqual(
    rows.slice(0, 4).map((row) => [row.prefix, row.text]),
    [
      ["● ", "[result]"],
      ["  ", "git add + commit + push complete."],
      ["  ", " "],
      ["    ", "Commit 6eaf348 on master (48 files changed, 619 insertions,"],
    ]
  );

  const indentedParagraphRows = rows.filter((row) =>
    row.text.includes("Commit") ||
    row.text.includes("pushed") ||
    row.text.includes("The changes") ||
    row.text.includes("evaluation") ||
    row.text.includes("stdio gateway")
  );
  assert.ok(indentedParagraphRows.length >= 5);
  assert.ok(indentedParagraphRows.every((row) => row.prefix === "    "));
  assert.doesNotMatch(rows.map((row) => row.text).join("\n"), /evalu\nation/);
});
