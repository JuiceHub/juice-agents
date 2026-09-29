import type {
  RuntimeModelInfo,
  SessionStatus,
  SessionSummary,
} from "@juice-agents/shared/gateway/types";
import type { MessageBlock } from "@juice-agents/shared/presenter/stream";
import {
  presentAvailableAgents,
  presentTeamManifest,
  presentTeams,
  presentCommandHelp,
  presentCronCreated,
  presentCronDelete,
  presentCronStatus,
  presentCronTasks,
  presentDreamReceipt,
  presentModeResources,
  presentGoal,
  presentMemorySearch,
  presentMemoryStatus,
  presentMemoryView,
  presentModels,
  presentAgentTypeConfigSaved,
  presentPlugins,
  presentSessions,
  presentSkillView,
  presentSkills,
  presentStatus,
} from "@juice-agents/shared/presenter/command";
import { WebGatewayClient } from "../gateway/client.js";

const MODEL_EFFORT_OPTIONS = ["disabled", "low", "medium", "high", "xhigh", "max", "auto"] as const;
export type WebModelEffort = (typeof MODEL_EFFORT_OPTIONS)[number];

export interface WebCommandContext {
  baseDir: string;
  status: SessionStatus | null;
  sessions: SessionSummary[];
  models: RuntimeModelInfo[];
}

export interface WebCommandResult {
  blocks: MessageBlock[];
  clearMessages: boolean;
  forwardMessage?: string;
  forwardAgentMode?: string;
  status?: SessionStatus;
  resumeRunnerId?: string;
  runtimeChange?: {
    modelName: string;
    modelEffort: WebModelEffort;
  };
  workspaceAgentType?: string;
  refreshSessions?: boolean;
  refreshSkills?: boolean;
}

function systemBlock(text: string, title?: string): MessageBlock {
  return { kind: "system", title, text };
}

function errorBlock(text: string): MessageBlock {
  return { kind: "error", text };
}

function jsonBlock(title: string, payload: unknown): MessageBlock {
  return systemBlock(JSON.stringify(payload, null, 2), title);
}

function parseJsonObject(raw: string): Record<string, unknown> {
  if (!raw.trim()) return {};
  const value = JSON.parse(raw);
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new Error("Graph payload must be a JSON object");
  }
  return value as Record<string, unknown>;
}

function parseDeepResearchRequest(raw: string): Record<string, unknown> {
  let remaining = raw.trim();
  let sourceMode = "web";

  if (remaining.startsWith("{")) {
    const payload = parseJsonObject(remaining);
    if (typeof payload.question !== "string" || !payload.question.trim()) {
      throw new Error('Deep research payload must include a non-empty "question" string');
    }
    return payload;
  }

  // Validate the friendly syntax here, then forward the original message so
  // the root agent can invoke graph_tool in the current Runner.
  const flag = remaining.match(/^--(web-workspace|workspace|web)\b/);
  if (flag) {
    sourceMode = flag[1] === "web-workspace" ? "web_workspace" : flag[1];
    remaining = remaining.slice(flag[0].length).trim();
  }

  if (!remaining) {
    throw new Error("Usage: /deep-research [--web|--workspace|--web-workspace] <question>");
  }
  return { question: remaining, source_mode: sourceMode };
}

function normalizeIntervalUnit(rawUnit: string): string {
  const unit = rawUnit.toLowerCase();
  if (["s", "sec", "secs", "second", "seconds"].includes(unit)) return "s";
  if (["m", "min", "mins", "minute", "minutes"].includes(unit)) return "m";
  if (["h", "hr", "hrs", "hour", "hours"].includes(unit)) return "h";
  if (["d", "day", "days"].includes(unit)) return "d";
  return unit;
}

export function intervalToCron(interval: string): string {
  const match = interval.trim().toLowerCase().match(/^(\d+)\s*([a-z]+)$/);
  if (!match) throw new Error(`Invalid interval: ${interval}. Expected Ns, Nm, Nh, or Nd.`);
  const amount = Number(match[1]);
  const unit = normalizeIntervalUnit(match[2]);
  if (!Number.isInteger(amount) || amount <= 0) throw new Error(`Invalid interval: ${interval}. Amount must be positive.`);
  if (unit === "s") return `*/${Math.max(1, Math.ceil(amount / 60))} * * * *`;
  if (unit === "m") {
    if (amount <= 59) return `*/${amount} * * * *`;
    if (amount % 60 === 0 && amount / 60 <= 23) return `0 */${amount / 60} * * *`;
    throw new Error(`Unsupported minute interval: ${interval}. Use <=59m or whole hours up to 23h.`);
  }
  if (unit === "h") {
    if (amount <= 23) return `0 */${amount} * * *`;
    throw new Error(`Unsupported hour interval: ${interval}. Use 1h through 23h.`);
  }
  if (unit === "d") return `0 0 */${amount} * *`;
  throw new Error(`Invalid interval unit: ${interval}. Expected s, m, h, or d.`);
}

