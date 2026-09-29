import assert from "node:assert/strict";
import test from "node:test";
import stringWidth from "string-width";

import { formatTableForTerminal } from "./displayFormatter.js";

test("wraps table cells inside the available terminal width", () => {
  const text = formatTableForTerminal(
    [
      { key: "agent", label: "AGENT" },
      { key: "type", label: "TYPE" },
      { key: "plan_mode", label: "PLAN_MODE" },
      { key: "tools", label: "TOOLS" },
      { key: "description", label: "DESCRIPTION" },
    ],
    [
      {
        agent: "general",
        type: "react",
        plan_mode: "no",
        tools: "shell,python,read,write,edit,adaptive_patch",
        description: "General subagent for isolated execution, multi-step work, and necessary file edits.",
      },
      {
        agent: "plan",
        type: "react",
        plan_mode: "yes",
        tools: "read,glob,grep",
        description: "Read-only planning subagent for implementation approach design based on code search.",
      },
    ],
    78,
  );

  for (const line of text.split("\n")) {
    assert.ok(stringWidth(line) <= 78, `line exceeds width: ${line}`);
  }

  const [header, separator] = text.split("\n");
  assert.match(text, /DESCRIPTION/);
  assert.equal(separator.indexOf("---", header.indexOf("DESCRIPTION") - 1), header.indexOf("DESCRIPTION"));
  assert.ok(stringWidth(separator) < 78, "separator should not fill the full terminal width");
  assert.doesNotMatch(text, /\nwork, and necessary/);
  assert.match(
    text.split("\n").find((line) => line.includes("multi-step work")) || "",
    /^\s+\S+\s+multi-step work/,
  );
  assert.doesNotMatch(text, /implement\n\s+ation/);
  assert.match(text, /general\s+react\s+no/);
});

test("uses display width for CJK table content", () => {
  const text = formatTableForTerminal(
    [
      { key: "name", label: "NAME" },
      { key: "description", label: "DESCRIPTION" },
    ],
    [
      {
        name: "配置",
        description: "中文描述需要按照终端显示宽度换行，避免双宽字符撑破布局。",
      },
    ],
    30,
  );

  for (const line of text.split("\n")) {
    assert.ok(stringWidth(line) <= 30, `line exceeds width: ${line}`);
  }
});
