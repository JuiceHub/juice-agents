/**
 * Slash command handling.
 */

import type {
  GatewayClient,
} from "../gateway/client.js";
import type {
  PendingSessionRuntime,
  SessionStatus,
} from "@juice-agents/shared/gateway/types";
import type { MessageBlock } from "./presenter.js";
import { isModelEffort, MODEL_EFFORTS, normalizeEffortForModel } from "./modelEffort.js";
import {
  presentCommandHelp,
  presentModels,
  presentAvailableAgents,
  presentTeamManifest,
  presentTeams,
  presentCronCreated,
  presentCronDelete,
  presentCronStatus,
  presentCronTasks,
  presentDreamReceipt,
  presentGoal,
  presentMemorySearch,
  presentMemoryStatus,
  presentMemoryView,
  presentPendingStatus,
  presentAgentTypeConfigSaved,
  presentPlugins,
  presentSessions,
  presentSkillView,
  presentSkills,
  presentStatus,
  presentWorktree,
  presentWorktrees,
} from "./presenter.js";

export interface CommandContext {
  sessionStatus?: SessionStatus | null;
  pendingRuntime?: PendingSessionRuntime | null;
}

/** 审批策略维度：只决定要不要向用户申请。 */
export const PERMISSION_MODES = ["default", "accept"];
/** 执行模式维度：决定同一 root 的工具面与能力。 */
export const AGENT_MODES = ["agent", "plan", "team", "group"];

export interface CommandResult {
  blocks: MessageBlock[];
  clearMessages: boolean;
  exitRequested: boolean;
  interactiveAction?: "model-selector" | "reward-model-selector" | "mode-selector" | "permissions-selector" | "agent-type-selector" | "resume-selector" | "config-panel" | "active-selector" | "tasks-panel";
  forwardMessage?: string;
  forwardAgentMode?: string;
  loadHistory?: boolean;
  /** 权限模式切换：不重建 root agent，由 latest-wins coordinator 应用。 */
  permissionModeSwitchRequest?: {
    permission_mode: "default" | "accept";
  };
  pendingRuntime?: PendingSessionRuntime;
  syncSession?: boolean;
}

function hasKnownSessionState(context?: CommandContext): boolean {
  return context?.sessionStatus !== undefined;
}

function hasActiveSession(context?: CommandContext): boolean {
  return Boolean(context?.sessionStatus);
}

function defaultPendingRuntime(baseDir: string): PendingSessionRuntime {
  return {
    base_dir: baseDir,
    permission_mode: "default",
    agent_mode: "agent",
    agent_type: "react",
    model_name: "",
    model_effort: "disabled",
  };
}

function normalizeAgentType(agentType: string): string {
  return agentType === "codeact" ? "codeact" : "react";
}

function normalizePendingRuntime(
  runtime: PendingSessionRuntime | null | undefined,
  baseDir: string
): PendingSessionRuntime {
  const fallback = defaultPendingRuntime(baseDir);
  if (!runtime) {
    return fallback;
  }
  return {
    base_dir: runtime.base_dir || fallback.base_dir,
    permission_mode: runtime.permission_mode || fallback.permission_mode,
    agent_mode: runtime.agent_mode || fallback.agent_mode,
    agent_type: normalizeAgentType(runtime.agent_type || fallback.agent_type),
    model_name: runtime.model_name || fallback.model_name,
    model_effort: runtime.model_effort || fallback.model_effort,
  };
}

function buildPendingRuntimeFromSession(status: SessionStatus): PendingSessionRuntime {
  return {
    base_dir: status.base_dir,
    permission_mode: status.permission_mode || "default",
    agent_mode: status.agent_mode || "agent",
    agent_type: normalizeAgentType(status.agent_type || "react"),
    model_name: status.model_name || "",
    model_effort: status.model_effort || "disabled",
  };
}

