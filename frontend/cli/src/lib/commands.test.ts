import assert from "node:assert/strict";
import test from "node:test";

import { executeCommand, intervalToCron, parseLoopCommand } from "./commands.js";

function createClient() {
  let status = {
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
    worktree: null as any,
  };
  const calls: any[] = [];
  const client: any = {
    stopped: false,
    calls,
    describeSession: async () => status,
    listModels: async () => [
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
        model_name: "claude-sonnet-4-6",
        backend: "anthropic",
        provider_model_name: "claude-sonnet-4-6",
        supported_efforts: ["disabled", "low", "medium", "high", "max"],
      },
    ],
    switchPermissionMode: async ({ permission_mode }: { permission_mode: string }) => {
      calls.push(["switchPermissionMode", { permission_mode }]);
      status = {
        ...status,
        runner_id: "runner-1",
        permission_mode,
      };
      return status;
    },
    switchAgentMode: async ({ agent_mode, agent_type }: { agent_mode: string; agent_type?: string }) => {
      calls.push(["switchAgentMode", { agent_mode, agent_type }]);
      status = {
        ...status,
        runner_id: "runner-1",
        agent_mode,
        agent_type: agent_type || status.agent_type,
      };
      return status;
    },
    switchModel: async ({ model_name, model_effort }: { model_name: string; model_effort?: string }) => {
      calls.push(["switchModel", { model_name, model_effort }]);
      status = {
        ...status,
        runner_id: "runner-1",
        started: true,
        resumed: false,
        model_name,
        model_effort: model_effort ?? status.model_effort,
        backend: model_name === "doubao_seed" ? "doubao" : "openai",
        provider_model_name:
          model_name === "doubao_seed" ? "doubao-seed-1-6" : "gpt-4o-mini",
      };
      return status;
    },
    stopSession: async () => {
      (client as any).stopped = true;
      return { stopped: true };
    },
    listSkills: async () => ({
      mode_id: "agent",
      skills: [
        {
          name: "joke-expert",
          qualified_name: "joke-expert",
          description: "Tell jokes",
          category: null,
          source: "builtin",
          read_only: true,
        },
      ],
      categories: [],
      category_descriptions: {},
      count: 1,
    }),
    listPlugins: async () => ({
      success: true,
      plugins: [
        {
          schema_version: 1,
          name: "review-pack",
          version: "0.1.0",
          description: "Review skills",
          root: "/plugins/review-pack",
          source: "workspace",
          enabled: true,
          skill_roots: ["/plugins/review-pack/skills"],
          skill_names: ["review"],
        },
      ],
      diagnostics: [],
      count: 1,
    }),
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
        ...(!name
          ? [
              {
                name: "explore",
                description: "Read-only exploration subagent",
                allowed_modes: ["agent", "plan"],
                source: "builtin",
                config: { agent_type: "react", tools: ["read", "glob", "grep"] },
              },
            ]
          : []),
      ],
    }),
    listTeams: async ({ base_dir }: { base_dir?: string } = {}) => {
      calls.push(["listTeams", { base_dir }]);
      return {
        teams: [{
          team_name: "default",
          description: "Default delivery Team",
          member_names: ["researcher", "developer"],
          manifest_path: "/tmp/workspace/.juice/teams/default/manifest.yaml",
        }],
      };
    },
    getTeam: async ({ team_name, base_dir }: { team_name: string; base_dir?: string }) => {
      calls.push(["getTeam", { team_name, base_dir }]);
      return {
        schema_version: 2,
        team_name,
        description: "Default delivery Team",
        member_names: ["researcher", "developer"],
      };
    },
    listModeResources: async ({ mode_id }: { mode_id?: string } = {}) => ({
      mode_ids: ["agent", "team", "group"],
      resources: [
        {
          mode_id: mode_id || "group",
          role: "worker",
          name: "research_worker",
          source: "builtin",
          path: ".juice/agents/group/workers/research_worker.yaml",
          description: "Research worker",
          status: "available",
        },
      ],
      count: 1,
    }),
    listGraphs: async () => ({ graphs: [{ name: "deep_research", source: "builtin" }], count: 1 }),
    viewGraph: async ({ name }: { name: string }) => ({ metadata: { name }, content: "GRAPH_METADATA = {}" }),
    runGraph: async ({ name, payload }: { name: string; payload: Record<string, any> }) => ({ manifest: { graph_name: name, status: "completed" }, result: payload }),
    listGraphRuns: async () => ({ runs: [{ graph_run_id: "graph-1", status: "completed" }], count: 1 }),
    controlGraphRun: async ({ graph_run_id, action }: { graph_run_id: string; action: string }) => ({ manifest: { graph_run_id, requested_status: action } }),
    graphsConfigStatus: async () => ({ success: true, enabled: true }),
    permissionStatus: async () => ({ permission_mode: "default", workspace_rules: [], session_rules: [] }),
    viewSkill: async ({ name, file_path }: { name: string; file_path?: string }) => ({
      success: true,
      name,
      qualified_name: name,
      content: file_path ? "api reference" : "# Joke Expert",
      raw_content: "",
      path: "/skills/joke-expert/SKILL.md",
      skill_dir: "/skills/joke-expert",
      metadata: { name },
      linked_files: {},
      available_files: ["SKILL.md", "references/api.md"],
    }),
    viewPlugin: async ({ name, file_path }: { name: string; file_path?: string }) => ({
      success: true,
      name,
      ...(file_path ? { file_path, content: "plugin file" } : { plugin: { name, skill_names: ["review"] } }),
    }),
    memoryStatus: async () => ({
      enabled: true,
      dream_enabled: true,
      memory_dir: "/tmp/workspace/.juice/memory",
      entrypoint: "/tmp/workspace/.juice/memory/MEMORY.md",
      topic_count: 1,
      last_dream_at: null,
    }),
    memorySearch: async ({ query }: { query: string }) => ({
      enabled: true,
      hits: [{ path: "topics/project-conventions.md", line: 7, snippet: `Use ${query}` }],
    }),
    memoryView: async ({ path }: { path?: string } = {}) => ({
      enabled: true,
      path: path || "MEMORY.md",
      content: "# Workspace Memory\n",
    }),
    runDream: async () => ({
      status: "launched",
      kind: "memory_dream",
      async_task_id: "memory_dream_root_1234",
      summary: "memory_dream 已启动",
      output_dir: "/tmp/memory_dream_root_1234",
    }),
    createCronTask: async ({ cron, prompt, recurring }: { cron: string; prompt: string; recurring?: boolean }) => {
      calls.push(["createCronTask", { cron, prompt, recurring }]);
      return {
        id: "cron1234",
        cron,
        prompt,
        recurring: Boolean(recurring),
        created_at: 1000,
        next_run_at: 1200,
      };
    },
    listCronTasks: async () => ({
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
    }),
    deleteCronTask: async ({ id }: { id: string }) => {
      calls.push(["deleteCronTask", { id }]);
      return { deleted: true, id };
    },
    cronStatus: async () => ({
      enabled: true,
      tasks_path: "/tmp/workspace/.juice/scheduled_tasks.json",
      lock_path: "/tmp/workspace/.juice/scheduled_tasks.lock",
      task_count: 1,
      next_run_at: 1200,
      lock_present: false,
    }),
    getGoal: async () => ({
      objective: "ship goal",
      status: "active",
      turns_used: 1,
      max_turns: 12,
      elapsed_seconds: 5,
      max_runtime_seconds: 900,
      latest_evaluator_reason: "continue",
      pause_reason: "",
    }),
    setGoal: async ({ objective }: { objective: string }) => {
      calls.push(["setGoal", { objective }]);
      return {
        objective,
        status: "active",
        turns_used: 0,
        max_turns: 12,
        elapsed_seconds: 0,
        max_runtime_seconds: 900,
        latest_evaluator_reason: "",
        pause_reason: "",
      };
    },
    pauseGoal: async () => {
      calls.push(["pauseGoal", {}]);
      return {
        objective: "ship goal",
        status: "paused",
        turns_used: 1,
        max_turns: 12,
        elapsed_seconds: 5,
        max_runtime_seconds: 900,
        latest_evaluator_reason: "continue",
        pause_reason: "user_paused",
      };
    },
    resumeGoal: async () => {
      calls.push(["resumeGoal", {}]);
      return {
        objective: "ship goal",
        status: "active",
        turns_used: 1,
        max_turns: 12,
        elapsed_seconds: 5,
        max_runtime_seconds: 900,
        latest_evaluator_reason: "continue",
        pause_reason: "",
      };
    },
    clearGoal: async () => {
      calls.push(["clearGoal", {}]);
      return { status: "cleared" };
    },
    enterWorktree: async ({ name }: { name?: string } = {}) => {
      calls.push(["enterWorktree", { name }]);
      status = {
        ...status,
        worktree: {
          name: name || "task-123",
          slug: name || "task-123",
          path: `/tmp/workspace/.juice/worktrees/${name || "task-123"}`,
          branch: `worktree-${name || "task-123"}`,
          base_sha: "base",
          head_sha: "head",
          dirty: false,
          ahead: 0,
          exists: true,
          active: true,
          temporary: false,
          error: "",
        },
      };
      return status.worktree;
    },
    exitWorktree: async ({ discard }: { discard?: boolean } = {}) => {
      calls.push(["exitWorktree", { discard: Boolean(discard) }]);
      return {
        name: "feature-x",
        slug: "feature-x",
        path: "/tmp/workspace/.juice/worktrees/feature-x",
        branch: "worktree-feature-x",
        base_sha: "base",
        head_sha: "head",
        dirty: false,
        ahead: 0,
        exists: false,
        active: false,
        temporary: false,
        error: "",
      };
    },
    listWorktrees: async () => ({
      worktrees: [
        {
          name: "feature-x",
          slug: "feature-x",
          path: "/tmp/workspace/.juice/worktrees/feature-x",
          branch: "worktree-feature-x",
          base_sha: "base",
          head_sha: "head",
          dirty: false,
          ahead: 0,
          exists: true,
          active: true,
          temporary: false,
          error: "",
        },
      ],
      count: 1,
    }),
    worktreeStatus: async ({ name }: { name?: string } = {}) => {
      calls.push(["worktreeStatus", { name }]);
      return {
        name: name || "feature-x",
        slug: name || "feature-x",
        path: `/tmp/workspace/.juice/worktrees/${name || "feature-x"}`,
        branch: `worktree-${name || "feature-x"}`,
        base_sha: "base",
        head_sha: "head",
        dirty: false,
        ahead: 0,
        exists: true,
        active: true,
        temporary: false,
        error: "",
      };
    },
    setMemoryConfig: async ({ feature, enabled }: { feature: string; enabled: boolean }) => {
      calls.push(["setMemoryConfig", { feature, enabled }]);
      return {
        enabled: feature === "memory" ? enabled : true,
        dream_enabled: feature === "dream" ? enabled : true,
        memory_dir: "/tmp/workspace/.juice/memory",
        entrypoint: "/tmp/workspace/.juice/memory/MEMORY.md",
        topic_count: 1,
        last_dream_at: null,
      };
    },
    saveWorkspaceConfig: async ({ config }: { config: Record<string, any> }) => {
      calls.push(["saveWorkspaceConfig", config]);
      return { saved: true };
    },
  };
  return client;
}

