import assert from "node:assert/strict";
import test from "node:test";

import { collectSkillAliases } from "./skills.js";

test("collectSkillAliases removes duplicate builtin aliases while keeping distinct qualified names", () => {
  const aliases = collectSkillAliases([
    {
      name: "joke-expert",
      qualified_name: "joke-expert",
    },
    {
      name: "writer",
      qualified_name: "team/writer",
    },
  ]);

  assert.deepEqual(aliases, ["joke-expert", "writer", "team/writer"]);
});
