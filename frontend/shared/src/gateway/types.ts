/**
 * Shared gateway protocol types consumed by the CLI and browser app.
 */

export interface SessionStatus {
  runner_id: string;
  /** 审批策略维度：default | accept */
  permission_mode: string;
  /** 执行模式维度：agent | plan | team | group */
  agent_mode: string;
  root_agent_name: string;
  base_dir: string;
  started: boolean;
  resumed: boolean;
  agent_type: string;
  model_name: string;
  model_effort: string;
  backend: string;
  provider_model_name: string;
  goal?: GoalSummary | null;
  worktree?: WorktreeStatus | null;
}

export interface PendingSessionRuntime {
  base_dir: string;
  permission_mode: string;
  agent_mode: string;
  agent_type: string;
  model_name: string;
  model_effort: string;
}

/** Per-step candidates; reward model selection is configured independently. */
export interface StrategyConfiguration {
  breadth?: number;
  reset?: boolean;
}

/** Reward model state returned by configure_reward_model, including effective execution. */
export interface RewardModelConfiguration {
  model_name: string | null;
  scorer_configured: boolean;
  breadth: number;
  execution: "linear" | "candidate_selection";
}

export interface StrategySwitchResult {
  status: "pending" | "applied";
  strategy: {
    type: "greedy" | "exploration";
    params: { breadth?: number };
    execution: "linear" | "candidate_selection";
    scorer_configured: boolean;
  };
  message: string;
}

export interface WorktreeStatus {
  name: string;
  slug: string;
  path: string;
  branch: string;
  base_sha: string;
  head_sha: string;
  dirty: boolean;
  ahead: number;
  exists: boolean;
  active: boolean;
  temporary: boolean;
  error: string;
}

export interface WorktreeListResponse {
  worktrees: WorktreeStatus[];
  count: number;
}

export interface GoalSummary {
  objective: string;
  status: "active" | "paused" | "completed" | "budget_limited" | string;
  turns_used: number;
  max_turns: number;
  elapsed_seconds: number;
  max_runtime_seconds: number;
  latest_evaluator_reason: string;
  pause_reason?: string;
}

export interface RuntimeModelInfo {
  model_name: string;
  backend: string;
  provider_model_name: string;
  api_base?: string;
  api_key_env?: string;
  supported_efforts?: string[];
  thinking_mode?: "adaptive" | "manual" | "none" | string;
  supports_budget_tokens?: boolean;
}

export interface SessionSummary {
  runner_id: string;
  permission_mode: string;
  agent_mode: string;
  root_agent_name: string;
  updated_at: string;
  root_dir: string;
  first_user_request_preview?: string;
  goal_status?: string;
  goal_objective_preview?: string;
}

export interface ObservationImage {
  image_url: string;
  description: string;
}

export interface ToolCallDisplay {
  name: string;
  args: Record<string, any>;
  mode: string;
  status: "success" | "error" | "unknown" | string;
  observation_index: number | null;
  is_terminal: boolean;
  max_observation_chars?: number | null;
}

export interface AskOption {
  label: string;
  value: string;
  description?: string;
}

export interface AskRequest {
  request_id: string;
  question: string;
  options: AskOption[];
  multiple: boolean;
  allow_custom: boolean;
  timeout_seconds?: number | null;
}

export interface AskResponse {
  status: "answered" | "cancelled" | "error";
  request_id: string;
  question?: string;
  selected: AskOption[];
  custom_response: string;
  error: string;
}

export interface ActionStep {
  step_num: number;
  model_output: string;
  thought: string;
  tool_calls: ToolCallDisplay[];
  code_action: string;
  reasoning_content: string;
  observations: string[];
  observation_limits?: number[];
  request_bytes?: number;
  usage?: Record<string, any> | null;
  observation_images: ObservationImage[];
  attachments: Record<string, any>[];
  error: string | null;
  round_outcome: "continue" | "submitted" | "failed";
  output: unknown;
}

