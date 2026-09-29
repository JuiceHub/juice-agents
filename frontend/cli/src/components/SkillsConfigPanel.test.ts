import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSkillsConfigApplyPlan,
  buildSkillsConfigRows,
  changeSkillsConfigDraft,
  createSkillsConfigDraft,
} from "./SkillsConfigPanel.js";

const status = {
  success: true,
  enabled: true,
  disabled: ["joke-expert"],
  count: 2,
  skills: [
    {
      name: "joke-expert",
      qualified_name: "joke-expert",
      description: "Tell jokes",
      source: "builtin",
      read_only: true,
      enabled: false,
    },
    {
      name: "code-review",
      qualified_name: "tools/code-review",
      description: "Review code",
      category: "tools",
      source: "local",
      read_only: false,
      enabled: true,
    },
  ],
};

test("creates a sorted draft that keeps disabled skills visible", () => {
  const draft = createSkillsConfigDraft(status);
  const rows = buildSkillsConfigRows({ draft, initialDraft: draft, selectedIndex: 0 });

  assert.equal(draft.skillsEnabled, true);
  assert.deepEqual(draft.disabledSkills, ["joke-expert"]);
  assert.deepEqual(
    rows.map((row) => [row.label, row.value, row.changed]),
    [
      ["code-review", "on", false],
      ["joke-expert", "off", false],
    ],
  );
});

test("toggles individual skill disabled entries without changing the global flag", () => {
  const initial = createSkillsConfigDraft(status);

  const codeReviewOff = changeSkillsConfigDraft(initial, "skill:tools/code-review");
  const jokeExpertOn = changeSkillsConfigDraft(initial, "skill:joke-expert");

  assert.equal(codeReviewOff.skillsEnabled, true);
  assert.deepEqual(codeReviewOff.disabledSkills, ["joke-expert", "tools/code-review"]);
  assert.deepEqual(jokeExpertOn.disabledSkills, []);
});

test("builds a complete skills apply plan for set_skills_config", () => {
  const initial = createSkillsConfigDraft(status);
  const draft = changeSkillsConfigDraft(initial, "skill:tools/code-review");

  assert.deepEqual(buildSkillsConfigApplyPlan(draft), {
    enabled: true,
    disabled: ["joke-expert", "tools/code-review"],
  });
});

test("matches disabled skill aliases by name and qualified name", () => {
  const draft = createSkillsConfigDraft({
    ...status,
    disabled: ["code-review"],
    skills: status.skills.map((skill) =>
      skill.name === "code-review" ? { ...skill, enabled: false } : skill,
    ),
  });
  const rows = buildSkillsConfigRows({ draft, initialDraft: draft, selectedIndex: 0 });

  assert.equal(rows.find((row) => row.label === "code-review")?.value, "off");
  assert.deepEqual(
    changeSkillsConfigDraft(draft, "skill:tools/code-review").disabledSkills,
    ["joke-expert"],
  );
});
