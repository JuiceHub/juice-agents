/**
 * JSON-RPC client for communicating with Python gateway over stdio.
 */

import { spawn, ChildProcess } from "child_process";
import * as readline from "readline";
import * as path from "path";
import { fileURLToPath } from "url";
import type {
  JsonRpcRequest,
  JsonRpcResponse,
  JsonRpcNotification,
  RunnerStreamEvent,
  StreamMessageResult,
  RuntimeModelInfo,
  SessionStatus,
  SessionSummary,
  WorkspaceRuntimeConfig,
  ActorSessionsReport,
  AskRequest,
  AskResponse,
  SkillsListResponse,
  PluginsListResponse,
  PluginViewResponse,
  SkillsConfigStatusResponse,
  SkillViewResponse,
  AvailableAgentsResponse,
  TeamManifest,
  TeamsListResponse,
  ModeResourcesResponse,
  DreamRunResponse,
  CronStatusResponse,
  CronTask,
  CronTaskDeleteResponse,
  CronTaskFireResponse,
  CronTaskListResponse,
  MemorySearchResponse,
  MemoryStatusResponse,
  MemoryViewResponse,
  BrowserConfigResponse,
  ImageConfigResponse,
  GoalSummary,
  AsyncTaskState,
  AsyncTaskOutput,
  WorktreeListResponse,
  WorktreeStatus,
  SendActorMessageResponse,
  InterruptActorResponse,
  StreamStepNotification,
  AskRequestNotification,
} from "@juice-agents/shared/gateway/types";

// Resolve project root: frontend/cli/src/gateway/client.ts -> project root
const __filename = fileURLToPath(import.meta.url);
const PROJECT_ROOT = path.resolve(path.dirname(__filename), "..", "..", "..", "..");

export class GatewayClient {
  private process: ChildProcess | null = null;
  private requestId = 0;
  private pendingRequests = new Map<
    number,
    { resolve: (value: any) => void; reject: (error: Error) => void }
  >();
  private streamHandlers = new Map<number, (event: RunnerStreamEvent) => void>();
  private askHandlers = new Map<number, (request: AskRequest) => void>();
  // 常驻订阅：处理没有对应请求的服务端主动推送（如 watcher 后台任务完成自动续跑）。
  // 普通 streamMessage 期间有临时 streamHandlers，续跑期间没有，则回落到 ambient。
  private ambientStreamHandler: ((event: RunnerStreamEvent) => void) | null = null;
  private ambientAskHandler: ((request: AskRequest) => void) | null = null;
  private rl: readline.Interface | null = null;

  constructor(private pythonPath: string = "python") {}

  /** 注册常驻 stream 事件处理器，接收无活跃请求时的自动续跑推送。 */
  setAmbientStreamHandler(handler: ((event: RunnerStreamEvent) => void) | null): void {
    this.ambientStreamHandler = handler;
  }

  /** 注册常驻 ask 处理器，接收自动续跑期间触发的 ask 请求。 */
  setAmbientAskHandler(handler: ((request: AskRequest) => void) | null): void {
    this.ambientAskHandler = handler;
  }