export type ActiveAgentStep =
  | {
      type: "task";
      task: string;
      task_images?: Record<string, any>[];
      attachments?: Record<string, any>[];
    }
  | ({
      type: "action";
    } & ActionStep)
  | {
      type: "summary";
      content: string;
    }
  | Record<string, any>;

export interface ActiveAgentSnapshot {
  agent_id: string;
  agent_name: string;
  agent_kind: string;
  agent_role: string;
  status: string;
  is_root: boolean;
  current_async_task_id: string;
  metadata: Record<string, any>;
  session: Record<string, any>;
  steps: ActiveAgentStep[];
}

export interface ActiveAgentsReport {
  runner_id: string;
  permission_mode: string;
  agent_mode: string;
  root_agent_name: string;
  agents: ActiveAgentSnapshot[];
}

export interface SendAgentMessageResponse {
  accepted: boolean;
  agent_name: string;
  message_id: string;
  delivery: "actor_attachment" | string;
  started_async_task_id?: string;
  status?: string;
  error?: string;
}

export interface InterruptAgentResponse {
  interrupted: boolean;
  agent_name: string;
  async_task_id?: string;
  status: "interrupted" | "idle" | "not_live" | string;
  reason: string;
}

/** Public snapshot returned by the runner async-task control plane. */
export interface AsyncTaskState {
  async_task_id: string;
  type: string;
  status: string;
  owner_agent_id: string;
  owner_agent_name: string;
  description: string;
  output_dir: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  closed_reason: string;
  error: string;
  metadata: Record<string, any>;
}

export interface AsyncTaskOutput {
  async_task_id: string;
  output_dir: string;
  status: string;
  events: Record<string, any>[];
  latest_result: unknown;
  stdout_tail: string;
  stderr_tail: string;
  max_lines: number;
}

export interface RunnerLifecycleEvent {
  scope: string;
  event: string;
  workflow_name?: string;
  workflow_run_id?: string;
  node?: string;
  agent_role?: string;
  completed?: number;
  total?: number;
  detail?: string;
}

/** Task eligibility is fixed when the root creates a task; claimed_by records execution. */
export interface TeamTaskSnapshot {
  task_id: string;
  title: string;
  description: string;
  eligible_members: string[];
  claimed_by: string;
  dependencies: string[];
  status: "pending" | "in_progress" | "completed";
  result?: string;
  error?: string;
}

export interface TeamMemberSnapshot {
  name: string;
  status: string;
  busy?: boolean;
}

/** TeamManager owns this snapshot; both gateways forward it unchanged. */
export interface TeamStreamEvent {
  team_name?: string;
  actor?: string;
  update?: Record<string, unknown>;
  snapshot?: {
    team_name?: string;
    tasks?: TeamTaskSnapshot[];
    members?: TeamMemberSnapshot[];
    finished?: boolean;
  };
}

export interface RunnerStreamEvent {
  kind: "action_step" | "action_event" | "team_update" | "runner_lifecycle" | "round_end" | "stream_cancelled";
  permission_mode: string;
  agent_mode: string;
  runner_id: string;
  agent_name: string | null;
  agent_role: string | null;
  step_num: number | null;
  action_step: ActionStep | null;
  // action_event 字段
  event_type?: string; // "thinking" | "tool_start" | "tool_end" | "step_complete"
  data?: Record<string, any>;
  // 原有字段
  team_event: TeamStreamEvent | null;
  lifecycle_event?: RunnerLifecycleEvent | null;
  round_id: string;
  outcome?: "submitted" | "failed" | "";
  output?: unknown;
  reason?: string;
  stop_reason?: string;
  agent_sessions_report?: ActiveAgentsReport | null;
}

export interface StreamMessageResult {
  done: true;
  stopped?: boolean;
  stop_reason?: string;
}

/** stdio notifications carry the originating JSON-RPC request explicitly. */
export interface StreamStepNotification {
  request_id: number | string | null;
  event: RunnerStreamEvent;
}

export interface AskRequestNotification {
  request_id: number | string | null;
  request: AskRequest;
}

export interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params: Record<string, any>;
}

export interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number | string;
  result?: any;
  error?: { code: number; message: string };
}

export interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params: any;
}

/** Layered read results and sparse project patches share the same YAML shape.
 * load_effective_config reads defaults → user → project → launch overrides;
 * save_workspace_config accepts only fields the user actually changed.
 */
export interface WorkspaceRuntimeConfig {
  runtime?: {
    permission_mode?: string;
    agent_mode?: string;
    agent_type?: string;
    model_name?: string;
    model_effort?: string;
  };
  memory?: {
    enabled?: boolean;
    dream?: {
      enabled?: boolean;
    };
  };
  browser?: {
    enabled?: boolean;
  };
  image?: {
    enabled?: boolean;
  };
  self_evolution?: {
    enabled?: boolean;
  };
  workflows?: {
    enabled?: boolean;
  };
  [key: string]: any;
}

export interface BrowserConfigResponse {
  enabled: boolean;
  runner_id: string;
}

export interface ImageConfigResponse {
  enabled: boolean;
  runner_id: string;
}

export interface CapabilityConfigResponse {
  success: boolean;
  enabled: boolean;
}

export interface SkillInfo {
  name: string;
  qualified_name: string;
  description: string;
  category?: string | null;
  version?: string | null;
  source: string;
  skill_dir?: string;
  skill_file?: string;
  read_only: boolean;
  platforms?: string[];
  conditions?: Record<string, string[]>;
  metadata?: Record<string, any>;
}

export interface SkillsListResponse {
  success: boolean;
  skills: SkillInfo[];
  categories: string[];
  category_descriptions: Record<string, string>;
  count: number;
  hint?: string;
}

export interface PluginInfo {
  schema_version: number;
  name: string;
  version: string;
  description: string;
  skills?: string[];
  root: string;
  source: string;
  enabled: boolean;
  skill_roots: string[];
  skill_names: string[];
  [key: string]: any;
}

export interface PluginsListResponse {
  success: boolean;
  plugins: PluginInfo[];
  diagnostics: Array<Record<string, any>>;
  count: number;
  hint?: string;
}

export interface PluginViewResponse {
  success: boolean;
  name: string;
  plugin?: PluginInfo;
  file_path?: string | null;
  content?: string;
}

/**
 * A declaration visible to the current Runner/mode. Root is deliberately
 * omitted from child discovery even though Registry also resolves its declaration.
 */
export interface AvailableAgentInfo {
  name: string;
  description: string;
  allowed_modes: string[] | null;
  source: string;
  /** Serialized effective AgentConfig, rendered on `/agents <name>`. */
  config: Record<string, unknown>;
}

export interface AvailableAgentsResponse {
  mode_id: string;
  agents: AvailableAgentInfo[];
}

/** A read-only summary of a schema-2 Team manifest. */
export interface TeamInfo {
  team_name: string;
  description: string;
  member_names: string[];
  source: "local" | "builtin";
  manifest_path: string | null;
}

export interface TeamsListResponse {
  teams: TeamInfo[];
}

/** Full Team relationship returned by `/teams <name>`. */
export interface TeamManifest {
  schema_version: number;
  team_name: string;
  description: string;
  member_names: string[];
  source: "local" | "builtin";
  manifest_path: string | null;
  /** Explicit shared Agent sources; omitted by legacy all-shared manifests. */
  shared_agent_names?: Record<string, string>;
}

export interface SkillConfigInfo extends SkillInfo {
  enabled: boolean;
}

export interface SkillsConfigStatusResponse {
  success: boolean;
  enabled: boolean;
  disabled: string[];
  skills: SkillConfigInfo[];
  count: number;
}

export interface SkillViewResponse {
  success: boolean;
  name?: string;
  qualified_name?: string;
  content?: string;
  raw_content?: string;
  path?: string;
  skill_dir?: string;
  metadata?: Record<string, any>;
  linked_files?: Record<string, string[]>;
  available_files?: string[];
  error?: string;
}