function parseLoopCommand(commandText: string): { cron: string; prompt: string } {
  const args = commandText.trim().slice("/loop".length).trim();
  const leading = args.match(/^(\d+\s*[smhd])\s+(.+)$/i);
  if (!leading) throw new Error("Usage: /loop <interval> <prompt>");
  const prompt = leading[2].trim();
  if (!prompt) throw new Error("Usage: /loop <interval> <prompt>");
  return { cron: intervalToCron(leading[1].replace(/\s+/g, "")), prompt };
}

async function executeCronManagementCommand(
  client: WebGatewayClient,
  baseDir: string,
  action: string,
  id: string | undefined,
  commandName: "/cron" | "/loop"
): Promise<WebCommandResult> {
  if (action === "list") {
    return { blocks: [presentCronTasks(await client.listCronTasks(baseDir))], clearMessages: false };
  }
  if (action === "status") {
    return { blocks: [presentCronStatus(await client.cronStatus(baseDir))], clearMessages: false };
  }
  if (action === "delete") {
    if (!id) return { blocks: [errorBlock(`Usage: ${commandName} delete <id>`)], clearMessages: false };
    return { blocks: [presentCronDelete(await client.deleteCronTask(baseDir, id))], clearMessages: false };
  }
  return { blocks: [errorBlock(`Unknown ${commandName} action: ${action}`)], clearMessages: false };
}

function isModelEffort(value: string): value is WebModelEffort {
  return MODEL_EFFORT_OPTIONS.includes(value as WebModelEffort);
}

function supportedEffortsForModel(model: RuntimeModelInfo | undefined): WebModelEffort[] {
  if (!model) return [...MODEL_EFFORT_OPTIONS];
  const efforts = (model.supported_efforts || [])
    .map((effort) => String(effort || "").trim().toLowerCase())
    .filter(isModelEffort);
  const unique = [...new Set(efforts)];
  return unique.length > 0 ? unique : ["disabled"];
}

function normalizeEffortForModel(value: string | undefined, model: RuntimeModelInfo | undefined): WebModelEffort {
  const normalized = String(value || "disabled").trim().toLowerCase();
  const effort = isModelEffort(normalized) ? normalized : "disabled";
  const supported = supportedEffortsForModel(model);
  return supported.includes(effort) ? effort : supported[0] || "disabled";
}

