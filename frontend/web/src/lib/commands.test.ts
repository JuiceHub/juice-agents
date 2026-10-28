import test from "node:test";
import assert from "node:assert/strict";
import { executeWebCommand, intervalToCron } from "./commands.js";
import type { WebGatewayClient } from "../gateway/client.js";

test("web slash loop intervals use the same cron shorthand as cli", () => {
  assert.equal(intervalToCron("5m"), "*/5 * * * *");
  assert.equal(intervalToCron("2h"), "0 */2 * * *");
});

test("web legacy slash skill shortcut is rejected", async () => {
  const client = {} as WebGatewayClient;
  const result = await executeWebCommand("/code-review inspect this diff", client, {
    baseDir: "/tmp/work",
    status: null,
    sessions: [],
    models: [
      {
        model_name: "gpt4o_mini",
        backend: "openai",
        provider_model_name: "gpt-4o-mini",
        supported_efforts: ["disabled", "low", "medium", "high", "auto"],
      },
    ],
  });

  assert.equal(result.forwardMessage, undefined);
  assert.match(result.blocks[0]?.text || "", /Unknown command/);
  assert.equal(result.clearMessages, false);
});

test("web slash model command returns a runtime switch request", async () => {
  const result = await executeWebCommand("/model gpt4o_mini high", {} as WebGatewayClient, {
    baseDir: "/tmp/work",
    status: null,
    sessions: [],
    models: [],
  });

  assert.deepEqual(result.runtimeChange, {
    modelName: "gpt4o_mini",
    modelEffort: "high",
  });
});

test("web slash model command rejects effort unsupported by target model", async () => {
  const result = await executeWebCommand("/model claude-sonnet-5-5 xhigh", {} as WebGatewayClient, {
    baseDir: "/tmp/work",
    status: null,
    sessions: [],
    models: [
      {
        model_name: "claude-sonnet-5-5",
        backend: "anthropic",
        provider_model_name: "claude-sonnet-5-5",
        supported_efforts: ["disabled", "low", "medium", "high", "max"],
      },
    ],
  });

  assert.equal(result.runtimeChange, undefined);
  assert.match(result.blocks[0]?.text || "", /does not support effort xhigh/);
});

test("web /agents lists and views available agents", async () => {
  const client = {
    listAvailableAgents: async ({ name }: { name?: string } = {}) => ({
      mode_id: "agent",
      agents: [
        {
          name: name || "general",
          description: "General subagent",
          allowed_modes: null,
          source: "builtin",
          config: { agent_type: "react", tools: ["shell", "read", "edit"] },
        },
      ],
    }),
  } as unknown as WebGatewayClient;

  const listed = await executeWebCommand("/agents", client, {
    baseDir: "/tmp/work",
    status: null,
    sessions: [],
    models: [],
  });
  const viewed = await executeWebCommand("/agents general", client, {
    baseDir: "/tmp/work",
    status: null,
    sessions: [],
    models: [],
  });

  assert.match(listed.blocks[0]?.text || "", /general/);
  assert.equal(viewed.blocks[0]?.title, "Agent general");
});

test("web /teams lists and views Team manifests", async () => {
  const calls: unknown[] = [];
  const client = {
    listTeams: async (params: unknown) => {
      calls.push(["listTeams", params]);
      return {
        teams: [{
          team_name: "default",
          description: "Default delivery Team",
          member_names: ["researcher", "developer"],
          source: "builtin",
          manifest_path: null,
        }],
      };
    },
    getTeam: async (params: { team_name: string }) => {
      calls.push(["getTeam", params]);
      return {
        schema_version: 2,
        source: "builtin",
        manifest_path: null,
        team_name: params.team_name,
        description: "Default delivery Team",
        member_names: ["researcher", "developer"],
      };
    },
  } as unknown as WebGatewayClient;
  const context = { baseDir: "/tmp/work", status: null, sessions: [], models: [] };

  const listed = await executeWebCommand("/teams", client, context);
  const viewed = await executeWebCommand("/teams default", client, context);

  assert.equal(listed.blocks[0]?.title, "Teams");
  assert.equal(listed.blocks[0]?.display?.type, "table");
  assert.equal(viewed.blocks[0]?.title, "Team default");
  assert.equal(viewed.blocks[0]?.display?.type, "kv");
  assert.deepEqual(calls, [
    ["listTeams", { base_dir: "/tmp/work" }],
    ["getTeam", { team_name: "default", base_dir: "/tmp/work" }],
  ]);
});

