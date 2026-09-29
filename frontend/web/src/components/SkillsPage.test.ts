import assert from "node:assert/strict";
import test from "node:test";

import { buildNextSkillToggleConfig } from "./SkillsPage.js";

test("skill toggle disables by qualified name", () => {
  const next = buildNextSkillToggleConfig({
    enabled: true,
    disabled: [],
    skill: {
      name: "code-review",
      qualified_name: "tools/code-review",
      enabled: true,
    },
  });

  assert.deepEqual(next, {
    enabled: true,
    disabled: ["tools/code-review"],
  });
});

test("skill toggle removes all aliases when re-enabling", () => {
  const next = buildNextSkillToggleConfig({
    enabled: true,
    disabled: ["code-review", "tools/code-review", "other"],
    skill: {
      name: "code-review",
      qualified_name: "tools/code-review",
      enabled: false,
    },
  });

  assert.deepEqual(next, {
    enabled: true,
    disabled: ["other"],
  });
});
