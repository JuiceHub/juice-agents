import assert from "node:assert/strict";
import test from "node:test";

import {
  applyAskCustomTextInput,
  ASK_CUSTOM_OPTION_VALUE,
  buildAskPromptModel,
  buildAskResponse,
  toggleAskSelection,
  type AskOptionItem,
} from "./AskPrompt.js";

const options: AskOptionItem[] = [
  { label: "背景", value: "background", description: "人物背景" },
  { label: "目标", value: "goal", description: "任务目标" },
];

test("builds single choice ask prompt guidance", () => {
  const model = buildAskPromptModel({
    question: "需要确定哪些人物细节？",
    options,
    selectedIndex: 0,
    checkedValues: new Set(),
    multiple: false,
    customValue: "",
    allowCustom: true,
  });

  assert.equal(model.question, "需要确定哪些人物细节？");
  assert.equal(model.items[0].cursor, "›");
  assert.equal(model.items[0].mark, "○");
  assert.equal(model.items[1].mark, "○");
  assert.equal(model.items[2].isCustom, true);
  assert.equal(model.items[2].option.label, "Other");
  assert.equal(model.helpLine, "↑↓ 选择 · 输入文字选择 Other · Enter 确认 · Esc 取消");
});

test("toggles multiple choice selections without mutating previous set", () => {
  const checked = new Set<string>(["background"]);

  const next = toggleAskSelection(checked, "goal");

  assert.deepEqual([...checked], ["background"]);
  assert.deepEqual([...next].sort(), ["background", "goal"]);
});

test("builds answer from selected option for single choice", () => {
  const response = buildAskResponse({
    requestId: "ask-1",
    status: "answered",
    options,
    selectedIndex: 1,
    checkedValues: new Set(),
    multiple: false,
    customValue: "历史输入不应随普通选项提交",
    allowCustom: true,
  });

  assert.equal(response.request_id, "ask-1");
  assert.equal(response.status, "answered");
  assert.deepEqual(response.selected, [options[1]]);
  assert.equal(response.custom_response, "");
});

test("builds answer from selected Other for single choice", () => {
  const response = buildAskResponse({
    requestId: "ask-1b",
    status: "answered",
    options,
    selectedIndex: options.length,
    checkedValues: new Set(),
    multiple: false,
    customValue: "还需要年龄范围",
    allowCustom: true,
  });

  assert.deepEqual(response.selected, []);
  assert.equal(response.custom_response, "还需要年龄范围");
});

test("builds answer from checked values and custom response for multiple choice", () => {
  const response = buildAskResponse({
    requestId: "ask-2",
    status: "answered",
    options,
    selectedIndex: 0,
    checkedValues: new Set(["goal", "background", ASK_CUSTOM_OPTION_VALUE]),
    multiple: true,
    customValue: "还需要年龄范围",
    allowCustom: true,
  });

  assert.deepEqual(response.selected, options);
  assert.equal(response.custom_response, "还需要年龄范围");
});

test("typing a custom answer selects Other and checks it for multiple choice", () => {
  const next = applyAskCustomTextInput({
    options,
    checkedValues: new Set(["goal"]),
    customValue: "还",
    input: "需要年龄范围",
    multiple: true,
  });

  assert.equal(next.selectedIndex, options.length);
  assert.equal(next.customValue, "还需要年龄范围");
  assert.deepEqual([...next.checkedValues].sort(), [ASK_CUSTOM_OPTION_VALUE, "goal"].sort());
});

test("builds cancelled response without selections", () => {
  const response = buildAskResponse({
    requestId: "ask-3",
    status: "cancelled",
    options,
    selectedIndex: 0,
    checkedValues: new Set(["goal"]),
    multiple: true,
    customValue: "ignored",
  });

  assert.equal(response.status, "cancelled");
  assert.deepEqual(response.selected, []);
  assert.equal(response.custom_response, "");
});

test("AskPrompt component uses submittedRef to prevent duplicate submissions", async () => {
  // 通过 source assertion 验证组件实现了防重复提交机制
  const { readFileSync } = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const sourcePath = fileURLToPath(new URL("./AskPrompt.tsx", import.meta.url));
  const source = readFileSync(sourcePath, "utf-8");

  // 必须有 useRef 并在提交时设置 submittedRef
  assert.match(source, /submittedRef/);
  assert.match(source, /submittedRef\.current = true/);
  assert.match(source, /if \(submittedRef\.current\) return/);
});