function createPendingContext(runtime: Record<string, string> = {}) {
  return {
    sessionStatus: null,
    pendingRuntime: {
      base_dir: "/tmp/workspace",
      permission_mode: "default",
      agent_mode: "agent",
      agent_type: "react",
      model_name: "gpt4o_mini",
      model_effort: "disabled",
      ...runtime,
    },
  };
}

test("/models lists available runtime models", async () => {
  const client = createClient();

  const result = await executeCommand("/models", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /gpt4o_mini/);
  assert.match(result.blocks[0]?.text || "", /doubao_seed/);
  assert.match(result.blocks[0]?.text || "", /gpt-4o-mini/);
  assert.equal(result.blocks[0]?.display?.type, "table");
});

test("/config opens the config panel", async () => {
  const client = createClient();

  const result = await executeCommand("/config", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 0);
  assert.equal(result.interactiveAction, "config-panel");
});

test("/tasks opens the async tasks panel", async () => {
  const client = createClient();
  const result = await executeCommand("/tasks", client, "/tmp/workspace");
  assert.equal(result.interactiveAction, "tasks-panel");
});

test("/actors opens the actor selector", async () => {
  const client = createClient();
  const result = await executeCommand("/actors", client, "/tmp/workspace");
  assert.equal(result.interactiveAction, "actor-selector");
});

