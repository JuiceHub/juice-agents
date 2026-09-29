import assert from "node:assert/strict";
import test from "node:test";

import {
  buildConfigApplyPlan,
  buildConfigPanelModel,
  changeConfigDraft,
  createConfigPanelDraft,
  type ConfigPanelDraft,
} from "./ConfigPanel.js";

const session = {
  runner_id: "runner-1",
  permission_mode: "default",
  agent_mode: "agent",
  root_actor_name: "root",
  base_dir: "/tmp/workspace",
  started: true,
  resumed: false,
  agent_type: "react",
  model_name: "gpt4o_mini",
  model_effort: "disabled",
  backend: "openai",
  provider_model_name: "gpt-4o-mini",
};

const memory = {
  enabled: true,
  dream_enabled: false,
  memory_dir: "/tmp/workspace/.juice/memory",
  entrypoint: "/tmp/workspace/.juice/memory/MEMORY.md",
  topic_count: 2,
};

const models = [
  {
    model_name: "gpt4o_mini",
    backend: "openai",
    provider_model_name: "gpt-4o-mini",
    supported_efforts: ["disabled", "low", "medium", "high", "auto"],
  },
  {
    model_name: "doubao_seed",
    backend: "doubao",
    provider_model_name: "doubao-seed-1-6",
    supported_efforts: ["disabled", "low", "medium", "high", "auto"],
  },
  {
    model_name: "claude-haiku-4-5-20251001",
    backend: "anthropic",
    provider_model_name: "claude-haiku-4-5-20251001",
    supported_efforts: ["disabled"],
  },
];

