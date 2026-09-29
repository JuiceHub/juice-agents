import assert from "node:assert/strict";
import test from "node:test";

import {
  messageBlockToText,
  presentAgentTypeConfigSaved,
  presentAvailableAgents,
  presentTeamManifest,
  presentTeams,
  presentCronTasks,
  presentGoal,
  presentMemoryStatus,
  presentModels,
  presentSessions,
  presentSkills,
  presentStatus,
} from "./command.js";

test("agent type save feedback distinguishes workspace defaults from live actors", () => {
  const block = presentAgentTypeConfigSaved("codeact");

  assert.match(block.text, /Workspace default agent_type: codeact/);
  assert.match(block.text, /Existing live actors are unchanged/);
  assert.match(block.text, /Newly created default Agents will use codeact/);
});

test("list-style command presenters return table display and text fallback", () => {
  const models = presentModels([
    { model_name: "gpt4o_mini", backend: "openai", provider_model_name: "gpt-4o-mini" },
  ]);
  const sessions = presentSessions([
    {
      runner_id: "runner-1",
      permission_mode: "default",
      agent_mode: "agent",
      root_actor_name: "root",
      updated_at: "2026-05-20T00:00:00Z",
      root_dir: "/tmp/work/.juice/runners/runner-1",
      first_user_request_preview: "Implement readable runner titles",
    },
  ]);
  const skills = presentSkills({
    success: true,
    skills: [
      {
        name: "code-review",
        qualified_name: "workflow/code-review",
        description: "Review code",
        source: "builtin",
        read_only: true,
      },
    ],
    categories: [],
    category_descriptions: {},
    count: 1,
  });
  const agents = presentAvailableAgents({
    mode_id: "agent",
    agents: [
      {
        name: "general",
        description: "General subagent",
        allowed_modes: null,
        source: "builtin",
        config: { agent_type: "react", tools: ["read", "edit"] },
      },
      {
        name: "explore",
        description: "Read-only exploration",
        allowed_modes: ["agent", "plan"],
        source: "builtin",
        config: { agent_type: "react", tools: ["read", "glob", "grep"] },
      },
    ],
  });
  const cron = presentCronTasks({
    tasks: [
      {
        id: "cron1234",
        cron: "*/5 * * * *",
        prompt: "check deploy",
        recurring: true,
        created_at: 1000,
        next_run_at: 1200,
      },
    ],
  });
  const teams = presentTeams({
    teams: [{
      team_name: "default",
      description: "Default delivery Team",
      member_names: ["researcher", "developer"],
      manifest_path: "/tmp/work/.juice/teams/default/manifest.yaml",
    }],
  });

  for (const block of [models, sessions, skills, agents, cron, teams]) {
    assert.equal(block.display?.type, "table");
    assert.notEqual(messageBlockToText(block).trim(), "");
  }
  assert.match(sessions.text, /Implement readable runner titles/);
});

test("Team detail presents only global member-name references", () => {
  const team = presentTeamManifest({
    schema_version: 2,
    team_name: "default",
    description: "Default delivery Team",
    member_names: ["researcher", "developer"],
  });

  assert.equal(team.title, "Team default");
  assert.equal(team.display?.type, "kv");
  assert.match(team.text, /member_names: researcher, developer/);
});

test("status-style command presenters return key-value display", () => {
  const status = presentStatus({
    runner_id: "runner-123",
    permission_mode: "default",
    agent_mode: "agent",
    root_actor_name: "root",
    base_dir: "/tmp/work",
    started: true,
    resumed: false,
    agent_type: "react",
    model_name: "gpt4o_mini",
    model_effort: "high",
    backend: "openai",
    provider_model_name: "gpt-4o-mini",
  });
  const memory = presentMemoryStatus({
    enabled: true,
    dream_enabled: false,
    memory_dir: "/tmp/work/.juice/memory",
    entrypoint: "/tmp/work/.juice/memory/MEMORY.md",
    topic_count: 2,
  });
  const goal = presentGoal({
    objective: "ship",
    status: "active",
    turns_used: 1,
    max_turns: 12,
    elapsed_seconds: 5,
    max_runtime_seconds: 900,
    latest_evaluator_reason: "continue",
  });

  for (const block of [status, memory, goal]) {
    assert.equal(block.display?.type, "kv");
    assert.notEqual(messageBlockToText(block).trim(), "");
  }
});

test("empty command lists keep plain empty-state text", () => {
  const models = presentModels([]);
  const skills = presentSkills({
    success: true,
    skills: [],
    categories: [],
    category_descriptions: {},
    count: 0,
  });

  assert.equal(models.display, undefined);
  assert.equal(models.text, "No models found.");
  assert.equal(skills.display, undefined);
  assert.equal(skills.text, "No skills found.");
});