test("/config still opens in cold start without forcing a runner", async () => {
  const client = createClient();

  const result = await executeCommand(
    "/config",
    client,
    "/tmp/workspace",
    createPendingContext(),
  );

  assert.equal(result.blocks.length, 0);
  assert.equal(result.interactiveAction, "config-panel");
  assert.deepEqual(client.calls, []);
});

test("/help lists the config command", async () => {
  const client = createClient();

  const result = await executeCommand("/help", client, "/tmp/workspace");

  assert.match(result.blocks[0]?.text || "", /\/config/);
  assert.match(result.blocks[0]?.text || "", /\/agents \[name\]/);
  assert.match(result.blocks[0]?.text || "", /\/teams \[name\]/);
  assert.match(result.blocks[0]?.text || "", /\/mode \[agent\|plan\|team\|group\]/);
  assert.match(result.blocks[0]?.text || "", /\/mode \[agent\|plan\|team\|group\]/);
  assert.match(result.blocks[0]?.text || "", /\/resources \[agent\|team\|group\]/);
  assert.match(result.blocks[0]?.text || "", /\/worktree \[enter\|exit\|list\|status\] \[name\]/);
});

test("/model without arguments opens the model selector", async () => {
  const client = createClient();

  const result = await executeCommand("/model", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 0);
  assert.equal(result.interactiveAction, "model-selector");
});

