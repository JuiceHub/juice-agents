import assert from "node:assert/strict";
import test from "node:test";

import {
  acceptCommandCompletion,
  buildGhostText,
  buildInlineHint,
  getCommandCandidates,
} from "./useCompletion.js";

test("offers /models and /model in top-level slash completion", () => {
  const candidates = getCommandCandidates("/mo");

  assert.deepEqual(
    candidates.map((item) => item.label),
    ["/mode", "/models", "/model"],
  );
});

test("offers memory and dream commands in slash completion", () => {
  assert.deepEqual(
    getCommandCandidates("/con").map((item) => item.label),
    ["/config"],
  );
  assert.deepEqual(
    getCommandCandidates("/mem").map((item) => item.label),
    ["/memory"],
  );
  assert.deepEqual(
    getCommandCandidates("/ag").map((item) => item.label),
    ["/agent-type", "/agents"],
  );
  // `/resources` is the public resource command and completes only from its
  // own prefix; no hidden compatibility alias is retained.
  assert.deepEqual(
    getCommandCandidates("/reso").map((item) => item.label),
    ["/resources"],
  );
  assert.deepEqual(
    getCommandCandidates("/dr").map((item) => item.label),
    ["/dream"],
  );
});

test("offers memory subcommands", () => {
  assert.deepEqual(
    getCommandCandidates("/memory s").map((item) => item.label),
    ["status", "search"],
  );
  assert.deepEqual(
    getCommandCandidates("/memory v").map((item) => item.label),
    ["view"],
  );
});

test("offers goal command and lifecycle subcommands", () => {
  assert.deepEqual(
    getCommandCandidates("/go").map((item) => item.label),
    ["/goal"],
  );
  assert.deepEqual(
    getCommandCandidates("/goal r").map((item) => item.label),
    ["resume"],
  );
  assert.deepEqual(
    getCommandCandidates("/goal c").map((item) => item.label),
    ["clear"],
  );
});

test("offers loop and cron command completions", () => {
  assert.deepEqual(
    getCommandCandidates("/lo").map((item) => item.label),
    ["/loop"],
  );
  assert.deepEqual(
    getCommandCandidates("/cr").map((item) => item.label),
    ["/cron"],
  );
  assert.deepEqual(
    getCommandCandidates("/cron d").map((item) => item.label),
    ["delete"],
  );
  assert.deepEqual(
    getCommandCandidates("/loop l").map((item) => item.label),
    ["list"],
  );
});

test("offers worktree command completions", () => {
  assert.deepEqual(
    getCommandCandidates("/wor").map((item) => item.label),
    ["/worktree"],
  );
  assert.deepEqual(
    getCommandCandidates("/worktree s").map((item) => item.label),
    ["status"],
  );
  assert.deepEqual(
    getCommandCandidates("/worktree e").map((item) => item.label),
    ["enter", "exit"],
  );
  assert.deepEqual(
    getCommandCandidates("/worktree exit --").map((item) => item.label),
    ["--discard"],
  );
});

test("does not offer removed memory set candidates", () => {
  assert.deepEqual(
    getCommandCandidates("/memory set d"),
    [],
  );
  assert.deepEqual(
    getCommandCandidates("/memory set memory o"),
    [],
  );
});

test("offers runtime model names for /model", () => {
  const candidates = getCommandCandidates("/model gp", ["gpt4o_mini", "doubao_seed"]);

  assert.deepEqual(candidates.map((item) => item.label), ["gpt4o_mini"]);
});

test("keeps /skills as a query command and offers skills with dollar invocation", () => {
  assert.deepEqual(
    getCommandCandidates("/ski").map((item) => item.label),
    ["/skills"],
  );

  const skillCandidates = getCommandCandidates("$jo", [], ["joke-expert"]);

  assert.deepEqual(skillCandidates.map((item) => item.label), ["$joke-expert"]);
});

test("offers plugin dollar invocation and /plugins query arguments", () => {
  assert.deepEqual(
    getCommandCandidates("$rev", [], [], undefined, ["review-pack"]).map((item) => item.label),
    ["$review-pack"],
  );
  assert.deepEqual(
    getCommandCandidates("/plugins rev", [], [], undefined, ["review-pack"]).map((item) => item.label),
    ["review-pack"],
  );
});

test("offers the reserved graph dollar namespace", () => {
  assert.deepEqual(
    getCommandCandidates("$graph:d").map((item) => item.label),
    ["$graph:deep_research"],
  );
});

test("offers skill names for /skills", () => {
  const candidates = getCommandCandidates("/skills jo", [], ["joke-expert"]);

  assert.deepEqual(candidates.map((item) => item.label), ["joke-expert"]);
});

test("offers availability-filtered agent names for /agents", () => {
  const candidates = getCommandCandidates(
    "/agents re",
    [],
    [],
    undefined,
    [],
    ["research_worker", "reviewer"],
  );

  assert.deepEqual(candidates.map((item) => item.label), ["research_worker", "reviewer"]);
});

test("offers graph names for generic graph commands", () => {
  assert.deepEqual(
    getCommandCandidates("/graph run d").map((item) => item.label),
    ["deep_research"],
  );
});

test("offers direct deep research command, source flags, and JSON payload templates", () => {
  assert.deepEqual(
    getCommandCandidates("/deep").map((item) => item.label),
    ["/deep-research"],
  );
  assert.deepEqual(
    getCommandCandidates("/deep-research --w").map((item) => item.label),
    ["--web", "--workspace", "--web-workspace"],
  );
  assert.deepEqual(
    getCommandCandidates("/deep-research {").map((item) => item.label),
    [
      '{"question":"","source_mode":"web"}',
      '{"question":"","source_mode":"workspace"}',
      '{"question":"","source_mode":"web_workspace"}',
    ],
  );
});