export interface MemoryStatusResponse {
  enabled: boolean;
  dream_enabled: boolean;
  memory_dir: string;
  entrypoint: string;
  topic_count: number;
  last_dream_at?: string | null;
  auto_dream_due?: boolean;
  next_dream_reason?: string;
  eligible_session_count?: number;
  dream_lock_owner?: string;
}

export interface MemorySearchHit {
  path: string;
  line: number;
  snippet: string;
}

export interface MemorySearchResponse {
  enabled?: boolean;
  hits: MemorySearchHit[];
  error?: string;
}

export interface MemoryViewResponse {
  enabled?: boolean;
  path: string;
  content: string;
  error?: string;
}

export interface DreamRunResponse {
  status: string;
  async_task_id?: string;
  job_id?: string;
  kind?: string;
  summary?: string;
  output_dir?: string;
  error?: string;
  lock_owner?: string;
}

export interface CronTask {
  id: string;
  cron: string;
  prompt: string;
  created_at: number;
  last_fired_at?: number;
  recurring?: boolean;
  next_run_at?: number | null;
  fired_at?: number;
}

export interface CronTaskListResponse {
  tasks: CronTask[];
}

export interface CronTaskFireResponse {
  fired: CronTask[];
}

export interface CronTaskDeleteResponse {
  deleted: boolean;
  id: string;
}

export interface CronStatusResponse {
  enabled: boolean;
  tasks_path: string;
  lock_path: string;
  task_count: number;
  next_run_at?: number | null;
  lock_present: boolean;
}

export interface BrowserPreview {
  connected: boolean;
  title: string;
  url: string;
  image_url: string;
  session_key?: string;
  active_tab_id?: string;
  tabs?: BrowserTabInfo[];
  can_go_back?: boolean;
  can_go_forward?: boolean;
  external_window?: boolean;
  headless?: boolean;
  viewport?: {
    width: number;
    height: number;
  };
  stream_state?: "empty" | "artifact" | "live" | "error" | string;
}

export interface BrowserTabInfo {
  tab_id: string;
  url: string;
  title: string;
  active: boolean;
}

export interface FileTreeNode {
  name: string;
  path: string;
  type: "directory" | "file";
  children?: FileTreeNode[];
}

export interface ClientCommand {
  id: string;
  type:
    | "start_session"
    | "resume_session"
    | "describe_session"
    | "switch_permission_mode"
    | "switch_agent_mode"
    | "switch_model"
    | "list_models"
    | "stream_message"
    | "answer_ask"
    | "list_sessions"
    | "archive_runner"
    | "restore_runner"
    | "list_archived_sessions"
    | "stop_session"
    | "cancel_stream"
    | "list_skills"
    | "list_plugins"
    | "list_available_agents"
    | "list_team_configs"
    | "get_team_config"
    | "view_skill"
    | "view_plugin"
    | "skills_config_status"
    | "set_skills_config"
    | "self_evolution_config_status"
    | "set_self_evolution_config"
    | "workflows_config_status"
    | "set_workflows_config"
    | "set_image_config"
    | "memory_status"
    | "memory_search"
    | "memory_view"
    | "run_dream"
    | "goal_status"
    | "set_goal"
    | "get_goal"
    | "pause_goal"
    | "resume_goal"
    | "clear_goal"
    | "create_cron_task"
    | "list_cron_tasks"
    | "delete_cron_task"
    | "cron_status"
    | "list_workflows"
    | "view_workflow"
    | "run_workflow"
    | "permission_status"
    | "describe_agent_sessions"
    | "send_agent_message"
    | "interrupt_agent";
  payload?: Record<string, unknown>;
}

export type ServerEvent =
  | { id: string; type: "result"; payload: unknown }
  | { id: string; type: "error"; error: { message: string } }
  | { id: string; type: "stream_step"; payload: RunnerStreamEvent }
  | { id?: string; type: "ask_request"; payload: AskRequest };
