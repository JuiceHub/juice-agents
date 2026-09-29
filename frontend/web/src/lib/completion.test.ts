import test from "node:test";
import assert from "node:assert/strict";
import {
  WEB_COMPLETION_VISIBLE_LIMIT,
  acceptWebCompletion,
  getVisibleWebCompletions,
  getWebCommandCandidates,
} from "./completion.js";
import type { PluginInfo, SkillInfo } from "@juice-agents/shared/gateway/types";

test("slash completion keeps skills as a static query command", () => {
  const candidates = getWebCommandCandidates({
    input: "/sk",
    skills: [makeSkill({ name: "skill-review", qualified_name: "tools/skill-review" })],
  });

  assert.deepEqual(candidates.map((candidate) => candidate.label), ["/skills"]);
});

test("dollar completion includes skill and plugin invocations", () => {
  const skillCandidates = getWebCommandCandidates({
    input: "$ski",
    skills: [makeSkill({ name: "skill-review", qualified_name: "tools/skill-review" })],
  });
  const pluginCandidates = getWebCommandCandidates({
    input: "$rev",
    plugins: [makePlugin({ name: "review-pack" })],
  });

  assert.deepEqual(skillCandidates.map((candidate) => candidate.label), ["$skill-review"]);
  assert.deepEqual(pluginCandidates.map((candidate) => candidate.label), ["$review-pack"]);
});

test("web dollar completion reserves graph namespace", () => {
  assert.deepEqual(
    getWebCommandCandidates({ input: "$graph:d" }).map((candidate) => candidate.label),
    ["$graph:deep_research"],
  );
});

test("slash completion suggests command arguments", () => {
  assert.deepEqual(
    getWebCommandCandidates({ input: "/mode p" }).map((candidate) => candidate.label),
    ["plan"]
  );
  assert.deepEqual(
    getWebCommandCandidates({ input: "/skills ski", skills: [makeSkill({ name: "skill-review" })] }).map(
      (candidate) => candidate.label
    ),
    ["skill-review"]
  );
  assert.deepEqual(
    getWebCommandCandidates({ input: "/plugins rev", plugins: [makePlugin({ name: "review-pack" })] }).map(
      (candidate) => candidate.label
    ),
    ["review-pack"]
  );
  assert.deepEqual(
    getWebCommandCandidates({
      input: "/agents re",
      availableAgentNames: ["research_worker", "reviewer"],
    }).map((candidate) => candidate.label),
    ["research_worker", "reviewer"]
  );
  assert.deepEqual(
    getWebCommandCandidates({ input: "/graph run d" }).map((candidate) => candidate.label),
    ["deep_research"]
  );
  assert.deepEqual(
    getWebCommandCandidates({ input: "/deep" }).map((candidate) => candidate.label),
    ["/deep-research"]
  );
  assert.deepEqual(
    getWebCommandCandidates({ input: "/deep-research {" }).map((candidate) => candidate.label),
    [
      '{"question":"","source_mode":"web"}',
      '{"question":"","source_mode":"workspace"}',
      '{"question":"","source_mode":"web_workspace"}',
    ]
  );
  assert.deepEqual(
    getWebCommandCandidates({ input: "/deep-research --w" }).map((candidate) => candidate.label),
    ["--web", "--workspace", "--web-workspace"]
  );
});

test("accepted completion replaces the token at the cursor", () => {
  assert.deepEqual(
    acceptWebCompletion({
      input: "/skills ski extra",
      cursor: "/skills ski".length,
      candidate: { label: "skill-review", description: "Skill name" },
    }),
    {
      input: "/skills skill-review extra",
      cursor: "/skills skill-review".length,
    }
  );
});

test("visible web completions show a five item moving window", () => {
  const candidates = Array.from({ length: 8 }, (_, index) => ({
    label: `/cmd-${index}`,
    description: `Command ${index}`,
  }));

  const visible = getVisibleWebCompletions({
    candidates,
    selectedIndex: 6,
  });

  assert.equal(visible.length, WEB_COMPLETION_VISIBLE_LIMIT);
  assert.deepEqual(
    visible.map((candidate) => candidate.label),
    ["/cmd-2", "/cmd-3", "/cmd-4", "/cmd-5", "/cmd-6"]
  );
  assert.deepEqual(
    visible.map((candidate) => candidate.originalIndex),
    [2, 3, 4, 5, 6]
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

function makePlugin(overrides: Partial<PluginInfo>): PluginInfo {
  return {
    schema_version: 1,
    name: "demo-plugin",
    version: "0.1.0",
    description: "",
    root: "/plugins/demo-plugin",
    source: "workspace",
    enabled: true,
    skill_roots: [],
    skill_names: [],
    ...overrides,
  };
}