test("deduplicates repeated skill aliases in completion candidates", () => {
  const duplicated = ["joke-expert", "joke-expert"];

  assert.deepEqual(
    getCommandCandidates("$jo", [], duplicated).map((item) => item.label),
    ["$joke-expert"],
  );
  assert.deepEqual(
    getCommandCandidates("/skills jo", [], duplicated).map((item) => item.label),
    ["joke-expert"],
  );
});

test("skill aliases win when a plugin has the same dollar name", () => {
  assert.deepEqual(
    getCommandCandidates("$rev", [], ["review"], undefined, ["review", "review-pack"]),
    [
      { label: "$review", description: "Skill" },
      { label: "$review-pack", description: "Plugin" },
    ],
  );
});

test("offers candidates from the token at the cursor", () => {
  const candidates = getCommandCandidates("prefix /mo suffix", [], [], 10);

  assert.deepEqual(
    candidates.map((item) => item.label),
    ["/mode", "/models", "/model"],
  );
});

test("hides completion candidates after the current token is complete", () => {
  // 子命令被完整输入后停止提示
  assert.deepEqual(getCommandCandidates("/memory status"), []);
  // 完整输入的技能名也不再作为候选返回
  assert.deepEqual(getCommandCandidates("/skills joke-expert", [], ["joke-expert"]), []);
});

test("keeps top-level candidates visible until a trailing space closes the panel", () => {
  // 命令名被完整输入时仍保留候选（含自身与同前缀命令），方便继续浏览
  assert.deepEqual(
    getCommandCandidates("/mode").map((item) => item.label),
    ["/mode", "/models", "/model"],
  );
  assert.deepEqual(
    getCommandCandidates("/model").map((item) => item.label),
    ["/models", "/model"],
  );
  // 补全时补入的尾随空格会关闭面板，让 ↑/↓ 回到历史记录导航
  assert.deepEqual(getCommandCandidates("/mode "), []);
});

test("accepts completion by replacing only the token at the cursor", () => {
  const accepted = acceptCommandCompletion({
    input: "run /mo later",
    cursor: 7,
    candidate: { label: "/model", description: "Show or switch runtime model" },
  });

  // 后面已有空白时不再补空格，避免出现连续两个空格
  assert.deepEqual(accepted, {
    input: "run /model later",
    cursor: 10,
  });
});

test("appends a trailing space when accepting completion at the end of the line", () => {
  const accepted = acceptCommandCompletion({
    input: "/mo",
    cursor: 3,
    candidate: { label: "/mode", description: "Switch permission mode" },
  });

  assert.deepEqual(accepted, {
    input: "/mode ",
    cursor: 6,
  });
  // 补全结果不再产生候选，面板随即关闭
  assert.deepEqual(getCommandCandidates(accepted.input), []);
});

test("builds ghost text from the cursor token", () => {
  assert.equal(
    buildGhostText({
      input: "run /mo later",
      cursor: 7,
      candidates: [{ label: "/model", description: "Show or switch runtime model" }],
      selectedIndex: 0,
      visible: true,
    }),
    "del",
  );
});

test("buildInlineHint shows options when command is followed by space", () => {
  const candidates = getCommandCandidates("/mode ");
  const hint = buildInlineHint({
    input: "/mode ",
    cursor: 6,
    candidates,
  });

  assert.equal(hint, "<agent|plan|team|group>");
});

test("buildInlineHint shows agent mode options", () => {
  const candidates = getCommandCandidates("/mode ");
  const hint = buildInlineHint({
    input: "/mode ",
    cursor: 6,
    candidates,
  });

  assert.equal(hint, "<agent|plan|team|group>");
});

test("buildInlineHint returns null when no trailing space", () => {
  const candidates = getCommandCandidates("/mode");
  const hint = buildInlineHint({
    input: "/mode",
    cursor: 5,
    candidates,
  });

  assert.equal(hint, null);
});

test("buildInlineHint returns null when cursor not at end", () => {
  const candidates = getCommandCandidates("/mode ", undefined, undefined, 3);
  const hint = buildInlineHint({
    input: "/mode ",
    cursor: 3,
    candidates,
  });

  assert.equal(hint, null);
});

test("buildInlineHint shows memory subcommand options", () => {
  const candidates = getCommandCandidates("/memory ");
  const hint = buildInlineHint({
    input: "/memory ",
    cursor: 8,
    candidates,
  });

  assert.equal(hint, "<status|search|view>");
});

test("buildInlineHint shows worktree subcommand options", () => {
  const candidates = getCommandCandidates("/worktree ");
  const hint = buildInlineHint({
    input: "/worktree ",
    cursor: 10,
    candidates,
  });

  assert.equal(hint, "<enter|exit|list|status>");
});

test("buildInlineHint shows worktree exit options", () => {
  const candidates = getCommandCandidates("/worktree exit ");
  const hint = buildInlineHint({
    input: "/worktree exit ",
    cursor: 15,
    candidates,
  });

  assert.equal(hint, "<--discard>");
});

test("buildInlineHint returns null when already typing argument", () => {
  const candidates = getCommandCandidates("/mode d");
  const hint = buildInlineHint({
    input: "/mode d",
    cursor: 7,
    candidates,
  });

  // 当已经在输入参数时，不显示内联提示
  assert.equal(hint, null);
});

test("buildInlineHint returns null for non-slash commands", () => {
  const candidates = getCommandCandidates("hello");
  const hint = buildInlineHint({
    input: "hello",
    cursor: 5,
    candidates,
  });

  assert.equal(hint, null);
});