function updatePendingRuntime(
  current: PendingSessionRuntime,
  patch: Partial<PendingSessionRuntime>
): PendingSessionRuntime {
  const agentMode = patch.agent_mode || current.agent_mode;
  const nextAgentType = patch.agent_type || current.agent_type;
  return normalizePendingRuntime(
    {
      ...current,
      ...patch,
      agent_mode: agentMode,
      agent_type: normalizeAgentType(nextAgentType),
    },
    current.base_dir,
  );
}

async function resolveLiveSession(
  client: GatewayClient,
  baseDir: string,
  context?: CommandContext
): Promise<SessionStatus> {
  if (hasKnownSessionState(context)) {
    if (!context?.sessionStatus) {
      throw new Error("No active session");
    }
    return context.sessionStatus;
  }
  return client.describeSession();
}

/** Persist a model edit without copying untouched inherited fields. */
export function modelConfigPatch(
  previous: { model_name?: string; model_effort?: string } | null | undefined,
  next: { model_name: string; model_effort: string },
): Record<string, string> {
  const patch: Record<string, string> = {};
  if (previous?.model_name !== next.model_name) patch.model_name = next.model_name;
  if (previous?.model_effort !== next.model_effort) patch.model_effort = next.model_effort;
  return patch;
}

async function saveRuntimeConfig(
  client: GatewayClient,
  baseDir: string,
  runtime: Record<string, string>
): Promise<void> {
  if (Object.keys(runtime).length > 0) {
    await client.saveWorkspaceConfig({ base_dir: baseDir, config: { runtime } });
  }
}

function normalizeIntervalUnit(rawUnit: string): string {
  const unit = rawUnit.toLowerCase();
  if (["s", "sec", "secs", "second", "seconds"].includes(unit)) return "s";
  if (["m", "min", "mins", "minute", "minutes"].includes(unit)) return "m";
  if (["h", "hr", "hrs", "hour", "hours"].includes(unit)) return "h";
  if (["d", "day", "days"].includes(unit)) return "d";
  return unit;
}

function jsonBlock(title: string, payload: unknown): MessageBlock {
  return { kind: "system", title, text: JSON.stringify(payload, null, 2) };
}


export function intervalToCron(interval: string): string {
  const match = interval.trim().toLowerCase().match(/^(\d+)\s*([a-z]+)$/);
  if (!match) {
    throw new Error(`Invalid interval: ${interval}. Expected Ns, Nm, Nh, or Nd.`);
  }
  const amount = Number(match[1]);
  const unit = normalizeIntervalUnit(match[2]);
  if (!Number.isInteger(amount) || amount <= 0) {
    throw new Error(`Invalid interval: ${interval}. Amount must be positive.`);
  }
  if (unit === "s") {
    const minutes = Math.max(1, Math.ceil(amount / 60));
    return `*/${minutes} * * * *`;
  }
  if (unit === "m") {
    if (amount <= 59) {
      return `*/${amount} * * * *`;
    }
    if (amount % 60 === 0 && amount / 60 <= 23) {
      return `0 */${amount / 60} * * *`;
    }
    throw new Error(`Unsupported minute interval: ${interval}. Use <=59m or whole hours up to 23h.`);
  }
  if (unit === "h") {
    if (amount <= 23) {
      return `0 */${amount} * * *`;
    }
    throw new Error(`Unsupported hour interval: ${interval}. Use 1h through 23h.`);
  }
  if (unit === "d") {
    return `0 0 */${amount} * *`;
  }
  throw new Error(`Invalid interval unit: ${interval}. Expected s, m, h, or d.`);
}

export function parseLoopCommand(commandText: string): { interval: string; cron: string; prompt: string } {
  const args = commandText.trim().slice("/loop".length).trim();
  if (!args) {
    throw new Error("Usage: /loop <interval> <prompt>");
  }
  const leading = args.match(/^(\d+\s*[smhd])\s+(.+)$/i);
  if (leading) {
    const interval = leading[1].replace(/\s+/g, "");
    const prompt = leading[2].trim();
    if (!prompt) throw new Error("Usage: /loop <interval> <prompt>");
    return { interval, cron: intervalToCron(interval), prompt };
  }
  throw new Error("Usage: /loop <interval> <prompt>");
}

