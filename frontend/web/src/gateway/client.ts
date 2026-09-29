import type {
  AskRequest,
  ActorSessionsReport,
  BrowserPreview,
  ClientCommand,
  FileTreeNode,
  InterruptActorResponse,
  RuntimeModelInfo,
  ServerEvent,
  SessionStatus,
  SessionSummary,
  SendActorMessageResponse,
  SkillsListResponse,
  PluginsListResponse,
  PluginViewResponse,
  SkillsConfigStatusResponse,
  AvailableAgentsResponse,
  TeamManifest,
  TeamsListResponse,
  ModeResourcesResponse,
  WorkspaceRuntimeConfig,
  RunnerStreamEvent,
  StreamMessageResult,
} from "@juice-agents/shared/gateway/types";

export interface BootstrapPayload {
  base_dir: string;
  workspace_config: WorkspaceRuntimeConfig;
  sessions: SessionSummary[];
  models: RuntimeModelInfo[];
  memory: Record<string, unknown>;
}

export interface FileReadPayload {
  path: string;
  content: string;
  language: string;
  kind?: "text" | "image";
  image_url?: string;
  media_type?: string;
}

type ResultHandler = {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
};

export class WebGatewayClient {
  private socket: WebSocket | null = null;
  private requestId = 0;
  private pending = new Map<string, ResultHandler>();
  private streamHandlers = new Map<string, (event: RunnerStreamEvent) => void>();
  private askHandlers = new Map<string, (request: AskRequest) => void>();
  private ambientStreamHandler: ((event: RunnerStreamEvent) => void) | null = null;
  private ambientAskHandler: ((request: AskRequest) => void) | null = null;

  constructor(private readonly httpBase = "http://127.0.0.1:8003") {}

  get wsBase(): string {
    return this.httpBase.replace(/^http/, "ws");
  }

  browserLiveSocketUrl(baseDir: string, runnerId = ""): string {
    const params = new URLSearchParams({ base_dir: baseDir });
    if (runnerId) {
      params.set("runner_id", runnerId);
    }
    return `${this.wsBase}/ws/browser/live?${params.toString()}`;
  }

  browserExternalSocketUrl(baseDir: string, runnerId = ""): string {
    const params = new URLSearchParams({ base_dir: baseDir, stream: "0" });
    if (runnerId) {
      params.set("runner_id", runnerId);
    }
    return `${this.wsBase}/ws/browser/live?${params.toString()}`;
  }

  async bootstrap(baseDir: string): Promise<BootstrapPayload> {
    return this.getJson(`/api/bootstrap?base_dir=${encodeURIComponent(baseDir)}`);
  }

  saveWorkspaceConfig(baseDir: string, config: WorkspaceRuntimeConfig): Promise<{ saved: boolean }> {
    return this.putJson("/api/workspace-config", { base_dir: baseDir, config });
  }

  async fileTree(baseDir: string): Promise<{ base_dir: string; tree: FileTreeNode }> {
    return this.getJson(`/api/files/tree?base_dir=${encodeURIComponent(baseDir)}`);
  }

  async readFile(baseDir: string, path: string): Promise<FileReadPayload> {
    return this.getJson(
      `/api/files/read?base_dir=${encodeURIComponent(baseDir)}&path=${encodeURIComponent(path)}`
    );
  }

  async browserLivePreview(baseDir: string, runnerId = ""): Promise<BrowserPreview> {
    const params = new URLSearchParams({ base_dir: baseDir });
    if (runnerId) {
      params.set("runner_id", runnerId);
    }
    return this.getJson(`/api/browser/live/preview?${params.toString()}`);
  }

  async browserLiveOpenExternal(baseDir: string, runnerId = "", url = ""): Promise<BrowserPreview> {
    return this.postJson("/api/browser/live/open-external", { base_dir: baseDir, runner_id: runnerId, url });
  }

  async browserLiveNewTab(baseDir: string, runnerId: string, url = "", makeActive = true): Promise<BrowserPreview> {
    return this.postJson("/api/browser/live/new-tab", {
      base_dir: baseDir,
      runner_id: runnerId,
      url,
      make_active: makeActive,
    });
  }