export async function executeWebCommand(
  commandText: string,
  client: WebGatewayClient,
  context: WebCommandContext
): Promise<WebCommandResult> {
  const parts = commandText.trim().split(/\s+/);
  const command = parts[0] || "";
  const baseDir = context.baseDir;

  if (command === "/help") return { blocks: [presentCommandHelp()], clearMessages: false };
  if (command === "/status") return { blocks: [presentStatus(context.status)], clearMessages: false };
  if (command === "/models") return { blocks: [presentModels(await client.listModels(baseDir))], clearMessages: false };
  if (command === "/resources") {
    const modeId = parts[1];
    if (modeId && !["agent", "team", "group"].includes(modeId)) return { blocks: [errorBlock("Usage: /resources [agent|team|group]")], clearMessages: false };
    return { blocks: [presentModeResources(await client.listModeResources(baseDir, modeId || ""))], clearMessages: false };
  }
  if (command === "/agents") {
    return {
      blocks: [
        presentAvailableAgents(await client.listAvailableAgents({
          ...(parts[1] ? { name: parts[1] } : {}),
          mode_id: context.status?.agent_mode || "agent",
        })),
      ],
      clearMessages: false,
    };
  }
  if (command === "/teams") {
    if (parts.length > 2) {
      return { blocks: [errorBlock("Usage: /teams [name]")], clearMessages: false };
    }
    const teamName = parts[1];
    return {
      blocks: [
        teamName
          ? presentTeamManifest(await client.getTeam({ team_name: teamName, base_dir: baseDir }))
          : presentTeams(await client.listTeams({ base_dir: baseDir })),
      ],
      clearMessages: false,
    };
  }
  if (command === "/sessions") return { blocks: [presentSessions(await client.listSessions(baseDir))], clearMessages: false, refreshSessions: true };
  if (command === "/clear") return { blocks: [], clearMessages: true };

  if (command === "/mode") {
    // /mode 只切执行模式；审批策略走 /permissions。
    const target = parts[1];
    if (!["agent", "plan", "team", "group"].includes(target)) {
      return {
        blocks: [errorBlock("Usage: /mode [agent|plan|team|group]")],
        clearMessages: false,
      };
    }
    const status = await client.switchAgentMode(target, context.status?.agent_type || "react");
    return { blocks: [presentStatus(status)], clearMessages: false, status };
  }

  if (command === "/team" || command === "/group") {
    const task = commandText.trim().slice(command.length).trim();
    if (!task) return { blocks: [errorBlock(`Usage: ${command} <task>`)], clearMessages: false };
    return { blocks: [], clearMessages: false, forwardMessage: task, forwardAgentMode: command.slice(1) };
  }

  if (command === "/agent-type") {
    const agentType = parts[1];
    if (!["react", "codeact"].includes(agentType)) return { blocks: [errorBlock("Usage: /agent-type [react|codeact]")], clearMessages: false };
    await client.saveWorkspaceConfig(baseDir, { runtime: { agent_type: agentType } });
    return {
      blocks: [presentAgentTypeConfigSaved(agentType)],
      clearMessages: false,
      workspaceAgentType: agentType,
    };
  }

  if (command === "/model") {
    const modelName = parts[1];
    const model = context.models.find((item) => item.model_name === modelName);
    const effort = String(parts[2] || context.status?.model_effort || "disabled").toLowerCase();
    if (!modelName) return { blocks: [errorBlock("Usage: /model <model_name> [effort]")], clearMessages: false };
    if (!isModelEffort(effort)) return { blocks: [errorBlock(`Invalid model effort: ${parts[2]}`)], clearMessages: false };
    const supported = supportedEffortsForModel(model);
    if (parts[2] && model && !supported.includes(effort)) {
      return { blocks: [errorBlock(`Model ${modelName} does not support effort ${parts[2]}. Expected: ${supported.join(", ")}`)], clearMessages: false };
    }
    return { blocks: [], clearMessages: false, runtimeChange: { modelName, modelEffort: normalizeEffortForModel(effort, model) } };
  }

  if (command === "/skills") {
    if (!parts[1]) return { blocks: [presentSkills(await client.listSkills({}))], clearMessages: false, refreshSkills: true };
    return { blocks: [presentSkillView(await client.viewSkill(parts[1], parts[2]))], clearMessages: false };
  }


  if (command === "/plugins") {
    if (!parts[1]) return { blocks: [presentPlugins(await client.listPlugins())], clearMessages: false };
    return { blocks: [jsonBlock("Plugin", await client.viewPlugin(parts[1], parts[2]))], clearMessages: false };
  }

  if (command === "/graph") {
    const action = (parts[1] || "list").toLowerCase();
    if (action === "list") return { blocks: [jsonBlock("Graphs", await client.listGraphs(baseDir))], clearMessages: false };
    if (action === "view") {
      if (!parts[2]) return { blocks: [errorBlock("Usage: /graph view <name>")], clearMessages: false };
      return { blocks: [jsonBlock("Graph", await client.viewGraph(baseDir, parts[2]))], clearMessages: false };
    }
    if (action === "run") {
      if (!parts[2]) return { blocks: [errorBlock("Usage: /graph run <name> [json]")], clearMessages: false };
      try {
        return { blocks: [jsonBlock("Graph Run", await client.runGraph(baseDir, parts[2], parseJsonObject(parts.slice(3).join(" "))))], clearMessages: false };
      } catch (error) {
        return { blocks: [errorBlock(error instanceof Error ? error.message : String(error))], clearMessages: false };
      }
    }
    if (action === "runs") return { blocks: [jsonBlock("Graph Runs", await client.listGraphRuns(baseDir))], clearMessages: false };
    if (["pause", "resume", "stop", "restart"].includes(action)) {
      if (!parts[2]) return { blocks: [errorBlock(`Usage: /graph ${action} <run_id>`)], clearMessages: false };
      return { blocks: [jsonBlock("Graph Run", await client.controlGraphRun(baseDir, parts[2], action))], clearMessages: false };
    }
    return { blocks: [errorBlock(`Unknown /graph action: ${action}`)], clearMessages: false };
  }

  if (command === "/deep-research") {
    try {
      const request = parseDeepResearchRequest(commandText.trim().slice(command.length));
      const graphConfig = await client.graphsConfigStatus(baseDir);
      if (!graphConfig.enabled) {
        const question = String(request.question || "").trim();
        return {
          blocks: [systemBlock(`Graph automatic tools are disabled. Use $graph:deep_research ${question}`.trim())],
          clearMessages: false,
        };
      }
      return {
        blocks: [],
        clearMessages: false,
        forwardMessage: commandText.trim(),
      };
    } catch (error) {
      return { blocks: [errorBlock(error instanceof Error ? error.message : String(error))], clearMessages: false };
    }
  }

  if (command === "/permissions") {
    // /permissions 既能切审批策略，也能只读上报规则。
    const target = parts[1];
    if (!target || target === "status") {
      return { blocks: [jsonBlock("Permissions", await client.permissionStatus(baseDir))], clearMessages: false };
    }
    if (!["default", "accept"].includes(target)) {
      return {
        blocks: [errorBlock("Usage: /permissions [default|accept|status]")],
        clearMessages: false,
      };
    }
    const status = await client.switchPermissionMode(target);
    return { blocks: [presentStatus(status)], clearMessages: false, status };
  }

  if (command === "/memory") {
    const action = (parts[1] || "status").toLowerCase();
    if (action === "status") return { blocks: [presentMemoryStatus(await client.memoryStatus())], clearMessages: false };
    if (action === "search") {
      const query = parts.slice(2).join(" ").trim();
      if (!query) return { blocks: [errorBlock("Usage: /memory search <query>")], clearMessages: false };
      return { blocks: [presentMemorySearch(await client.memorySearch(query))], clearMessages: false };
    }
    if (action === "view") return { blocks: [presentMemoryView(await client.memoryView(parts.slice(2).join(" ").trim()))], clearMessages: false };
    return { blocks: [errorBlock(`Unknown /memory action: ${action}`)], clearMessages: false };
  }

  if (command === "/dream") {
    const dream = await client.runDream();
    const status = await client.describeSession().catch(() => undefined);
    return { blocks: [presentDreamReceipt(dream)], clearMessages: false, ...(status ? { status } : {}) };
  }

  if (command === "/goal") {
    const actionOrObjective = commandText.trim().slice(command.length).trim();
    const action = (parts[1] || "").toLowerCase();
    if (!actionOrObjective) return { blocks: [presentGoal(await client.getGoal())], clearMessages: false };
    if (action === "pause") return { blocks: [presentGoal(await client.pauseGoal())], clearMessages: false };
    if (action === "resume") {
      await client.resumeGoal();
      return { blocks: [], clearMessages: false, forwardMessage: "Continue working toward the active goal using the latest evaluator feedback." };
    }
    if (action === "clear") {
      await client.clearGoal();
      return { blocks: [systemBlock("Goal cleared.", "Goal")], clearMessages: false };
    }
    const goal = await client.setGoal(actionOrObjective);
    const status = await client.describeSession().catch(() => undefined);
    return { blocks: [presentGoal(goal)], clearMessages: false, forwardMessage: actionOrObjective, ...(status ? { status } : {}) };
  }

  if (command === "/loop") {
    const action = (parts[1] || "list").toLowerCase();
    if (["list", "status", "delete"].includes(action)) return executeCronManagementCommand(client, baseDir, action, parts[2], "/loop");
    try {
      const parsed = parseLoopCommand(commandText);
      const task = await client.createCronTask(baseDir, parsed.cron, parsed.prompt, true);
      return { blocks: [presentCronCreated(task)], clearMessages: false, forwardMessage: parsed.prompt };
    } catch (exc) {
      return { blocks: [errorBlock(exc instanceof Error ? exc.message : String(exc))], clearMessages: false };
    }
  }

  if (command === "/cron") {
    const action = (parts[1] || "list").toLowerCase();
    return executeCronManagementCommand(client, baseDir, action, parts[2], "/cron");
  }

  if (command === "/resume") {
    if (!parts[1]) return { blocks: [errorBlock("Usage: /resume <runner_id>")], clearMessages: false };
    return { blocks: [], clearMessages: true, resumeRunnerId: parts[1] };
  }

  return { blocks: [errorBlock(`Unknown command: ${commandText}`)], clearMessages: false };
}