test("web /agent-type only saves the future default and keeps active status unchanged", async () => {
  const calls: any[] = [];
  const client = {
    saveWorkspaceConfig: async (baseDir: string, config: Record<string, unknown>) => {
      calls.push(["saveWorkspaceConfig", baseDir, config]);
      return { saved: true };
    },
  } as unknown as WebGatewayClient;

  const result = await executeWebCommand("/agent-type codeact", client, {
    baseDir: "/tmp/work",
    status: {
      runner_id: "runner-1",
      permission_mode: "default",
      agent_mode: "team",
      root_agent_name: "teamlead",
      base_dir: "/tmp/work",
      started: true,
      resumed: false,
      agent_type: "react",
      model_name: "gpt4o_mini",
      model_effort: "disabled",
      backend: "openai",
      provider_model_name: "gpt-4o-mini",
    },
    sessions: [],
    models: [],
  });

  assert.deepEqual(calls, [["saveWorkspaceConfig", "/tmp/work", { runtime: { agent_type: "codeact" } }]]);
  assert.equal(result.status, undefined);
  assert.equal(result.workspaceAgentType, "codeact");
  assert.match(result.blocks[0]?.text || "", /Existing live actors are unchanged/);
  assert.match(result.blocks[0]?.text || "", /Newly created default Agents will use codeact/);
});

test("web /skills lists skills and treats the first argument as the skill name", async () => {
  const calls: any[] = [];
  const client = {
    listSkills: async () => ({
      success: true,
      skills: [
        {
          name: "joke-expert",
          qualified_name: "joke-expert",
          description: "Tell jokes",
          source: "builtin",
          read_only: true,
        },
      ],
      categories: [],
      category_descriptions: {},
      count: 1,
    }),
    viewSkill: async (name: string, filePath = "") => {
      calls.push(["viewSkill", name, filePath]);
      return {
        success: true,
        name,
        qualified_name: name,
        content: filePath ? "api reference" : "# Skill",
      };
    },
  } as unknown as WebGatewayClient;

  const listed = await executeWebCommand("/skills", client, {
    baseDir: "/tmp/work",
    status: null,
    sessions: [],
    models: [],
  });
  const viewed = await executeWebCommand("/skills joke-expert references/api.md", client, {
    baseDir: "/tmp/work",
    status: null,
    sessions: [],
    models: [],
  });

  assert.match(listed.blocks[0]?.text || "", /joke-expert\s+builtin\s+Tell jokes/);
  assert.equal(listed.blocks[0]?.display?.type, "table");
  assert.match(viewed.blocks[0]?.text || "", /api reference/);
  assert.equal(viewed.blocks[0]?.display?.type, "code");
  assert.deepEqual(calls, [["viewSkill", "joke-expert", "references/api.md"]]);
});

test("web /plugins lists and views read-only plugins", async () => {
  const client = {
    listPlugins: async () => ({
      success: true,
      plugins: [{ name: "review-pack", source: "workspace", description: "Review skills", skill_names: ["review"] }],
      diagnostics: [],
      count: 1,
    }),
    viewPlugin: async (name: string, filePath = "") => ({ success: true, name, file_path: filePath, content: "plugin file" }),
  } as unknown as WebGatewayClient;

  const context = { baseDir: "/tmp/work", status: null, sessions: [], models: [] };
  const listed = await executeWebCommand("/plugins", client, context);
  const viewed = await executeWebCommand("/plugins review-pack skills/review/SKILL.md", client, context);

  assert.match(listed.blocks[0]?.text || "", /review-pack/);
  assert.match(viewed.blocks[0]?.text || "", /plugin file/);
});

test("web /permissions uses permission gateway helper", async () => {
  const client = {
    permissionStatus: async () => ({ permission_mode: "default", workspace_rules: [], session_rules: [] }),
  } as unknown as WebGatewayClient;
  const context = { baseDir: "/tmp/work", status: null, sessions: [], models: [] };

  const permissions = await executeWebCommand("/permissions", client, context);

  assert.match(permissions.blocks[0]?.text || "", /default/);
});


test("retired /resources is absent from Web help and command dispatch", async () => {
  const context = { baseDir: "/tmp/work", status: null, sessions: [], models: [] };
  const client = {} as WebGatewayClient;
  const help = await executeWebCommand("/help", client, context);
  assert.doesNotMatch(help.blocks[0]?.text || "", /\/resources/);
  const result = await executeWebCommand("/resources group", client, context);
  assert.match(result.blocks[0]?.text || "", /Unknown command/);
  assert.equal(result.forwardMessage, undefined);
});