  browserAssetUrl(path: string): string {
    if (!path) return "";
    if (/^https?:\/\//.test(path)) return path;
    return `${this.httpBase}${path}`;
  }

  fileAssetUrl(path: string): string {
    if (!path) return "";
    if (/^https?:\/\//.test(path)) return path;
    return `${this.httpBase}${path}`;
  }

  async connect(params?: {
    onStream: (event: ServerEvent) => void;
    onAsk: (request: AskRequest) => void;
  }): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN) return;
    if (params) {
      this.setAmbientStreamHandler((event) => params.onStream({ id: "", type: "stream_step", payload: event }));
      this.setAmbientAskHandler(params.onAsk);
    }
    this.socket = new WebSocket(`${this.wsBase}/ws`);
    this.socket.onmessage = (event) => this.handleMessage(event.data);
    await new Promise<void>((resolve, reject) => {
      if (!this.socket) return reject(new Error("socket missing"));
      let settled = false;
      const fail = (error: Error) => {
        this.handleDisconnect(error);
        if (!settled) {
          settled = true;
          reject(error);
        }
      };
      this.socket.onopen = () => {
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      this.socket.onerror = () => {
        fail(new Error("failed to connect web gateway"));
      };
      this.socket.onclose = () => fail(new Error("web gateway socket closed"));
    });
  }

  setAmbientStreamHandler(handler: ((event: RunnerStreamEvent) => void) | null): void {
    this.ambientStreamHandler = handler;
  }

  setAmbientAskHandler(handler: ((request: AskRequest) => void) | null): void {
    this.ambientAskHandler = handler;
  }

  command<T = unknown>(type: ClientCommand["type"], payload: Record<string, unknown> = {}): Promise<T> {
    return this.sendCommand(type, payload);
  }

  private sendCommand<T = unknown>(
    type: ClientCommand["type"],
    payload: Record<string, unknown>,
    reservedId?: string,
  ): Promise<T> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("web gateway is not connected"));
    }
    const id = reservedId ?? String(++this.requestId);
    const command: ClientCommand = { id, type, payload };
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (value: unknown) => void, reject });
      this.socket?.send(JSON.stringify(command));
    });
  }

  describeActorSessions(): Promise<ActorSessionsReport> {
    return this.command<ActorSessionsReport>("describe_actor_sessions", {});
  }

  sendActorMessage(params: { actor_name: string; message: string }): Promise<SendActorMessageResponse> {
    return this.command<SendActorMessageResponse>("send_actor_message", params);
  }

  interruptActor(params: { actor_name: string; reason?: string }): Promise<InterruptActorResponse> {
    return this.command<InterruptActorResponse>("interrupt_actor", {
      actor_name: params.actor_name,
      reason: params.reason ?? "user_actor_interrupt",
    });
  }

  describeSession(): Promise<SessionStatus> {
    return this.command<SessionStatus>("describe_session", {});
  }

  archiveRunner(baseDir: string, runnerId: string): Promise<{ archived: boolean; runner_id: string }> {
    return this.command("archive_runner", { base_dir: baseDir, runner_id: runnerId });
  }

  restoreRunner(baseDir: string, runnerId: string): Promise<{ restored: boolean; runner_id: string }> {
    return this.command("restore_runner", { base_dir: baseDir, runner_id: runnerId });
  }

  listArchivedSessions(baseDir: string): Promise<SessionSummary[]> {
    return this.command<SessionSummary[]>("list_archived_sessions", { base_dir: baseDir });
  }

  stopSession(): Promise<{ stopped: boolean }> {
    return this.command("stop_session", {});
  }

  // 中断当前 stream_message; 后端在下一 step 边界协作退出，
  // 不影响 session 持久化、可立即继续后续对话。
  cancelStream(): Promise<{ cancelled: boolean }> {
    const requestIds = [...this.streamHandlers.keys()];
    const requestId = requestIds.length > 0
      ? requestIds.reduce((latest, current) => Number(current) > Number(latest) ? current : latest)
      : "";
    return this.command("cancel_stream", requestId ? { request_id: requestId } : {});
  }

  async streamMessage(
    message: string,
    onEvent: (event: RunnerStreamEvent) => void,
    onAsk?: (request: AskRequest) => void,
    agentModeOverride?: string,
  ): Promise<StreamMessageResult> {
    const id = String(++this.requestId);
    this.streamHandlers.set(id, onEvent);
    if (onAsk) this.askHandlers.set(id, onAsk);
    try {
      return await this.sendCommand<StreamMessageResult>("stream_message", {
        message,
        ...(agentModeOverride ? { agent_mode_override: agentModeOverride } : {}),
      }, id);
    } finally {
      this.streamHandlers.delete(id);
      this.askHandlers.delete(id);
    }
  }

  listModels(baseDir: string): Promise<RuntimeModelInfo[]> {
    return this.command<RuntimeModelInfo[]>("list_models", { base_dir: baseDir });
  }

  listSessions(baseDir: string): Promise<SessionSummary[]> {
    return this.command<SessionSummary[]>("list_sessions", { base_dir: baseDir });
  }

  listGraphs(baseDir: string): Promise<Record<string, unknown>> {
    return this.command("list_graphs", { base_dir: baseDir });
  }

  viewGraph(baseDir: string, name: string): Promise<Record<string, unknown>> {
    return this.command("view_graph", { base_dir: baseDir, name });
  }

  runGraph(baseDir: string, name: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.command("run_graph", { base_dir: baseDir, name, payload });
  }

  listGraphRuns(baseDir: string): Promise<Record<string, unknown>> {
    return this.command("list_graph_runs", { base_dir: baseDir });
  }

  controlGraphRun(baseDir: string, graphRunId: string, action: string): Promise<Record<string, unknown>> {
    return this.command("control_graph_run", { base_dir: baseDir, graph_run_id: graphRunId, action });
  }

  permissionStatus(baseDir: string): Promise<Record<string, unknown>> {
    return this.command("permission_status", { base_dir: baseDir });
  }

  /** 切换审批策略；不重建 root agent。 */
  switchPermissionMode(permissionMode: string): Promise<SessionStatus> {
    return this.command<SessionStatus>("switch_permission_mode", {
      permission_mode: permissionMode,
    });
  }

  /** 切换执行模式；同一 Runner 在空闲边界刷新 root 的有效配置。 */
  switchAgentMode(agentMode: string, agentType = "react"): Promise<SessionStatus> {
    return this.command<SessionStatus>("switch_agent_mode", {
      agent_mode: agentMode,
      agent_type: agentType,
    });
  }

  listSkills(params: { category?: string } = {}): Promise<SkillsListResponse> {
    return this.command<SkillsListResponse>("list_skills", params);
  }

  listPlugins(): Promise<PluginsListResponse> {
    return this.command<PluginsListResponse>("list_plugins", {});
  }

  listAvailableAgents(params: { name?: string; mode_id?: string } = {}): Promise<AvailableAgentsResponse> {
    return this.command<AvailableAgentsResponse>("list_available_agents", params);
  }

  listTeams(params: { base_dir?: string } = {}): Promise<TeamsListResponse> {
    return this.command<TeamsListResponse>("list_team_configs", params);
  }

  getTeam(params: { team_name: string; base_dir?: string }): Promise<TeamManifest> {
    return this.command<TeamManifest>("get_team_config", params);
  }

  listModeResources(baseDir: string, modeId = ""): Promise<ModeResourcesResponse> {
    return this.command<ModeResourcesResponse>("list_mode_resources", {
      base_dir: baseDir,
      ...(modeId ? { mode_id: modeId } : {}),
    });
  }

  viewSkill(name: string, filePath = ""): Promise<Record<string, unknown>> {
    return this.command("view_skill", {
      name,
      ...(filePath ? { file_path: filePath } : {}),
    });
  }

  viewPlugin(name: string, filePath = ""): Promise<PluginViewResponse> {
    return this.command<PluginViewResponse>("view_plugin", {
      name,
      ...(filePath ? { file_path: filePath } : {}),
    });
  }

  skillsConfigStatus(): Promise<SkillsConfigStatusResponse> {
    return this.command<SkillsConfigStatusResponse>("skills_config_status", {});
  }

  setSkillsConfig(params: { enabled?: boolean; disabled: string[] }): Promise<SkillsConfigStatusResponse> {
    return this.command<SkillsConfigStatusResponse>("set_skills_config", params);
  }

  graphsConfigStatus(baseDir: string): Promise<{ success: boolean; enabled: boolean }> {
    return this.command("graphs_config_status", { base_dir: baseDir });
  }

  selfEvolutionConfigStatus(baseDir: string): Promise<{ success: boolean; enabled: boolean }> {
    return this.command("self_evolution_config_status", { base_dir: baseDir });
  }

  setImageConfig(params: { enabled: boolean }): Promise<Record<string, unknown>> {
    return this.command("set_image_config", params);
  }

  memoryStatus(): Promise<Record<string, unknown>> {
    return this.command("memory_status", {});
  }

  memorySearch(query: string, limit = 20): Promise<Record<string, unknown>> {
    return this.command("memory_search", { query, limit });
  }

  memoryView(path = ""): Promise<Record<string, unknown>> {
    return this.command("memory_view", path ? { path } : {});
  }

  runDream(): Promise<Record<string, unknown>> {
    return this.command("run_dream", {});
  }

  setGoal(objective: string): Promise<Record<string, unknown>> {
    return this.command("set_goal", { objective });
  }

  getGoal(): Promise<Record<string, unknown>> {
    return this.command("get_goal", {});
  }

  pauseGoal(): Promise<Record<string, unknown>> {
    return this.command("pause_goal", {});
  }

  resumeGoal(): Promise<Record<string, unknown>> {
    return this.command("resume_goal", {});
  }

  clearGoal(): Promise<Record<string, unknown>> {
    return this.command("clear_goal", {});
  }

  createCronTask(baseDir: string, cron: string, prompt: string, recurring = true): Promise<Record<string, unknown>> {
    return this.command("create_cron_task", { base_dir: baseDir, cron, prompt, recurring });
  }

  listCronTasks(baseDir: string): Promise<Record<string, unknown>> {
    return this.command("list_cron_tasks", { base_dir: baseDir });
  }

  deleteCronTask(baseDir: string, id: string): Promise<Record<string, unknown>> {
    return this.command("delete_cron_task", { base_dir: baseDir, id });
  }

  cronStatus(baseDir: string): Promise<Record<string, unknown>> {
    return this.command("cron_status", { base_dir: baseDir });
  }

  close(): void {
    this.socket?.close();
    this.handleDisconnect(new Error("web gateway client closed"));
    this.socket = null;
  }

  private handleMessage(raw: string): void {
    const event = JSON.parse(raw) as ServerEvent;
    if (event.type === "ask_request") {
      if (!event.id) {
        this.ambientAskHandler?.(event.payload);
      } else {
        const handler = this.askHandlers.get(event.id);
        if (handler) handler(event.payload);
        else if (typeof window !== "undefined") {
          console.debug("Dropped late web ask_request", { requestId: event.id });
        }
      }
      return;
    }
    if (event.type === "stream_step") {
      if (!event.id) {
        this.ambientStreamHandler?.(event.payload);
      } else {
        const handler = this.streamHandlers.get(event.id);
        if (handler) handler(event.payload);
        else if (typeof window !== "undefined") {
          console.debug("Dropped late web stream_step", { requestId: event.id });
        }
      }
      return;
    }
    const pending = this.pending.get(event.id);
    if (!pending) return;
    this.pending.delete(event.id);
    if (event.type === "error") {
      pending.reject(new Error(event.error.message));
    } else {
      pending.resolve(event.payload);
    }
  }

  private handleDisconnect(error: Error): void {
    if (this.pending.size > 0 && typeof window !== "undefined") {
      console.warn("Web gateway transport disconnected", {
        error: error.message,
        pending: this.pending.size,
      });
    }
    for (const pending of this.pending.values()) {
      pending.reject(error);
    }
    this.pending.clear();
    this.streamHandlers.clear();
    this.askHandlers.clear();
  }

  private async getJson<T>(path: string): Promise<T> {
    const response = await fetch(`${this.httpBase}${path}`);
    if (!response.ok) {
      throw new Error(await this.errorMessage(response));
    }
    return response.json() as Promise<T>;
  }

  private async postJson<T>(path: string, payload: Record<string, unknown>): Promise<T> {
    const response = await fetch(`${this.httpBase}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await this.errorMessage(response));
    }
    return response.json() as Promise<T>;
  }

  private async putJson<T>(path: string, payload: Record<string, unknown>): Promise<T> {
    const response = await fetch(`${this.httpBase}${path}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await this.errorMessage(response));
    }
    return response.json() as Promise<T>;
  }

  private async errorMessage(response: Response): Promise<string> {
    try {
      const payload = (await response.json()) as { detail?: unknown; error?: { message?: unknown } };
      const detail = payload.detail ?? payload.error?.message;
      if (detail) {
        return String(detail);
      }
    } catch {
      // Some gateway errors are plain text or empty. Fall back to HTTP status.
    }
    return `${response.status} ${response.statusText}`;
  }
}
