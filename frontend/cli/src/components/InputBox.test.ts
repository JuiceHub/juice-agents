import assert from "node:assert/strict";
import test from "node:test";

import { getInputFrameModel } from "./InputBox.js";

test("builds a multiline-ready composer shell for an empty draft", () => {
  const model = getInputFrameModel({
    value: "",
    disabled: false,
    candidatesVisible: false,
  });

  assert.equal(model.prompt, ">");
  assert.equal(model.placeholder, "Ask for code, docs, or a workflow change");
  assert.equal(model.tone, "ready");
});

test("switches the composer shell into busy mode only when input is disabled", () => {
  const model = getInputFrameModel({
    value: "Summarize this repo",
    disabled: true,
    candidatesVisible: false,
  });

  assert.equal(model.prompt, "·");
  assert.equal(model.tone, "busy");
});

test("keeps the ready input shell while only submit is disabled", () => {
  const model = getInputFrameModel({
    value: "next draft",
    disabled: false,
    candidatesVisible: false,
  });

  assert.equal(model.prompt, ">");
  assert.equal(model.tone, "ready");
});

test("keeps the same borderless prompt while slash completion is visible", () => {
  const model = getInputFrameModel({
    value: "/mo",
    disabled: false,
    candidatesVisible: true,
  });

  assert.equal(model.tone, "active");
  assert.equal(model.prompt, ">");
});