const skillsConfig = {
  success: true,
  enabled: true,
  disabled: [],
  count: 2,
  skills: [
    {
      name: "joke-expert",
      qualified_name: "joke-expert",
      description: "Tell jokes",
      source: "builtin",
      read_only: true,
      enabled: true,
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

test("builds the initial config draft from session, memory, and model data", () => {
  const draft = createConfigPanelDraft({ session, memory, models, skillsConfig });
  const model = buildConfigPanelModel({
    draft,
    initialDraft: draft,
    models,
    selectedIndex: 0,
  });

  assert.deepEqual(draft, {
    memoryEnabled: true,
    dreamEnabled: false,
    browserEnabled: true,
    imageEnabled: false,
    skillsEnabled: true,
    selfEvolutionEnabled: true,
    graphsEnabled: true,
    disabledSkills: [],
    skillsCount: 2,
    permissionMode: "default",
    agentMode: "agent",
    agentType: "react",
    modelName: "gpt4o_mini",
    modelEffort: "disabled",
  });
  assert.deepEqual(
    model.rows.map((row) => [row.label, row.value, row.changed]),
    [
      ["Workspace memory", "on", false],
      ["Auto dream", "off", false],
      ["Browser tools", "on", false],
      ["Image tools", "off", false],
      ["Skills", "on", false],
      ["Self evolution", "on", false],
      ["Graphs", "on", false],
      ["Agent mode", "agent", false],
      ["Agent type", "react", false],
      ["Model", "gpt4o_mini", false],
      ["Model effort", "disabled", false],
    ],
  );
  assert.equal(model.helpLine, "Up/Down select · Space/Left/Right change · Enter save · Esc cancel");
});

test("space and right change the current config item", () => {
  const initial = createConfigPanelDraft({ session, memory, models });

  const memoryChanged = changeConfigDraft(initial, "memory", "next", models);
  const browserChanged = changeConfigDraft(initial, "browser", "next", models);
  const imageChanged = changeConfigDraft(initial, "image", "next", models);
  const modelChanged = changeConfigDraft(initial, "model", "next", models);

  assert.equal(memoryChanged.memoryEnabled, false);
  assert.equal(browserChanged.browserEnabled, false);
  assert.equal(imageChanged.imageEnabled, true);
  assert.equal(modelChanged.modelName, "doubao_seed");
});

test("left moves enum items backward and flips on/off items", () => {
  const initial = createConfigPanelDraft({ session, memory, models });

  const memoryChanged = changeConfigDraft(initial, "memory", "previous", models);
  const effortChanged = changeConfigDraft(initial, "model_effort", "previous", models);

  assert.equal(memoryChanged.memoryEnabled, false);
  assert.equal(effortChanged.modelEffort, "auto");
});

test("model effort choices come from the selected model configuration", () => {
  const initial = createConfigPanelDraft({
    session: { ...session, model_effort: "high" },
    memory,
    models,
  });

  const firstChange = changeConfigDraft(initial, "model", "next", models);
  const secondChange = changeConfigDraft(firstChange, "model", "next", models);
  const effortChanged = changeConfigDraft(secondChange, "model_effort", "next", models);

  assert.equal(secondChange.modelName, "claude-haiku-4-5-20251001");
  assert.equal(secondChange.modelEffort, "disabled");
  assert.equal(effortChanged.modelEffort, "disabled");
});

test("builds an empty apply plan when the draft is cancelled or unchanged", () => {
  const initial = createConfigPanelDraft({ session, memory, models });

  const plan = buildConfigApplyPlan(initial, initial);

  assert.deepEqual(plan, {
    memoryChanges: [],
    browserChanges: [],
    imageChanges: [],
    skillsChange: null,
    selfEvolutionChange: null,
    graphsChange: null,
    runtimePatch: {},
    switchPermissionMode: null,
    switchAgentMode: null,
    switchModel: null,
  });
});

test("builds an apply plan with only changed config values", () => {
  const initial = createConfigPanelDraft({ session, memory, models });
  const draft: ConfigPanelDraft = {
    ...initial,
    memoryEnabled: false,
    browserEnabled: false,
    imageEnabled: true,
    skillsEnabled: false,
    selfEvolutionEnabled: false,
    graphsEnabled: false,
    disabledSkills: ["joke-expert"],
    agentMode: "team",
    modelName: "doubao_seed",
    modelEffort: "high",
  };

  const plan = buildConfigApplyPlan(initial, draft);

  assert.deepEqual(plan, {
    memoryChanges: [{ feature: "memory", enabled: false }],
    browserChanges: [{ enabled: false }],
    imageChanges: [{ enabled: true }],
    skillsChange: { enabled: false, disabled: ["joke-expert"] },
    selfEvolutionChange: { enabled: false },
    graphsChange: { enabled: false },
    runtimePatch: {
      agent_mode: "team",
      model_name: "doubao_seed",
      model_effort: "high",
    },
    switchPermissionMode: null,
    switchAgentMode: { agent_mode: "team", agent_type: "react" },
    switchModel: { model_name: "doubao_seed", model_effort: "high" },
  });
});

test("agent type cycles to CodeAct outside the agent profile", () => {
  const initial = createConfigPanelDraft({
    session: { ...session, agent_mode: "team" },
    memory,
    models,
  });

  const changed = changeConfigDraft(initial, "agent_type", "next", models);
  const plan = buildConfigApplyPlan(initial, changed);

  assert.equal(changed.agentMode, "team");
  assert.equal(changed.agentType, "codeact");
  assert.deepEqual(plan.runtimePatch, { agent_type: "codeact" });
  assert.equal(plan.switchAgentMode, null);
});

test("changing agent mode and agent type keeps the live switch on its current protocol", () => {
  const initial = createConfigPanelDraft({ session, memory, models });
  const draft = {
    ...initial,
    agentMode: "group",
    agentType: "codeact",
  };

  const plan = buildConfigApplyPlan(initial, draft);

  assert.deepEqual(plan.runtimePatch, { agent_mode: "group", agent_type: "codeact" });
  assert.deepEqual(plan.switchAgentMode, { agent_mode: "group", agent_type: "react" });
});

test("skills row toggles only the global skills flag and preserves disabled entries", () => {
  const initial = createConfigPanelDraft({ session, memory, models, skillsConfig });

  const skillsOff = changeConfigDraft(initial, "skills", "next", models);
  const plan = buildConfigApplyPlan(initial, skillsOff);

  assert.equal(skillsOff.skillsEnabled, false);
  assert.deepEqual(skillsOff.disabledSkills, []);
  assert.deepEqual(plan.skillsChange, { enabled: false, disabled: [] });
});

test("self evolution and graph rows persist only their own feature switches", () => {
  const initial = createConfigPanelDraft({ session, memory, models });
  const evolutionOff = changeConfigDraft(initial, "self_evolution", "next", models);
  const graphsOff = changeConfigDraft(evolutionOff, "graphs", "next", models);
  const plan = buildConfigApplyPlan(initial, graphsOff);

  assert.deepEqual(plan.selfEvolutionChange, { enabled: false });
  assert.deepEqual(plan.graphsChange, { enabled: false });
});

test("config panel does not expose mode as persistent runtime config", () => {
  const initial = createConfigPanelDraft({ session, memory, models });
  const draft: ConfigPanelDraft = {
    ...initial,
    permissionMode: "default", agentMode: "plan",
  };

  const model = buildConfigPanelModel({
    draft,
    initialDraft: initial,
    models,
    selectedIndex: 0,
  });
  const plan = buildConfigApplyPlan(initial, draft);

  assert.equal(
    model.rows.some((row) => String(row.id) === "mode" || row.label === "Mode"),
    false,
  );
  assert.equal("mode" in plan.runtimePatch, false);
  assert.equal(plan.switchPermissionMode, null);
});

test("builds the initial browser draft from workspace config", () => {
  const draft = createConfigPanelDraft({
    session,
    memory,
    models,
    workspaceConfig: { browser: { enabled: false } },
  });

  assert.equal(draft.browserEnabled, false);
});

test("builds the initial image draft from workspace config", () => {
  const draft = createConfigPanelDraft({
    session,
    memory,
    models,
    workspaceConfig: { image: { enabled: true } },
  });

  assert.equal(draft.imageEnabled, true);
});