async function executeCronManagementCommand(
  client: GatewayClient,
  baseDir: string,
  action: string,
  id: string | undefined,
  commandName: "/cron" | "/loop"
): Promise<CommandResult> {
  if (action === "list") {
    return {
      blocks: [presentCronTasks(await client.listCronTasks({ base_dir: baseDir }))],
      clearMessages: false,
      exitRequested: false,
    };
  }
  if (action === "status") {
    return {
      blocks: [presentCronStatus(await client.cronStatus({ base_dir: baseDir }))],
      clearMessages: false,
      exitRequested: false,
    };
  }
  if (action === "delete") {
    if (!id) {
      return {
        blocks: [{ kind: "error", text: `Usage: ${commandName} delete <id>` }],
        clearMessages: false,
        exitRequested: false,
      };
    }
    return {
      blocks: [presentCronDelete(await client.deleteCronTask({ base_dir: baseDir, id }))],
      clearMessages: false,
      exitRequested: false,
    };
  }
  return {
    blocks: [{ kind: "error", text: `Unknown ${commandName} action: ${action}` }],
    clearMessages: false,
    exitRequested: false,
  };
}

export async function executeCommand(
  commandText: string,
  client: GatewayClient,
  baseDir: string,
  context?: CommandContext
): Promise<CommandResult> {
  const parts = commandText.trim().split(/\s+/);
  const command = parts[0] || "";
  const pendingRuntime = normalizePendingRuntime(context?.pendingRuntime, baseDir);

  if (command === "/help") {
    return {
      blocks: [presentCommandHelp()],
      clearMessages: false,
      exitRequested: false,
    };
  }

  if (command === "/status") {
    if (hasKnownSessionState(context) && !hasActiveSession(context)) {
      return {
        blocks: [presentPendingStatus(pendingRuntime)],
        clearMessages: false,
        exitRequested: false,
      };
    }

    const status = await resolveLiveSession(client, baseDir, context);
    return {
      blocks: [presentStatus(status)],
      clearMessages: false,
      exitRequested: false,
    };
  }

  if (command === "/mode") {
    if (parts.length === 1) {
      return {
        blocks: [],
        clearMessages: false,
        exitRequested: false,
        interactiveAction: "mode-selector",
      };
    }

    const target = parts[1];
    if (!AGENT_MODES.includes(target)) {
      return {
        blocks: [{
          kind: "error",
          text: `Invalid agent mode: ${target}. Expected: ${AGENT_MODES.join(", ")}. `
            + `Use /permissions to change the approval policy.`,
        }],
        clearMessages: false,
        exitRequested: false,
      };
    }

    // 会话还没起来时只更新 pending 运行态，等首轮消息再真正装配。
    if (hasKnownSessionState(context) && !hasActiveSession(context)) {
      const nextRuntime = updatePendingRuntime(pendingRuntime, { agent_mode: target });
      await saveRuntimeConfig(client, baseDir, { agent_mode: nextRuntime.agent_mode });
      return {
        blocks: [presentPendingStatus(nextRuntime)],
        clearMessages: false,
        exitRequested: false,
        pendingRuntime: nextRuntime,
      };
    }

    // 每个 agent_mode 对应不同的 root actor profile，必须重建 coordinator。
    const currentStatus = await resolveLiveSession(client, baseDir, context);
    const status = await client.switchAgentMode({
      agent_mode: target,
      agent_type: currentStatus.agent_type,
    });
    await saveRuntimeConfig(client, baseDir, { agent_mode: target });
    return {
      blocks: [presentStatus(status)],
      clearMessages: false,
      exitRequested: false,
      syncSession: true,
    };
  }

  if (command === "/team" || command === "/group") {
    const modeId = command.slice(1);
    const task = commandText.trim().slice(command.length).trim();
    if (!task) {
      return {
        blocks: [{ kind: "error", text: `Usage: ${command} <task>` }],
        clearMessages: false,
        exitRequested: false,
      };
    }
    const currentAgentMode = hasActiveSession(context)
      ? context?.sessionStatus?.agent_mode || "agent"
      : pendingRuntime.agent_mode;
    if (currentAgentMode === "plan") {
      return {
        blocks: [{ kind: "error", text: `Plan mode cannot run ${modeId}. Use /mode agent first.` }],
        clearMessages: false,
        exitRequested: false,
      };
    }
    return {
      blocks: [],
      clearMessages: false,
      exitRequested: false,
      forwardMessage: task,
      forwardAgentMode: modeId,
    };
  }

  if (command === "/config") {
    return {
      blocks: [],
      clearMessages: false,
      exitRequested: false,
      interactiveAction: "config-panel",
    };
  }

  if (command === "/models") {
    const models = await client.listModels({ base_dir: baseDir });
    return {
      blocks: [presentModels(models)],
      clearMessages: false,
      exitRequested: false,
    };
  }

  if (command === "/agents") {
    const name = parts[1];
    return {
      blocks: [
        presentAvailableAgents(await client.listAvailableAgents({
          ...(name ? { name } : {}),
          mode_id: context?.sessionStatus?.agent_mode || pendingRuntime.agent_mode,
        })),
      ],
      clearMessages: false,
      exitRequested: false,
    };
  }

  if (command === "/teams") {
    const teamName = parts[1];
    if (parts.length > 2) {
      return {
        blocks: [{ kind: "error", text: "Usage: /teams [name]" }],
        clearMessages: false,
        exitRequested: false,
      };
    }
    return {
      blocks: [
        teamName
          ? presentTeamManifest(await client.getTeam({ team_name: teamName, base_dir: baseDir }))
          : presentTeams(await client.listTeams({ base_dir: baseDir })),
      ],
      clearMessages: false,
      exitRequested: false,
    };
  }

  if (command === "/model") {
    if (parts.length === 1) {
      return {
        blocks: [],
        clearMessages: false,
        exitRequested: false,
        interactiveAction: "model-selector",
      };
    }

    const modelEffort = parts[2]?.toLowerCase();
    if (modelEffort && !isModelEffort(modelEffort)) {
      return {
        blocks: [
          {
            kind: "error",
            text: `Invalid model effort: ${parts[2]}. Expected: ${MODEL_EFFORTS.join(", ")}`,
          },
        ],
        clearMessages: false,
        exitRequested: false,
      };
    }
    const models = await client.listModels({ base_dir: baseDir });
    const targetModel = models.find((model) => model.model_name === parts[1]);
    const targetEfforts = targetModel?.supported_efforts || [...MODEL_EFFORTS];
    if (modelEffort && targetModel && !targetEfforts.includes(modelEffort)) {
      return {
        blocks: [
          {
            kind: "error",
            text: `Model ${parts[1]} does not support effort ${parts[2]}. Expected: ${targetEfforts.join(", ")}`,
          },
        ],
        clearMessages: false,
        exitRequested: false,
      };
    }
    const currentRuntime = hasKnownSessionState(context) && !hasActiveSession(context)
      ? normalizePendingRuntime(context?.pendingRuntime, baseDir)
      : context?.sessionStatus
      ? buildPendingRuntimeFromSession(context.sessionStatus)
      : null;
    const selectedEffort = modelEffort || normalizeEffortForModel(currentRuntime?.model_effort, targetModel);

    if (hasKnownSessionState(context) && !hasActiveSession(context)) {
      const nextRuntime = updatePendingRuntime(pendingRuntime, {
        model_name: parts[1] || "",
        model_effort: selectedEffort,
      });
      await saveRuntimeConfig(client, baseDir, modelConfigPatch(currentRuntime, nextRuntime));
      return {
        blocks: [presentPendingStatus(nextRuntime)],
        clearMessages: false,
        exitRequested: false,
        pendingRuntime: nextRuntime,
      };
    }

    const status = await client.switchModel({
      model_name: parts[1] || "",
      model_effort: selectedEffort,
    });
    await saveRuntimeConfig(client, baseDir, modelConfigPatch(currentRuntime, status));
    return {
      blocks: [presentStatus(status)],
      clearMessages: false,
      exitRequested: false,
      syncSession: true,
    };
  }

  if (command === "/rm") {
    if (parts.length === 1) {
      // Match `/model`: querying current state belongs to opening the selector,
      // while dispatch itself remains read-only and emits no transcript block.
      return {
        blocks: [],
        clearMessages: false,
        exitRequested: false,
        interactiveAction: "reward-model-selector",
      };
    }
    if (parts.length > 2) {
      return {
        blocks: [{ kind: "error", text: "Usage: /rm [model_name|reset] (use /models to list models)" }],
        clearMessages: false,
        exitRequested: false,
      };
    }

    try {
      const modelName = parts[1];
      const reset = modelName === "reset";
      // Use the same workspace catalog as /models. The gateway resolves its
      // credentials and constructs the scorer; the CLI only selects its name.
      if (modelName && !reset) {
        const models = await client.listModels({ base_dir: baseDir });
        if (!models.some((model) => model.model_name === modelName)) {
          return {
            blocks: [{ kind: "error", text: `Unknown reward model: ${modelName}. Use /models to list available models.` }],
            clearMessages: false,
            exitRequested: false,
          };
        }
      }
      const result = await client.configureRewardModel({
        base_dir: baseDir,
        ...(reset ? { reset: true } : modelName ? { model_name: modelName } : {}),
      });
      const lines = [
        `Reward model: ${result.model_name || (result.scorer_configured ? "SDK scorer" : "not configured")}`,
        `Configured breadth: ${result.breadth}`,
        result.execution === "candidate_selection"
          ? "Strategy: Per-step candidate selection"
          : "Strategy: 线性执行 (Linear execution)",
      ];
      if (!result.scorer_configured) {
        lines.push("Use /rm <model_name> to select a reward model from /models.");
      } else if (result.breadth <= 1) {
        lines.push("Use /strategy breadth=N (N > 1) to enable candidate selection.");
      }
      return {
        blocks: [{ kind: "system", text: lines.join("\n") }],
        clearMessages: false,
        exitRequested: false,
      };
    } catch (error: any) {
      return {
        blocks: [{ kind: "error", text: `Failed to configure reward model: ${error.message}` }],
        clearMessages: false,
        exitRequested: false,
      };
    }
  }

  if (command === "/strategy") {
    if (parts.length === 1) {
      return {
        blocks: [{
          kind: "system",
          text: [
            "Usage: /strategy breadth=N",
            "       /strategy reset",
            "",
            "breadth is a positive integer: generate N candidates per step, select one, execute once.",
            "breadth=1 or no scorer: 线性执行 (Linear execution).",
            "Use /rm <model_name> to configure a reward model from /models.",
            "max_steps continues to limit executed steps.",
          ].join("\n"),
        }],
        clearMessages: false,
        exitRequested: false,
      };
    }

    // Accept exactly one supported choice. Avoid parseFloat: values such as
    // 3.5, arrays and trailing junk must not silently change candidate counts.
    const reset = parts.length === 2 && parts[1] === "reset";
    const breadthMatch = parts.length === 2 ? /^breadth=([1-9]\d*)$/.exec(parts[1]) : null;
    const breadth = breadthMatch ? Number(breadthMatch[1]) : null;
    if (!reset && (breadth === null || !Number.isSafeInteger(breadth))) {
      return {
        blocks: [{ kind: "error", text: "Usage: /strategy breadth=N (positive integer) or /strategy reset" }],
        clearMessages: false,
        exitRequested: false,
      };
    }

    try {
      // Include the workspace for a cold gateway shell. The subsequent
      // start_session RPC carries this pending configuration into its Runner.
      const result = await client.switchStrategy({
        base_dir: baseDir,
        ...(reset ? { reset: true } : { breadth: breadth! }),
      });
      const strategyInfo = result.strategy;
      const lines = [result.message, ""];
      if (strategyInfo.execution === "candidate_selection") {
        lines.push(`Strategy: Per-step candidate selection (breadth=${strategyInfo.params.breadth})`);
      } else {
        lines.push("Strategy: 线性执行 (Linear execution)");
        if (strategyInfo.params.breadth !== undefined) {
          lines.push(`Configured breadth: ${strategyInfo.params.breadth}; ${strategyInfo.scorer_configured ? "scorer configured" : "no scorer configured"}`);
        }
      }
      return {
        blocks: [{ kind: "system", text: lines.join("\n") }],
        clearMessages: false,
        exitRequested: false,
      };
    } catch (error: any) {
      return {
        blocks: [{ kind: "error", text: `Failed to switch strategy: ${error.message}` }],
        clearMessages: false,
        exitRequested: false,
      };
    }
  }

  if (command === "/skills") {
    if (!parts[1]) {
      const skills = await client.listSkills({});
      return {
        blocks: [presentSkills(skills)],
        clearMessages: false,
        exitRequested: false,
      };
    }
    const filePath = parts[2];
    const view = await client.viewSkill({
      name: parts[1],
      ...(filePath ? { file_path: filePath } : {}),
    });
    return {
      blocks: [presentSkillView(view)],
      clearMessages: false,
      exitRequested: false,
    };
  }

  if (command === "/plugins") {
    if (!parts[1]) {
      return {
        blocks: [presentPlugins(await client.listPlugins())],
        clearMessages: false,
        exitRequested: false,
      };
    }
    const view = await client.viewPlugin({
      name: parts[1],
      ...(parts[2] ? { file_path: parts[2] } : {}),
    });
    return {
      blocks: [jsonBlock("Plugin", view)],
      clearMessages: false,
      exitRequested: false,
    };
  }


  if (command === "/permissions") {
    // 无参数时打开 selector；带合法取值时切换审批策略；其余情况仍然只读上报。
    if (parts.length === 1) {
      return {
        blocks: [],
        clearMessages: false,
        exitRequested: false,
        interactiveAction: "permissions-selector",
      };
    }

    const target = parts[1];
    if (target === "status") {
      return {
        blocks: [jsonBlock("Permissions", await client.permissionStatus({ base_dir: baseDir }))],
        clearMessages: false,
        exitRequested: false,
      };
    }

    if (!PERMISSION_MODES.includes(target)) {
      return {
        blocks: [{
          kind: "error",
          text: `Invalid permission mode: ${target}. Expected: ${PERMISSION_MODES.join(", ")}, status. `
            + `Use /mode to change the agent mode.`,
        }],
        clearMessages: false,
        exitRequested: false,
      };
    }

    if (hasKnownSessionState(context) && !hasActiveSession(context)) {
      const nextRuntime = updatePendingRuntime(pendingRuntime, { permission_mode: target });
      await saveRuntimeConfig(client, baseDir, { permission_mode: nextRuntime.permission_mode });
      return {
        blocks: [presentPendingStatus(nextRuntime)],
        clearMessages: false,
        exitRequested: false,
        pendingRuntime: nextRuntime,
      };
    }

    // 权限模式不重建 root agent，交给 latest-wins coordinator 在 step 边界应用。
    // 持久化由 coordinator 统一负责，避免此处与 selector 各写一遍。
    return {
      blocks: [],
      clearMessages: false,
      exitRequested: false,
      permissionModeSwitchRequest: { permission_mode: target as "default" | "accept" },
    };
  }

  if (command === "/memory") {
    const action = (parts[1] || "status").toLowerCase();
    if (action === "status") {
      return {
        blocks: [presentMemoryStatus(await client.memoryStatus())],
        clearMessages: false,
        exitRequested: false,
      };
    }
    if (action === "search") {
      const query = parts.slice(2).join(" ").trim();
      if (!query) {
        return {
          blocks: [{ kind: "error", text: "Usage: /memory search <query>" }],
          clearMessages: false,
          exitRequested: false,
        };
      }
      return {
        blocks: [presentMemorySearch(await client.memorySearch({ query }))],
        clearMessages: false,
        exitRequested: false,
      };
    }
    if (action === "view") {
      const path = parts.slice(2).join(" ").trim();
      return {
        blocks: [presentMemoryView(await client.memoryView(path ? { path } : {}))],
        clearMessages: false,
        exitRequested: false,
      };
    }
    if (action === "set") {
      return {
        blocks: [{ kind: "error", text: "/memory set has moved to /config" }],
        clearMessages: false,
        exitRequested: false,
      };
    }
    return {
      blocks: [{ kind: "error", text: `Unknown /memory action: ${action}` }],
      clearMessages: false,
      exitRequested: false,
    };
  }

  if (command === "/dream") {
    return {
      blocks: [presentDreamReceipt(await client.runDream())],
      clearMessages: false,
      exitRequested: false,
      syncSession: true,
    };
  }

  if (command === "/loop") {
    const action = (parts[1] || "list").toLowerCase();
    if (["list", "status", "delete"].includes(action)) {
      return executeCronManagementCommand(client, baseDir, action, parts[2], "/loop");
    }
    try {
      const parsed = parseLoopCommand(commandText);
      const task = await client.createCronTask({
        base_dir: baseDir,
        cron: parsed.cron,
        prompt: parsed.prompt,
        recurring: true,
      });
      return {
        blocks: [presentCronCreated(task)],
        clearMessages: false,
        exitRequested: false,
        forwardMessage: parsed.prompt,
      };
    } catch (e: any) {
      return {
        blocks: [{ kind: "error", text: e.message }],
        clearMessages: false,
        exitRequested: false,
      };
    }
  }

  if (command === "/cron") {
    const action = (parts[1] || "list").toLowerCase();
    return executeCronManagementCommand(client, baseDir, action, parts[2], "/cron");
  }

  if (command === "/goal") {
    const actionOrObjective = commandText.trim().slice(command.length).trim();
    const action = (parts[1] || "").toLowerCase();
    if (!actionOrObjective) {
      if (hasKnownSessionState(context) && !hasActiveSession(context)) {
        return {
          blocks: [{ kind: "error", text: "No active session. Use /goal <objective> to start one." }],
          clearMessages: false,
          exitRequested: false,
        };
      }
      return {
        blocks: [presentGoal(await client.getGoal())],
        clearMessages: false,
        exitRequested: false,
      };
    }
    if (action === "pause") {
      if (hasKnownSessionState(context) && !hasActiveSession(context)) {
        return {
          blocks: [{ kind: "error", text: "No active session." }],
          clearMessages: false,
          exitRequested: false,
        };
      }
      return {
        blocks: [presentGoal(await client.pauseGoal())],
        clearMessages: false,
        exitRequested: false,
        syncSession: true,
      };
    }
    if (action === "resume") {
      if (hasKnownSessionState(context) && !hasActiveSession(context)) {
        return {
          blocks: [{ kind: "error", text: "No active session." }],
          clearMessages: false,
          exitRequested: false,
        };
      }
      await client.resumeGoal();
      return {
        blocks: [],
        clearMessages: false,
        exitRequested: false,
        forwardMessage: "Continue working toward the active goal using the latest evaluator feedback.",
      };
    }
    if (action === "clear") {
      if (hasKnownSessionState(context) && !hasActiveSession(context)) {
        return {
          blocks: [{ kind: "error", text: "No active session." }],
          clearMessages: false,
          exitRequested: false,
        };
      }
      await client.clearGoal();
      return {
        blocks: [{ kind: "system", title: "Goal", text: "Goal cleared." }],
        clearMessages: false,
        exitRequested: false,
        syncSession: true,
      };
    }
    const goal = await client.setGoal({ objective: actionOrObjective });
    return {
      blocks: [presentGoal(goal)],
      clearMessages: false,
      exitRequested: false,
      forwardMessage: actionOrObjective,
    };
  }

  if (command === "/worktree") {
    const action = (parts[1] || "status").toLowerCase();
    if (action === "enter") {
      const name = parts.slice(2).join(" ").trim();
      return {
        blocks: [presentWorktree(await client.enterWorktree(name ? { name } : {}))],
        clearMessages: false,
        exitRequested: false,
        syncSession: true,
      };
    }
    if (action === "exit") {
      const discard = parts.slice(2).includes("--discard");
      return {
        blocks: [presentWorktree(await client.exitWorktree({ discard }))],
        clearMessages: false,
        exitRequested: false,
        syncSession: true,
      };
    }
    if (action === "list") {
      return {
        blocks: [presentWorktrees(await client.listWorktrees())],
        clearMessages: false,
        exitRequested: false,
      };
    }
    if (action === "status") {
      const name = parts.slice(2).join(" ").trim();
      return {
        blocks: [presentWorktree(await client.worktreeStatus(name ? { name } : {}))],
        clearMessages: false,
        exitRequested: false,
        syncSession: true,
      };
    }
    return {
      blocks: [{ kind: "error", text: "Usage: /worktree [enter [name]|exit [--discard]|list|status [name]]" }],
      clearMessages: false,
      exitRequested: false,
    };
  }

  if (command === "/agent-type") {
    if (parts.length === 1) {
      return {
        blocks: [],
        clearMessages: false,
        exitRequested: false,
        interactiveAction: "agent-type-selector",
      };
    }

    const agentType = parts[1];
    if (!["react", "codeact"].includes(agentType)) {
      return {
        blocks: [{ kind: "error", text: `Invalid agent type: ${agentType}` }],
        clearMessages: false,
        exitRequested: false,
      };
    }

    const nextRuntime = updatePendingRuntime(pendingRuntime, { agent_type: agentType });
    await saveRuntimeConfig(client, baseDir, { agent_type: nextRuntime.agent_type });
    return {
      blocks: [presentAgentTypeConfigSaved(nextRuntime.agent_type)],
      clearMessages: false,
      exitRequested: false,
      pendingRuntime: nextRuntime,
    };
  }

  if (command === "/resume") {
    if (parts.length === 1) {
      return {
        blocks: [],
        clearMessages: false,
        exitRequested: false,
        interactiveAction: "resume-selector",
      };
    }

    await client.resumeSession({
      runner_id: parts[1],
      base_dir: baseDir,
    });
    return {
      blocks: [],
      clearMessages: true,
      exitRequested: false,
      loadHistory: true,
      syncSession: true,
    };
  }

  if (command === "/sessions") {
    const summaries = await client.listSessions({ base_dir: baseDir });
    return {
      blocks: [presentSessions(summaries)],
      clearMessages: false,
      exitRequested: false,
    };
  }

  if (command === "/tasks") {
    return {
      blocks: [],
      clearMessages: false,
      exitRequested: false,
      interactiveAction: "tasks-panel",
    };
  }

  if (command === "/active") {
    return {
      blocks: [],
      clearMessages: false,
      exitRequested: false,
      interactiveAction: "active-selector",
    };
  }

  if (command === "/clear") {
    return {
      blocks: [],
      clearMessages: true,
      exitRequested: false,
    };
  }

  if (command === "/exit") {
    await client.stopSession();
    return {
      blocks: [],
      clearMessages: false,
      exitRequested: true,
    };
  }

  return {
    blocks: [{ kind: "error", text: `Unknown command: ${commandText}` }],
    clearMessages: false,
    exitRequested: false,
  };
}

export function runtimeFromSession(status: SessionStatus | null | undefined, baseDir: string): PendingSessionRuntime {
  if (!status) {
    return defaultPendingRuntime(baseDir);
  }
  return buildPendingRuntimeFromSession(status);
}
