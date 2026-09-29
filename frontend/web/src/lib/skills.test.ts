import test from "node:test";
import assert from "node:assert/strict";
import { collectSkillAliases, filterInstalledSkills, skillIconLabel } from "./skills.js";
import type { SkillInfo } from "@juice-agents/shared/gateway/types";

test("installed skills are filtered by name, description, category, and source", () => {
  const skills: SkillInfo[] = [
    makeSkill({ name: "Browser", description: "Open webpages", category: "tools", source: "builtin" }),
    makeSkill({ name: "OpenAI Docs", description: "Reference docs", category: "docs", source: "system" }),
  ];

  assert.deepEqual(filterInstalledSkills(skills, "docs").map((skill) => skill.name), ["OpenAI Docs"]);
  assert.deepEqual(filterInstalledSkills(skills, "builtin").map((skill) => skill.name), ["Browser"]);
});

test("skill icon labels use the first visible skill name character", () => {
  assert.equal(skillIconLabel(makeSkill({ name: "imagegen" })), "I");
});

test("skill aliases keep qualified names but remove duplicates", () => {
  assert.deepEqual(
    collectSkillAliases([
      makeSkill({ name: "joke-expert", qualified_name: "joke-expert" }),
      makeSkill({ name: "writer", qualified_name: "team/writer" }),
    ]),
    ["joke-expert", "writer", "team/writer"]
  );
});

function makeSkill(overrides: Partial<SkillInfo>): SkillInfo {
  return {
    name: "demo",
    qualified_name: "demo",
    description: "",
    source: "builtin",
    read_only: true,
    ...overrides,
  };
}