  async start(): Promise<void> {
    // Spawn Python gateway process in project root so imports work
    this.process = spawn(this.pythonPath, [
      "-m",
      "adapters.stdio_gateway.entry",
    ], {
      cwd: PROJECT_ROOT,
      stdio: ["pipe", "pipe", "pipe"],
    });

    if (!this.process.stdout || !this.process.stdin) {
      throw new Error("Failed to create gateway process");
    }

    // Forward stderr for debugging
    this.process.stderr?.on("data", (data: Buffer) => {
      // Silently consume stderr to avoid polluting the CLI
    });

    // Set up readline for line-by-line JSON parsing
    this.rl = readline.createInterface({
      input: this.process.stdout,
      crlfDelay: Infinity,
    });

    this.rl.on("line", (line) => {
      this.handleResponse(line);
    });

    this.process.on("error", (error) => {
      this.rejectAllPending(`Gateway process error: ${error.message}`);
    });

    this.process.on("exit", (code) => {
      this.rejectAllPending(`Gateway process exited with code ${code}`);
    });

    // Wait briefly for the process to start
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(resolve, 200);
      this.process!.on("error", (err) => {
        clearTimeout(timer);
        reject(err);
      });
    });
  }

  private rejectAllPending(message: string): void {
    this.debugTransport(`${message}; pending=${this.pendingRequests.size}`);
    for (const [id, pending] of this.pendingRequests) {
      pending.reject(new Error(message));
    }
    this.pendingRequests.clear();
    this.streamHandlers.clear();
    this.askHandlers.clear();
  }

  private debugTransport(message: string): void {
    // Direct console output corrupts Ink frames, so verbose transport logging
    // is opt-in while backend lifecycle logs remain always available.
    if (process.env.JUICE_DEBUG_TRANSPORT === "1") {
      process.stderr.write(`[juice-cli transport] ${message}\n`);
    }
  }

  private handleResponse(line: string): void {
    try {
      const data = JSON.parse(line);

      // Check if it's a notification (streaming step)
      if (data.method === "stream_step" && !data.id) {
        const notification = data as JsonRpcNotification;
        const { request_id: requestId, event } = notification.params as StreamStepNotification;
        if (requestId === null || requestId === "") {
          this.ambientStreamHandler?.(event);
          return;
        }
        const handler = this.streamHandlers.get(Number(requestId));
        if (handler) {
          handler(event);
        } else {
          this.debugTransport(`drop late stream_step request_id=${String(requestId)}`);
        }
        return;
      }

      if (data.method === "ask_request" && !data.id) {
        const notification = data as JsonRpcNotification;
        const { request_id: requestId, request } = notification.params as AskRequestNotification;
        if (requestId === null || requestId === "") {
          this.ambientAskHandler?.(request);
          return;
        }
        const handler = this.askHandlers.get(Number(requestId));
        if (handler) {
          handler(request);
        } else {
          this.debugTransport(`drop late ask_request request_id=${String(requestId)}`);
        }
        return;
      }

      // Regular response
      const response = data as JsonRpcResponse;
      const pending = this.pendingRequests.get(response.id as number);
      if (!pending) return;

      this.pendingRequests.delete(response.id as number);

      if (response.error) {
        pending.reject(
          new Error(`RPC error: ${response.error.message}`)
        );
      } else {
        pending.resolve(response.result);
      }
    } catch (error) {
      console.error("Failed to parse gateway response:", error);
    }
  }

  private sendRequest(
    method: string,
    params: Record<string, any>,
    reservedId?: number,
  ): Promise<any> {
    if (!this.process?.stdin) {
      throw new Error("Gateway not started");
    }

    const id = reservedId ?? ++this.requestId;
    const request: JsonRpcRequest = {
      jsonrpc: "2.0",
      id,
      method,
      params,
    };

    return new Promise((resolve, reject) => {
      this.pendingRequests.set(id, { resolve, reject });
      this.process!.stdin!.write(JSON.stringify(request) + "\n");
    });
  }

  async startSession(params: {
    base_dir: string;
    permission_mode?: string;
    agent_mode?: string;
    agent_type?: string;
    runtime_config_path?: string;
    model_name?: string;
    model_effort?: string;
    worktree?: string;
  }): Promise<SessionStatus> {
    return this.sendRequest("start_session", params);
  }

  async resumeSession(params: {
    runner_id: string;
    base_dir: string;
    runtime_config_path?: string;
    model_name?: string;
    model_effort?: string;
  }): Promise<SessionStatus> {
    return this.sendRequest("resume_session", params);
  }

  async describeSession(): Promise<SessionStatus> {
    return this.sendRequest("describe_session", {});
  }

  async enterWorktree(params: { name?: string } = {}): Promise<WorktreeStatus> {
    return this.sendRequest("enter_worktree", params);
  }

  async exitWorktree(params: { discard?: boolean } = {}): Promise<WorktreeStatus> {
    return this.sendRequest("exit_worktree", params);
  }

  async listWorktrees(): Promise<WorktreeListResponse> {
    return this.sendRequest("list_worktrees", {});
  }

  async worktreeStatus(params: { name?: string } = {}): Promise<WorktreeStatus> {
    return this.sendRequest("worktree_status", params);
  }

  async describeActorSessions(): Promise<ActorSessionsReport> {
    return this.sendRequest("describe_actor_sessions", {});
  }

  async sendActorMessage(params: {
    actor_name: string;
    message: string;
  }): Promise<SendActorMessageResponse> {
    return this.sendRequest("send_actor_message", params);
  }

  async interruptActor(params: {
    actor_name: string;
    reason?: string;
  }): Promise<InterruptActorResponse> {
    return this.sendRequest("interrupt_actor", {
      actor_name: params.actor_name,
      reason: params.reason ?? "user_actor_interrupt",
    });
  }

  async listAsyncTasks(params?: { statuses?: string[] }): Promise<AsyncTaskState[]> {
    return this.sendRequest("list_async_tasks", {
      statuses: params?.statuses ?? [],
    });
  }

  async readAsyncTaskOutput(params: {
    async_task_id: string;
    max_lines?: number;
  }): Promise<AsyncTaskOutput> {
    return this.sendRequest("read_async_task_output", {
      async_task_id: params.async_task_id,
      max_lines: params.max_lines ?? 200,
    });
  }

  /**
   * 切换审批策略（default/accept）。不重建 root agent，因此不需要
   * agent_type / model 参数。
   */
  async switchPermissionMode(params: {
    permission_mode: string;
  }): Promise<SessionStatus> {
    return this.sendRequest("switch_permission_mode", params);
  }

  /**
   * 切换执行模式（agent/plan/team/group）。同一 Runner 保留 root 身份和
   * 会话快照，并只在空闲边界重建其有效工具面。
   */
  async switchAgentMode(params: {
    agent_mode: string;
    agent_type?: string;
    model_name?: string;
    model_effort?: string;
  }): Promise<SessionStatus> {
    return this.sendRequest("switch_agent_mode", params);
  }

  async listModels(params: {
    base_dir?: string;
    runtime_config_path?: string;
    model_name?: string;
  } = {}): Promise<RuntimeModelInfo[]> {
    return this.sendRequest("list_models", params);
  }

  async switchModel(params: {
    model_name: string;
    model_effort?: string;
    runtime_config_path?: string;
  }): Promise<SessionStatus> {
    return this.sendRequest("switch_model", params);
  }

  async stopSession(): Promise<{ stopped: boolean }> {
    return this.sendRequest("stop_session", {});
  }

  // 中断当前 stream_message; 后端在下一 step 边界协作退出，
  // 不影响 session 的连续性，可立即继续后续对话。
  async cancelStream(): Promise<{ cancelled: boolean }> {
    const requestIds = [...this.streamHandlers.keys()];
    const requestId = requestIds.length > 0 ? Math.max(...requestIds) : null;
    return this.sendRequest("cancel_stream", {
      ...(requestId === null ? {} : { request_id: requestId }),
    });
  }

  async listSessions(params: {
    base_dir: string;
  }): Promise<SessionSummary[]> {
    return this.sendRequest("list_sessions", params);
  }

  async listGraphs(params: { base_dir?: string } = {}): Promise<Record<string, any>> {
    return this.sendRequest("list_graphs", params);
  }

  async viewGraph(params: { base_dir?: string; name: string }): Promise<Record<string, any>> {
    return this.sendRequest("view_graph", params);
  }

  async runGraph(params: {
    base_dir?: string;
    name: string;
    payload: Record<string, any>;
    config?: Record<string, any>;
  }): Promise<Record<string, any>> {
    return this.sendRequest("run_graph", params);
  }

  async listGraphRuns(params: { base_dir?: string } = {}): Promise<Record<string, any>> {
    return this.sendRequest("list_graph_runs", params);
  }

  async controlGraphRun(params: {
    base_dir?: string;
    graph_run_id: string;
    action: "pause" | "resume" | "stop" | "restart";
  }): Promise<Record<string, any>> {
    return this.sendRequest("control_graph_run", params);
  }

  async permissionStatus(params: { base_dir?: string } = {}): Promise<Record<string, any>> {
    return this.sendRequest("permission_status", params);
  }

  async listSkills(params: {
    category?: string;
  } = {}): Promise<SkillsListResponse> {
    return this.sendRequest("list_skills", params);
  }

  async listPlugins(): Promise<PluginsListResponse> {
    return this.sendRequest("list_plugins", {});
  }

  async listAvailableAgents(params: {
    name?: string;
    mode_id?: string;
  } = {}): Promise<AvailableAgentsResponse> {
    return this.sendRequest("list_available_agents", params);
  }

  async listTeams(params: { base_dir?: string } = {}): Promise<TeamsListResponse> {
    return this.sendRequest("list_team_configs", params);
  }

  async getTeam(params: { team_name: string; base_dir?: string }): Promise<TeamManifest> {
    return this.sendRequest("get_team_config", params);
  }

  async listModeResources(params: {
    base_dir?: string;
    mode_id?: string;
  } = {}): Promise<ModeResourcesResponse> {
    return this.sendRequest("list_mode_resources", params);
  }

  async viewSkill(params: {
    name: string;
    file_path?: string;
  }): Promise<SkillViewResponse> {
    return this.sendRequest("view_skill", params);
  }

  async viewPlugin(params: {
    name: string;
    file_path?: string;
  }): Promise<PluginViewResponse> {
    return this.sendRequest("view_plugin", params);
  }

  async skillsConfigStatus(params: {
    base_dir?: string;
  } = {}): Promise<SkillsConfigStatusResponse> {
    return this.sendRequest("skills_config_status", params);
  }

  async setSkillsConfig(params: {
    base_dir?: string;
    enabled?: boolean;
    disabled: string[];
  }): Promise<SkillsConfigStatusResponse> {
    return this.sendRequest("set_skills_config", params);
  }

  async selfEvolutionConfigStatus(params: { base_dir?: string } = {}): Promise<{ success: boolean; enabled: boolean }> {
    return this.sendRequest("self_evolution_config_status", params);
  }

  async setSelfEvolutionConfig(params: { base_dir?: string; enabled: boolean }): Promise<{ success: boolean; enabled: boolean }> {
    return this.sendRequest("set_self_evolution_config", params);
  }

  async graphsConfigStatus(params: { base_dir?: string } = {}): Promise<{ success: boolean; enabled: boolean }> {
    return this.sendRequest("graphs_config_status", params);
  }

  async setGraphsConfig(params: { base_dir?: string; enabled: boolean }): Promise<{ success: boolean; enabled: boolean }> {
    return this.sendRequest("set_graphs_config", params);
  }

  async memoryStatus(): Promise<MemoryStatusResponse> {
    return this.sendRequest("memory_status", {});
  }

  async memorySearch(params: {
    query: string;
    limit?: number;
  }): Promise<MemorySearchResponse> {
    return this.sendRequest("memory_search", params);
  }

  async memoryView(params: {
    path?: string;
  } = {}): Promise<MemoryViewResponse> {
    return this.sendRequest("memory_view", params);
  }

  async runDream(): Promise<DreamRunResponse> {
    return this.sendRequest("run_dream", {});
  }

  async createCronTask(params: {
    base_dir?: string;
    cron: string;
    prompt: string;
    recurring?: boolean;
  }): Promise<CronTask> {
    return this.sendRequest("create_cron_task", params);
  }

  async listCronTasks(params: {
    base_dir?: string;
  } = {}): Promise<CronTaskListResponse> {
    return this.sendRequest("list_cron_tasks", params);
  }

  async deleteCronTask(params: {
    base_dir?: string;
    id: string;
  }): Promise<CronTaskDeleteResponse> {
    return this.sendRequest("delete_cron_task", params);
  }

  async fireDueCronTasks(params: {
    base_dir?: string;
    now?: number;
  } = {}): Promise<CronTaskFireResponse> {
    return this.sendRequest("fire_due_cron_tasks", params);
  }

  async cronStatus(params: {
    base_dir?: string;
  } = {}): Promise<CronStatusResponse> {
    return this.sendRequest("cron_status", params);
  }

  async setGoal(params: {
    objective: string;
    max_turns?: number;
    max_runtime_seconds?: number;
  }): Promise<GoalSummary | Record<string, never>> {
    return this.sendRequest("set_goal", params);
  }

  async getGoal(): Promise<GoalSummary | Record<string, never>> {
    return this.sendRequest("get_goal", {});
  }

  async pauseGoal(): Promise<GoalSummary | Record<string, never>> {
    return this.sendRequest("pause_goal", {});
  }

  async resumeGoal(): Promise<GoalSummary | Record<string, never>> {
    return this.sendRequest("resume_goal", {});
  }

  async clearGoal(): Promise<{ status: string }> {
    return this.sendRequest("clear_goal", {});
  }

  async setMemoryConfig(params: {
    feature: "memory" | "dream";
    enabled: boolean;
  }): Promise<MemoryStatusResponse> {
    return this.sendRequest("set_memory_config", params);
  }

  async setBrowserConfig(params: {
    base_dir?: string;
    enabled: boolean;
  }): Promise<BrowserConfigResponse> {
    return this.sendRequest("set_browser_config", params);
  }

  async setImageConfig(params: {
    base_dir?: string;
    enabled: boolean;
  }): Promise<ImageConfigResponse> {
    return this.sendRequest("set_image_config", params);
  }

  async loadWorkspaceConfig(params: { base_dir: string }): Promise<WorkspaceRuntimeConfig> {
    return this.sendRequest("load_workspace_config", params);
  }

  async saveWorkspaceConfig(params: {
    base_dir: string;
    config: WorkspaceRuntimeConfig;
  }): Promise<{ saved: boolean }> {
    return this.sendRequest("save_workspace_config", params);
  }

  async answerAsk(params: {
    request_id: string;
    response: AskResponse;
  }): Promise<{ accepted: boolean }> {
    return this.sendRequest("answer_ask", params);
  }

  async streamMessage(
    message: string,
    onStep: (event: RunnerStreamEvent) => void,
    onAskRequest?: (request: AskRequest) => void,
    agentModeOverride?: string
  ): Promise<StreamMessageResult> {
    const id = ++this.requestId;
    this.streamHandlers.set(id, onStep);
    if (onAskRequest) {
      this.askHandlers.set(id, onAskRequest);
    }

    try {
      return await this.sendRequest("stream_message", {
        message,
        ...(agentModeOverride ? { agent_mode_override: agentModeOverride } : {}),
      }, id) as StreamMessageResult;
    } finally {
      this.streamHandlers.delete(id);
      this.askHandlers.delete(id);
    }
  }

  stop(): void {
    if (this.process) {
      this.process.kill();
      this.process = null;
    }
    if (this.rl) {
      this.rl.close();
      this.rl = null;
    }
  }
}