test("/resources lists mode-scoped resources", async () => {
  const client = createClient();

  const result = await executeCommand("/resources group", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /research_worker/);
  assert.match(result.blocks[0]?.text || "", /\.juice\/agents\/group\/workers/);
});

test("/model <name> switches the current runtime model", async () => {
  const client = createClient();

  const result = await executeCommand("/model doubao_seed", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /model_name: doubao_seed/);
  assert.match(result.blocks[0]?.text || "", /runner_id: runner-1/);
  assert.match(result.blocks[0]?.text || "", /backend: doubao/);
  assert.match(result.blocks[0]?.text || "", /provider_model_name: doubao-seed-1-6/);
});

test("/model <name> <effort> switches model and effort together", async () => {
  const client = createClient();

  const result = await executeCommand("/model doubao_seed high", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.deepEqual(client.calls[0], [
    "switchModel",
    { model_name: "doubao_seed", model_effort: "high" },
  ]);
  assert.match(result.blocks[0]?.text || "", /model_name: doubao_seed/);
  assert.match(result.blocks[0]?.text || "", /model_effort: high/);
});

test("/model <name> in cold start only updates pending runtime and workspace config", async () => {
  const client = createClient();

  const result = await executeCommand(
    "/model doubao_seed high",
    client,
    "/tmp/workspace",
    createPendingContext(),
  );

  assert.equal(result.blocks.length, 1);
  assert.equal(result.pendingRuntime?.model_name, "doubao_seed");
  assert.equal(result.pendingRuntime?.model_effort, "high");
  assert.deepEqual(client.calls, [
    ["saveWorkspaceConfig", { runtime: { model_name: "doubao_seed", model_effort: "high" } }],
  ]);
  assert.match(result.blocks[0]?.text || "", /active_session: no/);
  assert.match(result.blocks[0]?.text || "", /pending_model_name: doubao_seed/);
});

test("/model rejects invalid effort values", async () => {
  const client = createClient();

  const result = await executeCommand("/model doubao_seed extreme", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.equal(client.calls.length, 0);
  assert.match(result.blocks[0]?.text || "", /Invalid model effort: extreme/);
});

test("/model rejects effort values unsupported by the target model", async () => {
  const client = createClient();

  const result = await executeCommand("/model claude-sonnet-4-6 xhigh", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.equal(client.calls.length, 0);
  assert.match(result.blocks[0]?.text || "", /does not support effort xhigh/);
  assert.match(result.blocks[0]?.text || "", /disabled, low, medium, high, max/);
});

test("/mode <name> returns an explicit coordinated switch request", async () => {
  const client = createClient();

  const result = await executeCommand("/mode plan", client, "/tmp/workspace");

  // plan 属执行模式维度：直接重建 root agent，不走 latest-wins coordinator。
  assert.equal(result.permissionModeSwitchRequest, undefined);
  assert.deepEqual(
    client.calls.filter(([name]: [string, unknown]) => name === "switchAgentMode"),
    [["switchAgentMode", { agent_mode: "plan", agent_type: "react" }]],
  );
});

test("/mode <name> in cold start only updates pending mode", async () => {
  const client = createClient();

  const result = await executeCommand(
    "/mode plan",
    client,
    "/tmp/workspace",
    createPendingContext(),
  );

  // plan 属执行模式维度，冷启动下会落 pending 运行态并持久化 workspace 默认值。
  assert.equal(result.pendingRuntime?.agent_mode, "plan");
  assert.deepEqual(client.calls, [
    ["saveWorkspaceConfig", { runtime: { agent_mode: "plan" } }],
  ]);
});

test("bare /mode and /permissions each open their own selector", async () => {
  const client = createClient();

  const mode = await executeCommand("/mode", client, "/tmp/workspace");
  const permissions = await executeCommand("/permissions", client, "/tmp/workspace");

  // 两个维度各自独立的 selector，互不复用。
  assert.equal(mode.interactiveAction, "mode-selector");
  assert.equal(permissions.interactiveAction, "permissions-selector");
  assert.deepEqual(client.calls, []);
});

test("/permissions status stays read-only", async () => {
  const client = createClient();

  const result = await executeCommand("/permissions status", client, "/tmp/workspace");

  assert.equal(result.permissionModeSwitchRequest, undefined);
  assert.equal(result.interactiveAction, undefined);
});

test("/permissions rejects agent mode values", async () => {
  const client = createClient();

  const result = await executeCommand("/permissions team", client, "/tmp/workspace");

  assert.match(result.blocks[0]?.text || "", /Invalid permission mode: team/);
  assert.match(result.blocks[0]?.text || "", /\/mode/);
  assert.deepEqual(client.calls, []);
});

test("/mode rejects values outside the agent mode dimension", async () => {
  const client = createClient();

  const result = await executeCommand("/mode nonsense", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /Invalid agent mode: nonsense/);
  assert.deepEqual(client.calls, []);
});

test("/mode <agent_mode> switches agent mode and saves workspace config", async () => {
  const client = createClient();

  const result = await executeCommand("/mode team", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.deepEqual(client.calls[0], [
    "switchAgentMode",
    { agent_mode: "team", agent_type: "react" },
  ]);
  assert.deepEqual(client.calls[1], [
    "saveWorkspaceConfig",
    { runtime: { agent_mode: "team" } },
  ]);
  assert.match(result.blocks[0]?.text || "", /permission_mode: default/);
  assert.match(result.blocks[0]?.text || "", /agent_mode: team/);
});

test("/permissions <mode> defers to the latest-wins coordinator", async () => {
  const client = createClient();

  const result = await executeCommand("/permissions accept", client, "/tmp/workspace");

  // 权限模式不重建 root agent，交由 coordinator 在 step 边界应用。
  // 持久化归 coordinator 统一负责，因此命令层自身不发 RPC。
  assert.deepEqual(result.permissionModeSwitchRequest, { permission_mode: "accept" });
  assert.deepEqual(client.calls, []);
});

test("/permissions in cold start persists the pending permission mode", async () => {
  const client = createClient();

  const result = await executeCommand(
    "/permissions accept",
    client,
    "/tmp/workspace",
    createPendingContext(),
  );

  assert.equal(result.pendingRuntime?.permission_mode, "accept");
  assert.deepEqual(client.calls, [
    ["saveWorkspaceConfig", { runtime: { permission_mode: "accept" } }],
  ]);
});

test("/mode rejects permission mode values", async () => {
  const client = createClient();

  const result = await executeCommand("/mode accept", client, "/tmp/workspace");

  assert.match(result.blocks[0]?.text || "", /Invalid agent mode: accept/);
  assert.match(result.blocks[0]?.text || "", /\/permissions/);
  assert.deepEqual(client.calls, []);
});

test("/mode <agent_mode> in cold start only saves the pending agent mode", async () => {
  const client = createClient();

  const result = await executeCommand(
    "/mode team",
    client,
    "/tmp/workspace",
    createPendingContext(),
  );

  assert.equal(result.pendingRuntime?.agent_mode, "team");
  assert.equal(result.pendingRuntime?.agent_type, "react");
  assert.deepEqual(client.calls, [
    ["saveWorkspaceConfig", { runtime: { agent_mode: "team" } }],
  ]);
  assert.match(result.blocks[0]?.text || "", /pending_agent_mode: team/);
});

test("/mode <agent_mode> in cold start preserves pending codeact agent type", async () => {
  const client = createClient();

  const result = await executeCommand(
    "/mode team",
    client,
    "/tmp/workspace",
    createPendingContext({ agent_type: "codeact" }),
  );

  assert.equal(result.pendingRuntime?.agent_mode, "team");
  assert.equal(result.pendingRuntime?.agent_type, "codeact");
  assert.deepEqual(client.calls, [
    ["saveWorkspaceConfig", { runtime: { agent_mode: "team" } }],
  ]);
});

test("/team <task> forwards one task with a temporary mode override", async () => {
  const client = createClient();

  const result = await executeCommand("/team design the API", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 0);
  assert.equal(result.forwardMessage, "design the API");
  assert.equal(result.forwardAgentMode, "team");
  assert.equal(client.calls.length, 0);
});

test("/agent-type <name> only saves the future default and keeps the live actor unchanged", async () => {
  const client = createClient();

  const result = await executeCommand("/agent-type codeact", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.equal(result.pendingRuntime?.agent_type, "codeact");
  assert.deepEqual(client.calls, [
    ["saveWorkspaceConfig", { runtime: { agent_type: "codeact" } }],
  ]);
  assert.match(result.blocks[0]?.text || "", /Existing live actors are unchanged/);
  assert.match(result.blocks[0]?.text || "", /Newly created default Agents will use codeact/);
  assert.equal(result.syncSession, undefined);
});

test("/agent-type <name> in cold start only saves the pending agent type", async () => {
  const client = createClient();

  const result = await executeCommand(
    "/agent-type codeact",
    client,
    "/tmp/workspace",
    createPendingContext(),
  );

  assert.equal(result.pendingRuntime?.agent_type, "codeact");
  assert.deepEqual(client.calls, [
    ["saveWorkspaceConfig", { runtime: { agent_type: "codeact" } }],
  ]);
  assert.match(result.blocks[0]?.text || "", /Workspace default agent_type: codeact/);
  assert.match(result.blocks[0]?.text || "", /Existing live actors are unchanged/);
});

test("/worktree enter creates or switches the active worktree", async () => {
  const client = createClient();

  const result = await executeCommand("/worktree enter feature-x", client, "/tmp/workspace");

  assert.deepEqual(client.calls.at(-1), ["enterWorktree", { name: "feature-x" }]);
  assert.equal(result.syncSession, true);
  assert.match(result.blocks[0]?.text || "", /branch: worktree-feature-x/);
});

test("/worktree list displays managed worktrees", async () => {
  const client = createClient();

  const result = await executeCommand("/worktree list", client, "/tmp/workspace");

  assert.match(result.blocks[0]?.text || "", /feature-x/);
  assert.equal(result.blocks[0]?.display?.type, "table");
});

test("/worktree status accepts an optional name", async () => {
  const client = createClient();

  const result = await executeCommand("/worktree status feature-x", client, "/tmp/workspace");

  assert.deepEqual(client.calls.at(-1), ["worktreeStatus", { name: "feature-x" }]);
  assert.match(result.blocks[0]?.text || "", /active: yes/);
});

test("/worktree exit supports discard", async () => {
  const client = createClient();

  const result = await executeCommand("/worktree exit --discard", client, "/tmp/workspace");

  assert.deepEqual(client.calls.at(-1), ["exitWorktree", { discard: true }]);
  assert.equal(result.syncSession, true);
  assert.match(result.blocks[0]?.text || "", /exists: no/);
});

test("/exit stops the active backend session before exiting", async () => {
  const client = createClient();

  const result = await executeCommand("/exit", client, "/tmp/workspace");

  assert.equal(result.exitRequested, true);
  assert.equal((client as any).stopped, true);
});

test("/agents displays availability-filtered agents", async () => {
  const client = createClient();

  const result = await executeCommand("/agents", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /general/);
  assert.match(result.blocks[0]?.text || "", /explore/);
  assert.equal(result.blocks[0]?.display?.type, "table");
});

test("/agents <name> displays available-agent detail", async () => {
  const client = createClient();

  const result = await executeCommand("/agents general", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.equal(result.blocks[0]?.title, "Agent general");
  assert.match(result.blocks[0]?.text || "", /"tools":\["shell","read","edit"\]/);
  assert.equal(result.blocks[0]?.display?.type, "kv");
});

test("/teams lists and views Team manifests", async () => {
  const client = createClient();

  const listed = await executeCommand("/teams", client, "/tmp/workspace");
  const viewed = await executeCommand("/teams default", client, "/tmp/workspace");

  assert.equal(listed.blocks[0]?.title, "Teams");
  assert.equal(listed.blocks[0]?.display?.type, "table");
  assert.match(listed.blocks[0]?.text || "", /researcher, developer/);
  assert.equal(viewed.blocks[0]?.title, "Team default");
  assert.equal(viewed.blocks[0]?.display?.type, "kv");
  assert.deepEqual(client.calls.slice(-2), [
    ["listTeams", { base_dir: "/tmp/workspace" }],
    ["getTeam", { team_name: "default", base_dir: "/tmp/workspace" }],
  ]);
});

test("/skills displays visible skills", async () => {
  const client = createClient();

  const result = await executeCommand("/skills", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /joke-expert/);
  assert.match(result.blocks[0]?.text || "", /Tell jokes/);
  assert.equal(result.blocks[0]?.display?.type, "table");
});

test("/skills <name> displays a skill file", async () => {
  const client = createClient();

  const result = await executeCommand("/skills joke-expert references/api.md", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /api reference/);
  assert.equal(result.blocks[0]?.display?.type, "code");
});

test("/skills view is treated as a skill name after command simplification", async () => {
  const client = createClient();

  const result = await executeCommand("/skills view joke-expert", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.title || "", /view/);
});

test("/plugins lists and views read-only plugins", async () => {
  const client = createClient();
  const listed = await executeCommand("/plugins", client, "/tmp/workspace");
  const viewed = await executeCommand("/plugins review-pack skills/review/SKILL.md", client, "/tmp/workspace");

  assert.match(listed.blocks[0]?.text || "", /review-pack/);
  assert.match(viewed.blocks[0]?.text || "", /plugin file/);
});

test("/memory displays workspace memory status", async () => {
  const client = createClient();

  const result = await executeCommand("/memory", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /enabled: yes/);
  assert.match(result.blocks[0]?.text || "", /topic_count: 1/);
  assert.equal(result.blocks[0]?.display?.type, "kv");
});

test("/memory search displays matching memory hits", async () => {
  const client = createClient();

  const result = await executeCommand("/memory search conda", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /topics\/project-conventions\.md:7/);
});

test("/memory view displays a memory file", async () => {
  const client = createClient();

  const result = await executeCommand("/memory view topics/project-conventions.md", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.equal(result.blocks[0]?.title, "topics/project-conventions.md");
  assert.match(result.blocks[0]?.text || "", /Workspace Memory/);
});

test("/dream launches the memory dream agent", async () => {
  const client = createClient();

  const result = await executeCommand("/dream", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /memory_dream_root_1234/);
  assert.match(result.blocks[0]?.text || "", /memory_dream/);
});

test("intervalToCron converts supported loop intervals", () => {
  assert.equal(intervalToCron("30s"), "*/1 * * * *");
  assert.equal(intervalToCron("5m"), "*/5 * * * *");
  assert.equal(intervalToCron("120m"), "0 */2 * * *");
  assert.equal(intervalToCron("2h"), "0 */2 * * *");
  assert.equal(intervalToCron("1d"), "0 0 */1 * *");
});

test("parseLoopCommand requires a leading separated interval", () => {
  assert.deepEqual(parseLoopCommand("/loop 5m check deploy"), {
    interval: "5m",
    cron: "*/5 * * * *",
    prompt: "check deploy",
  });
  assert.throws(() => parseLoopCommand("/loop check deploy every 2 hours"), /Usage: \/loop <interval> <prompt>/);
  assert.throws(() => parseLoopCommand("/loop check deploy"), /Usage: \/loop <interval> <prompt>/);
  assert.throws(() => parseLoopCommand("/loop 每1分钟报一下时间"), /Usage: \/loop <interval> <prompt>/);
});

test("/loop creates recurring cron task and immediately forwards prompt", async () => {
  const client = createClient();

  const result = await executeCommand("/loop 5m check deploy", client, "/tmp/workspace");

  assert.equal(result.forwardMessage, "check deploy");
  assert.equal(result.blocks[0]?.title, "Loop");
  assert.deepEqual(client.calls.at(-1), [
    "createCronTask",
    { cron: "*/5 * * * *", prompt: "check deploy", recurring: true },
  ]);
});

test("/loop list status and delete manage scheduled tasks", async () => {
  const client = createClient();

  const list = await executeCommand("/loop", client, "/tmp/workspace");
  const explicitList = await executeCommand("/loop list", client, "/tmp/workspace");
  const status = await executeCommand("/loop status", client, "/tmp/workspace");
  const deleted = await executeCommand("/loop delete cron1234", client, "/tmp/workspace");

  assert.match(list.blocks[0]?.text || "", /cron1234/);
  assert.match(explicitList.blocks[0]?.text || "", /cron1234/);
  assert.match(status.blocks[0]?.text || "", /task_count: 1/);
  assert.match(deleted.blocks[0]?.text || "", /Deleted scheduled task cron1234/);
  assert.deepEqual(client.calls.at(-1), ["deleteCronTask", { id: "cron1234" }]);
});

test("/cron list status and delete use cron RPCs", async () => {
  const client = createClient();

  const list = await executeCommand("/cron list", client, "/tmp/workspace");
  const status = await executeCommand("/cron status", client, "/tmp/workspace");
  const deleted = await executeCommand("/cron delete cron1234", client, "/tmp/workspace");

  assert.match(list.blocks[0]?.text || "", /cron1234/);
  assert.match(status.blocks[0]?.text || "", /task_count: 1/);
  assert.match(deleted.blocks[0]?.text || "", /Deleted scheduled task cron1234/);
  assert.deepEqual(client.calls.at(-1), ["deleteCronTask", { id: "cron1234" }]);
});

test("/goal displays current goal", async () => {
  const client = createClient();

  const result = await executeCommand("/goal", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /objective: ship goal/);
});

test("/status in cold start reports pending runtime instead of fetching a live session", async () => {
  const client = createClient();

  const result = await executeCommand(
    "/status",
    client,
    "/tmp/workspace",
    createPendingContext(),
  );

  assert.equal(result.blocks.length, 1);
  assert.match(result.blocks[0]?.text || "", /active_session: no/);
  assert.match(result.blocks[0]?.text || "", /pending_model_name: gpt4o_mini/);
  assert.deepEqual(client.calls, []);
});

test("/goal <objective> sets and forwards the first goal turn", async () => {
  const client = createClient();

  const result = await executeCommand("/goal implement hooks", client, "/tmp/workspace");

  assert.deepEqual(client.calls[0], ["setGoal", { objective: "implement hooks" }]);
  assert.equal(result.forwardMessage, "implement hooks");
  assert.match(result.blocks[0]?.text || "", /status: active/);
});

test("/goal pause pauses the active goal", async () => {
  const client = createClient();

  const result = await executeCommand("/goal pause", client, "/tmp/workspace");

  assert.deepEqual(client.calls[0], ["pauseGoal", {}]);
  assert.match(result.blocks[0]?.text || "", /status: paused/);
});

test("/goal resume resumes and forwards continuation", async () => {
  const client = createClient();

  const result = await executeCommand("/goal resume", client, "/tmp/workspace");

  assert.deepEqual(client.calls[0], ["resumeGoal", {}]);
  assert.match(result.forwardMessage || "", /Continue working toward the active goal/);
});

test("/goal clear clears the active goal", async () => {
  const client = createClient();

  const result = await executeCommand("/goal clear", client, "/tmp/workspace");

  assert.deepEqual(client.calls[0], ["clearGoal", {}]);
  assert.match(result.blocks[0]?.text || "", /Goal cleared/);
});

test("/memory set memory off tells users to use /config", async () => {
  const client = createClient();

  const result = await executeCommand("/memory set memory off", client, "/tmp/workspace");

  assert.equal(result.blocks.length, 1);
  assert.equal(client.calls.some((call: any[]) => call[0] === "setMemoryConfig"), false);
  assert.match(result.blocks[0]?.text || "", /\/memory set has moved to \/config/);
});

test("/memory set rejects invalid feature or value", async () => {
  const client = createClient();

  const invalidFeature = await executeCommand("/memory set cache off", client, "/tmp/workspace");
  const invalidValue = await executeCommand("/memory set memory maybe", client, "/tmp/workspace");

  assert.match(invalidFeature.blocks[0]?.text || "", /\/memory set has moved to \/config/);
  assert.match(invalidValue.blocks[0]?.text || "", /\/memory set has moved to \/config/);
});

test("legacy skill slash command is rejected", async () => {
  const client = createClient();

  const result = await executeCommand("/joke-expert tell me a short joke", client, "/tmp/workspace");

  assert.equal(result.forwardMessage, undefined);
  assert.match(result.blocks[0]?.text || "", /Unknown command/);
});

test("unknown slash command remains an error", async () => {
  const client = createClient();

  const result = await executeCommand("/does-not-exist", client, "/tmp/workspace");

  assert.equal(result.forwardMessage, undefined);
  assert.match(result.blocks[0]?.text || "", /Unknown command/);
});

test("/graph and /permissions call graph RPC helpers", async () => {
  const client = createClient();
  const listed = await executeCommand("/graph list", client, "/tmp/workspace");
  const run = await executeCommand('/graph run custom_graph {"value":"test"}', client, "/tmp/workspace");
  const permissions = await executeCommand("/permissions status", client, "/tmp/workspace");
  assert.match(listed.blocks[0]?.text || "", /deep_research/);
  assert.match(run.blocks[0]?.text || "", /completed/);
  assert.match(permissions.blocks[0]?.text || "", /default/);
});

test("/deep-research forwards a plain question to the root agent", async () => {
  const client = createClient();

  const result = await executeCommand("/deep-research --workspace compare graph APIs", client, "/tmp/workspace");

  assert.deepEqual(result.blocks, []);
  assert.equal(result.forwardMessage, "/deep-research --workspace compare graph APIs");
  assert.equal(client.calls.some((call: any[]) => call[0] === "runGraph"), false);
});

test("/deep-research validates and forwards an advanced JSON payload", async () => {
  const client = createClient();

  const result = await executeCommand('/deep-research {"question":"compare graph APIs","source_mode":"web_workspace"}', client, "/tmp/workspace");

  assert.deepEqual(result.blocks, []);
  assert.equal(result.forwardMessage, '/deep-research {"question":"compare graph APIs","source_mode":"web_workspace"}');
  assert.equal(client.calls.some((call: any[]) => call[0] === "runGraph"), false);
});

test("/deep-research suggests explicit graph invocation when automatic Graph tools are disabled", async () => {
  const client = createClient();
  client.graphsConfigStatus = async () => ({ success: true, enabled: false });

  const result = await executeCommand("/deep-research compare graph APIs", client, "/tmp/workspace");

  assert.equal(result.forwardMessage, undefined);
  assert.match(result.blocks[0]?.text || "", /\$graph:deep_research compare graph APIs/);
});
