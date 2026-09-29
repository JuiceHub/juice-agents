import assert from "node:assert/strict";
import test from "node:test";

import type { CandidateItem } from "../hooks/useCompletion.js";
import {
  MAX_VISIBLE_COMPLETION_CANDIDATES,
  getCandidatePanelModel,
} from "./CandidatePanel.js";

const candidates: CandidateItem[] = [
  { label: "/help", description: "Show available commands" },
  { label: "/mode", description: "Switch execution mode" },
];

test("returns overlay completion metadata with inline keyboard guidance", () => {
  const model = getCandidatePanelModel({
    candidates,
    selectedIndex: 1,
    visible: true,
  });

  assert.equal(model.hint, "Tab accept · Esc close");
  assert.equal(model.layout, "overlay");
  assert.equal(model.total, 2);
  assert.equal(model.hasHiddenItems, false);
  assert.equal(model.items[1]?.isSelected, true);
  assert.equal(model.items[1]?.description, "Switch execution mode");
});

test("limits the visible completion window to five candidates", () => {
  const manyCandidates = Array.from({ length: 8 }, (_, index) => ({
    label: `/cmd-${index}`,
    description: `Command ${index}`,
  }));

  const model = getCandidatePanelModel({
    candidates: manyCandidates,
    selectedIndex: 6,
    visible: true,
  });

  assert.equal(model.items.length, MAX_VISIBLE_COMPLETION_CANDIDATES);
  assert.equal(model.startIndex, 2);
  assert.equal(model.total, 8);
  assert.equal(model.hasHiddenItems, true);
  assert.deepEqual(
    model.items.map((item) => item.label),
    ["/cmd-2", "/cmd-3", "/cmd-4", "/cmd-5", "/cmd-6"],
  );
  assert.equal(model.items[4]?.isSelected, true);
});

test("hides the completion overlay model when not visible", () => {
  const model = getCandidatePanelModel({
    candidates,
    selectedIndex: 0,
    visible: false,
  });

  assert.equal(model.visible, false);
  assert.equal(model.items.length, 0);
  assert.equal(model.total, 2);
});
