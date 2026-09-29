/**
 * Main application component.
 */

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Box, Text, useInput } from "ink";
import { GatewayClient } from "./gateway/client.js";
import type {
  ActorSessionsReport,
  ActorSessionSnapshot,
  AskRequest,
  AskResponse,
  AsyncTaskState,
  PendingSessionRuntime,
  RuntimeModelInfo,
  SessionStatus,
} from "@juice-agents/shared/gateway/types";
import { ComposerDock } from "./components/ComposerDock.js";
import { CurrentWorkStatus } from "./components/CurrentWorkStatus.js";
import { QueuedMessages } from "./components/QueuedMessages.js";
import { AlternateScreen } from "./ink-ext/AlternateScreen.js";
import { VirtualScrollList } from "./ink-ext/VirtualScrollList.js";
import {
  flattenBlocksToRows,
  flattenIntroToRows,
  TranscriptRow,
  type RowVM,
} from "./lib/transcriptRows.js";
import { InteractiveSelector } from "./components/InteractiveSelector.js";
import { AsyncTasksDialog, sortAsyncTasks } from "./components/AsyncTasksDialog.js";
import { useTasksDialog } from "./hooks/useTasksDialog.js";
import type { AsyncTaskOutput } from "@juice-agents/shared/gateway/types";
import { AskPrompt } from "./components/AskPrompt.js";
import {
  ConfigPanel,
  createConfigPanelDraft,
  type ConfigApplyPlan,
  type ConfigPanelDraft,
} from "./components/ConfigPanel.js";
import {
  SkillsConfigPanel,
  createSkillsConfigDraft,
  type SkillsConfigApplyPlan,
  type SkillsConfigDraft,
} from "./components/SkillsConfigPanel.js";
import type { SelectorItem } from "./hooks/useInteractiveSelector.js";
import { useSession } from "./hooks/useSession.js";
import { useConversationStream } from "@juice-agents/shared/conversation";
import { useCompletion } from "./hooks/useCompletion.js";
import { useInteractiveSelector } from "./hooks/useInteractiveSelector.js";
import { createComposerController, type ComposerController } from "./lib/composer.js";
import { executeCommand, runtimeFromSession, type CommandResult } from "./lib/commands.js";
import {
  getCommandExecutionPolicy,
  getCommandToken,
  shouldQueueSubmission,
} from "./lib/commandScheduling.js";
import {
  closeConfigPanelLoad,
  createConfigPanelLoadState,
  openConfigPanelLoad,
  resolveConfigPanelLoad,
} from "./lib/configPanelLoad.js";
import { normalizeEffortForModel, normalizeModelEffort, type ModelEffortSource } from "./lib/modelEffort.js";
import {
  buildIntroPanel,
  presentActorSession,
  presentAgentTypeConfigSaved,
  shouldShowIntroPanel,
  type MessageBlock,
} from "./lib/presenter.js";
import { collectSkillAliases } from "./lib/skills.js";
import {
  computeOverlayBudget,
  COMPOSER_ROWS,
  STATUS_ROWS,
  TERMINAL_SCROLLBACK_GUARD_ROWS,
} from "./hooks/useOverlayHeightBudget.js";
import { MouseEmitterContext } from "./ink-ext/MouseEmitterContext.js";
import type { EventEmitter } from "node:events";
import { LoadingState } from "./components/design-system/index.js";

interface AppProps {
  client: GatewayClient;
  baseDir: string;
  permissionMode?: string;
  agentMode?: string;
  agentType?: string;
  resumeRunnerId?: string;
  worktree?: string;
  /** mouseAwareStdin 的 wheel 事件源；null = 非 TTY 或测试，退化为仅键盘滚动。 */
  mouseEmitter?: EventEmitter | null;
}

const ACTOR_SELECTOR_TITLE = "Select Actor Session";
const MIN_TRANSCRIPT_WINDOW_SIZE = 3;

/**
 * 把 permission_mode 落盘到 workspace `.juice/config.yaml`。
 *
 * 只携带 permission_mode：多写字段会覆盖 model_name / agent_mode 等由其他入口
 * 维护的持久化值。
 */
export async function persistPermissionMode(
  client: Pick<GatewayClient, "saveWorkspaceConfig">,
  baseDir: string,
  permissionMode: string
): Promise<void> {
  await client.saveWorkspaceConfig({
    base_dir: baseDir,
    config: { runtime: { permission_mode: permissionMode } },
  });
}

/**
 * 会话已经处于目标模式时应用 permission_mode：无需 RPC，但仍必须**落盘**。
 *
 * 会话态与配置文件可以不一致 —— 例如用 `--permission-mode accept` 启动而配置
 * 里没有该字段。此时用户再执行 `/permissions accept` 是在表达「记住它」，
 * 直接 return 会让这个意图静默丢失。
 */
export async function applyPermissionModeAlreadyActive(params: {
  client: Pick<GatewayClient, "saveWorkspaceConfig">;
  baseDir: string;
  permissionMode: string;
  setPendingMode: (mode: string | null) => void;
}): Promise<void> {
  const { client, baseDir, permissionMode, setPendingMode } = params;
  setPendingMode(null);
  await persistPermissionMode(client, baseDir, permissionMode);
}

/**
 * 会话尚未创建时应用 permission_mode：更新 pending 运行态并**落盘**。
 *
 * 冷启动阶段改的模式同样要被新会话继承。少了持久化这一步，`/permissions` 的
 * 选择就会在退出 CLI 后静默丢失（本函数存在的唯一理由，改动时勿拆分）。
 */
export async function applyPermissionModeWithoutSession(params: {
  client: Pick<GatewayClient, "saveWorkspaceConfig">;
  baseDir: string;
  permissionMode: string;
  setPendingMode: (mode: string | null) => void;
  setPendingRuntime: (
    updater: (current: PendingSessionRuntime) => PendingSessionRuntime
  ) => void;
}): Promise<void> {
  const { client, baseDir, permissionMode, setPendingMode, setPendingRuntime } = params;
  setPendingMode(null);
  setPendingRuntime((current) =>
    buildPendingRuntimeState({
      baseDir,
      permissionMode,
      agentMode: current.agent_mode,
      agentType: current.agent_type,
      modelName: current.model_name,
      modelEffort: current.model_effort,
    })
  );
  await persistPermissionMode(client, baseDir, permissionMode);
}

export function shouldRenderIntroPanel(params: {
  hasSession: boolean;
  firstVisibleIndex: number;
}): boolean {
  return params.hasSession && params.firstVisibleIndex === 0;
}

export function buildActorViewHint(activeActorName: string | null): string | null {
  return activeActorName
    ? `Viewing: ${activeActorName} · interactive · Esc return`
    : null;
}

/**
 * Shift+Tab 只在审批策略之间循环。plan 属执行模式维度，需通过
 * `/mode plan` 显式进入，避免一次按键改变 root agent 形态。
 */
export function getNextAgentMode(currentMode: string | null | undefined): "agent" | "plan" | "team" | "group" {
  // 常用执行模式按实用频率循环：agent → team → plan → group → agent
  if (currentMode === "agent") return "team";
  if (currentMode === "team") return "plan";
  if (currentMode === "plan") return "group";
  return "agent";
}

export interface InputLayerState {
  configActive: boolean;
  skillsConfigActive: boolean;
  selectorActive: boolean;
  tasksDialogActive: boolean;
  askActive: boolean;
  // streaming 期间 composer 仍可编辑，但 Esc/Ctrl+C 优先取消当前 stream。
  // streamingCancelActive 为 true 时，独立的 useInput 监听器拦截 Esc/Ctrl+C。
  streamingCancelActive: boolean;
  composerActive: boolean;
}

export function resolveInputLayer(params: {
  configOpen?: boolean;
  /** `/config` 正在读取快照时也必须抢占输入，才能让 Esc 即刻取消。 */
  configLoading?: boolean;
  skillsConfigOpen?: boolean;
  selectorOpen: boolean;
  tasksDialogOpen?: boolean;
  askPending: boolean;
  streaming?: boolean;
}): InputLayerState {
  const configActive = Boolean(params.configOpen || params.configLoading);
  const skillsConfigActive = !configActive && Boolean(params.skillsConfigOpen);
  const selectorActive = !configActive && !skillsConfigActive && params.selectorOpen;
  const askActive =
    !configActive && !skillsConfigActive && !selectorActive && params.askPending;
  const tasksDialogActive =
    !configActive &&
    !skillsConfigActive &&
    !selectorActive &&
    !askActive &&
    Boolean(params.tasksDialogOpen);
  // streaming 时 composer 仍可编辑（composerActive=true），但 streamingCancelActive 也为 true，
  // 让独立的 useInput 监听器优先拦截 Esc/Ctrl+C 用于取消 stream。
  const streamingCancelActive =
    !configActive && !skillsConfigActive && !selectorActive && !tasksDialogActive && !askActive && Boolean(params.streaming);
  return {
    configActive,
    skillsConfigActive,
    selectorActive,
    tasksDialogActive,
    askActive,
    streamingCancelActive,
    composerActive:
      !configActive && !skillsConfigActive && !selectorActive && !tasksDialogActive && !askActive,
  };
}

export function shouldRenderComposerDock(inputLayer: InputLayerState): boolean {
  return inputLayer.composerActive;
}

export function shouldJumpTranscriptForCommandResult(result: CommandResult): boolean {
  return (
    result.blocks.length > 0 ||
    Boolean(result.forwardMessage) ||
    Boolean(result.loadHistory)
  );
}

export function shouldEchoSlashCommand(commandText: string, result: CommandResult): boolean {
  return (
    commandText.trim().startsWith("/") &&
    !result.clearMessages &&
    !result.exitRequested &&
    !result.forwardMessage
  );
}

export function buildCommandStatus(input: string): { summary: string; items?: { label: string; value?: string }[] } {
  const normalized = input.replace(/\s+/g, " ").trim();
  const parts = normalized.split(" ");
  if (parts[0] !== "/worktree") {
    return { summary: "Running command..." };
  }
  const action = (parts[1] || "status").toLowerCase();
  if (action === "enter") {
    const name = parts.slice(2).join(" ").trim();
    return {
      summary: "Creating worktree...",
      items: name ? [{ label: "name", value: name }] : undefined,
    };
  }
  if (action === "exit") {
    return { summary: "Exiting worktree..." };
  }
  if (action === "list") {
    return { summary: "Listing worktrees..." };
  }
  if (action === "status") {
    return { summary: "Checking worktree status..." };
  }
  return { summary: "Running worktree command..." };
}

export function shouldPollCron(params: {
  exitRequested?: boolean;
  streaming?: boolean;
  queuedCount?: number;
  askPending?: boolean;
  configOpen?: boolean;
  selectorOpen?: boolean;
}): boolean {
  return !(
    params.exitRequested ||
    params.streaming ||
    (params.queuedCount || 0) > 0 ||
    params.askPending ||
    params.configOpen ||
    params.selectorOpen
  );
}

interface ConfigPanelState {
  initialDraft: ConfigPanelDraft;
  models: Awaited<ReturnType<GatewayClient["listModels"]>>;
}

interface SkillsConfigPanelState {
  initialDraft: SkillsConfigDraft;
}

interface DeferredModelSwitch {
  modelName: string;
  modelEffort: string;
}

export function resolveActorSelectorIdentity(
  actor: ActorSessionSnapshot
): { label: string; value: string } | null {
  const actorName = String(actor.actor_name || "").trim();
  const actorId = String(actor.actor_id || "").trim();
  const fallback = actorId ? `Unnamed actor · ${actorId}` : "";
  const value = actorName || actorId;
  const label = actorName || fallback;
  if (!value || !label) {
    return null;
  }
  return { label, value };
}

export function buildActorSelectorItems(
  report: ActorSessionsReport | null,
  activeActorName: string | null
): SelectorItem[] {
  const actors = report?.actors || [];
  const root = actors.find((actor) => actor.is_root)
    || actors.find((actor) => actor.actor_name === report?.root_actor_name);
  const rootName = root?.actor_name || report?.root_actor_name || "main";
  const items: SelectorItem[] = [
    {
      label: `Main · ${rootName}`,
      value: "",
      description: "primary transcript",
      active: activeActorName === null,
    },
  ];

  for (const actor of actors) {
    if (actor.is_root || actor.actor_name === rootName) {
      continue;
    }
    const identity = resolveActorSelectorIdentity(actor);
    if (!identity) {
      continue;
    }
    const description = [
      actor.actor_kind || actor.actor_role || "actor",
      actor.status || "unknown",
      actor.current_async_task_id || "",
    ].filter(Boolean).join(" · ");
    items.push({
      label: identity.label,
      value: identity.value,
      description,
      active: activeActorName === identity.value,
    });
  }

  if (items.length === 1) {
    items.push({
      label: "No subagent sessions yet",
      value: "__empty_actor_sessions__",
      description: "workers appear here after team/group registers them",
      active: false,
    });
  }

  return items;
}

export function resolveActorTranscriptMessages(
  report: ActorSessionsReport | null,
  activeActorName: string | null,
  mainMessages: MessageBlock[]
): MessageBlock[] {
  if (!activeActorName) {
    return mainMessages;
  }
  const actor = findActorSession(report, activeActorName);
  return presentActorSession(actor);
}

function loadHistoryFromActorSessions(
  report: ActorSessionsReport | null
): MessageBlock[] {
  if (!report || !report.actors || report.actors.length === 0) {
    return [];
  }
  const rootActor = report.actors.find((actor) => actor.is_root)
    || report.actors.find((actor) => actor.actor_name === report.root_actor_name);
  if (!rootActor || !rootActor.steps || rootActor.steps.length === 0) {
    return [];
  }
  const allBlocks = presentActorSession(rootActor);
  // Skip the first system block (actor status) to avoid duplication with presentStatus
  return allBlocks.slice(1);
}

function findActorSession(
  report: ActorSessionsReport | null,
  actorName: string | null
): ActorSessionSnapshot | null {
  if (!report || !actorName) {
    return null;
  }
  return report.actors.find((actor) => {
    const identity = resolveActorSelectorIdentity(actor);
    return actor.actor_name === actorName || identity?.value === actorName;
  }) || null;
}

function normalizePendingAgentType(agentMode: string, agentType: string | undefined): string {
  // All builtin profiles resolve their default protocol from the same runtime
  // setting, so a pending mode change must not silently rewrite agent_type.
  void agentMode;
  return agentType === "codeact" ? "codeact" : "react";
}

function buildPendingRuntimeState(params: {
  baseDir: string;
  permissionMode?: string;
  agentMode?: string;
  agentType?: string;
  modelName?: string;
  modelEffort?: string;
  modelEffortSource?: ModelEffortSource | null;
  fallbackModelName?: string;
}): PendingSessionRuntime {
  const agentMode = params.agentMode || "agent";
  return {
    base_dir: params.baseDir,
    permission_mode: params.permissionMode || "default",
    agent_mode: agentMode,
    agent_type: normalizePendingAgentType(agentMode, params.agentType),
    model_name: params.modelName || params.fallbackModelName || "",
    model_effort: normalizeEffortForModel(params.modelEffort, params.modelEffortSource),
  };
}

function buildPendingSessionPreview(runtime: PendingSessionRuntime): SessionStatus {
  return {
    runner_id: "",
    permission_mode: runtime.permission_mode,
    agent_mode: runtime.agent_mode,
    root_actor_name: "root_agent",
    base_dir: runtime.base_dir,
    started: false,
    resumed: false,
    agent_type: runtime.agent_type,
    model_name: runtime.model_name,
    model_effort: runtime.model_effort,
    backend: "pending",
    provider_model_name: "pending",
    goal: null,
  };
}

export function App({
  client,
  baseDir,
  permissionMode,
  agentMode,
  agentType,
  resumeRunnerId,
  worktree,
  mouseEmitter = null,
}: AppProps) {
  // alt-screen 架构下不再用 useStdout().write 直写 scrollback；
  // 终端尺寸直接读 process.stdout，resize 监听见下方 useEffect。
  const stdout = process.stdout;
  const session = useSession(client);
  const [runtimeModels, setRuntimeModels] = useState<RuntimeModelInfo[]>([]);
  const [skillNames, setSkillNames] = useState<string[]>([]);
  const [pluginNames, setPluginNames] = useState<string[]>([]);
  // `/agents` completion is driven by the same availability endpoint as the
  // command itself, so custom declarations appear without a frontend release.
  const [availableAgentNames, setAvailableAgentNames] = useState<string[]>([]);
  const runtimeModelNames = useMemo(
    () => runtimeModels.map((item) => item.model_name),
    [runtimeModels]
  );
  const completion = useCompletion(runtimeModelNames, skillNames, pluginNames, availableAgentNames);
  const selector = useInteractiveSelector();
  const tasksDialog = useTasksDialog();
  // 进入 detail 模式后异步加载的 stdout/stderr tail。
  const [taskDetailOutput, setTaskDetailOutput] = useState<AsyncTaskOutput | null>(null);
  const [taskDetailLoading, setTaskDetailLoading] = useState(false);
  const [taskDetailError, setTaskDetailError] = useState<string | null>(null);
  // 详情视图里展示 duration 时需要"现在"的时间戳；用 state + 1s tick 驱动重渲染。
  const [tasksNow, setTasksNow] = useState<number>(() => Date.now() / 1000);
  const [exitRequested, setExitRequested] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [inputCursor, setInputCursor] = useState(0);
  // ComposerDock is unmounted while overlays are open, so App owns the
  // history controller to keep ↑/↓ recall stable after selectors close.
  const [composerController, setComposerController] = useState<ComposerController>(() =>
    createComposerController()
  );
  const [queuedMessages, setQueuedMessages] = useState<string[]>([]);
  const [actorSessionsReport, setActorSessionsReport] = useState<ActorSessionsReport | null>(null);
  const [activeActorName, setActiveActorName] = useState<string | null>(null);
  // 仅缓存 active (pending/running) 后台任务，用于计数徽标和 /tasks 面板。
  const [asyncTasks, setAsyncTasks] = useState<AsyncTaskState[]>([]);
  const [pendingAsk, setPendingAsk] = useState<AskRequest | null>(null);
  // Config 加载是可取消的：generation ref 与 UI 状态机一起阻止晚到的 RPC
  // 结果在用户按 Esc 后重新打开面板。
  const [configPanelLoad, setConfigPanelLoad] = useState(() =>
    createConfigPanelLoadState<ConfigPanelState>()
  );
  const configPanelLoadGenerationRef = useRef(0);
  const configPanel = configPanelLoad.phase === "ready" ? configPanelLoad.data : null;
  const configPanelLoading = configPanelLoad.phase === "loading";
  const [skillsConfigPanel, setSkillsConfigPanel] = useState<SkillsConfigPanelState | null>(null);
  const [terminalRows, setTerminalRows] = useState<number | undefined>(
    () => stdout.rows
  );
  const [terminalColumns, setTerminalColumns] = useState<number | undefined>(
    () => stdout.columns
  );
  const [pendingRuntime, setPendingRuntime] = useState<PendingSessionRuntime>(() =>
    buildPendingRuntimeState({
      baseDir,
      permissionMode: permissionMode || "default",
      agentMode: agentMode || "agent",
      agentType: agentType || "react",
    })
  );
  // runtimeReady 守护首屏渲染：避免在 workspace 配置/模型列表异步加载完成前
  // IntroPanel 把空的 model_name 渲染成 "unknown-model · disabled"
  const [runtimeReady, setRuntimeReady] = useState<boolean>(false);
  // ensureSession 需要「等待」而不只是「读取」就绪状态：pendingRuntime 由异步
  // init() 从 workspace 配置填充，在它完成前建 runner 会用上不读配置的初始值，
  // 导致持久化的 permission_mode / agent_mode 被静默丢弃。
  const runtimeReadyPromiseRef = useRef<Promise<void> | null>(null);
  const runtimeReadyResolveRef = useRef<(() => void) | null>(null);
  if (runtimeReadyPromiseRef.current === null) {
    runtimeReadyPromiseRef.current = new Promise<void>((resolve) => {
      runtimeReadyResolveRef.current = resolve;
    });
  }
  const [commandStatus, setCommandStatus] = useState<{
    summary: string;
    items?: { label: string; value?: string }[];
  } | null>(null);
  const deferredModelSwitchRef = useRef<DeferredModelSwitch | null>(null);
  // Mode changes have their own latest-wins coordinator. The visible pending
  // state is separate from SessionStatus so users get immediate feedback even
  // when a plan transition must wait for the current round to finish.
  const [pendingMode, setPendingMode] = useState<string | null>(null);
  const requestedModeRef = useRef<string | null>(null);
  const modeSwitchInFlightRef = useRef(false);
  const processModeSwitchRef = useRef<() => void>(() => {});
  const sessionStatusRef = useRef<SessionStatus | null>(null);
  const pendingRuntimeRef = useRef(pendingRuntime);
  const streamInFlightRef = useRef(false);

  const dismissConfigPanel = useCallback(() => {
    // 先推进令牌再关闭，确保正在等待的 Promise 无法在下一帧复活 overlay。
    const generation = ++configPanelLoadGenerationRef.current;
    setConfigPanelLoad(closeConfigPanelLoad<ConfigPanelState>(generation));
  }, []);

  const handleStreamActorSessionsReport = useCallback(
    (report: ActorSessionsReport) => {
      setActorSessionsReport(report);
      selector.updateItemsIfOpen(
        ACTOR_SELECTOR_TITLE,
        buildActorSelectorItems(report, activeActorName)
      );
    },
    [activeActorName, selector]
  );

  const handleAskRequest = useCallback(
    (request: AskRequest | null) => {
      if (!request) {
        setPendingAsk(null);
        return;
      }
      selector.close();
      dismissConfigPanel();
      setSkillsConfigPanel(null);
      // Ask is the only overlay that requires an answer before the stream can
      // continue. Close tasks eagerly so approval input is never hidden.
      tasksDialog.close();
      setPendingAsk(request);
    },
    [dismissConfigPanel, selector, tasksDialog.close]
  );

  const messagesHook = useConversationStream(client, {
    onActorSessionsReport: handleStreamActorSessionsReport,
    onAskRequest: handleAskRequest,
  });
  // While a stale mode response is being reconciled, React may still render
  // the previous visible SessionStatus. Do not let that overwrite the
  // transport-authoritative status used to dispatch the latest target.
  if (!modeSwitchInFlightRef.current && !requestedModeRef.current) {
    sessionStatusRef.current = session.status;
  }
  pendingRuntimeRef.current = pendingRuntime;
  streamInFlightRef.current = messagesHook.streamInFlight;

  const processRequestedMode = useCallback(async () => {
    if (modeSwitchInFlightRef.current) {
      return;
    }

    const targetMode = requestedModeRef.current;
    if (!targetMode) {
      return;
    }

    const currentStatus = sessionStatusRef.current;
    if (!currentStatus) {
      requestedModeRef.current = null;
      await applyPermissionModeWithoutSession({
        client,
        baseDir,
        permissionMode: targetMode,
        setPendingMode,
        setPendingRuntime,
      });
      return;
    }

    const actualMode = currentStatus.permission_mode || "default";
    if (actualMode === targetMode) {
      requestedModeRef.current = null;
      await applyPermissionModeAlreadyActive({
        client,
        baseDir,
        permissionMode: targetMode,
        setPendingMode,
      });
      return;
    }

    // 权限模式与执行模式解耦后，切权限从不重建 root actor 或 profile，
    // 因此在 stream 进行中也能安全地在 step 边界应用，无需延后。

    modeSwitchInFlightRef.current = true;
    try {
      const status = await client.switchPermissionMode({
        permission_mode: targetMode,
      });
      // 持久化放在 coordinator 而不是各入口：/permissions、selector 与任何
      // 后续入口都经由此处，写在这里才不会有入口漏持久化。
      await persistPermissionMode(client, baseDir, targetMode);
      // switch_permission_mode 已返回权威 SessionStatus，复用它可避免在 stream
      // 之后追加 describe_session / actor refresh RPC。
      sessionStatusRef.current = status;
      if (requestedModeRef.current === targetMode) {
        // A response for an older target may update our transport-side actual
        // mode, but it must not overwrite the UI chosen by a newer request.
        session.setStatus(status);
        setPendingRuntime(runtimeFromSession(status, baseDir));
        requestedModeRef.current = null;
        setPendingMode(null);
      }
    } catch (e: any) {
      if (requestedModeRef.current === targetMode) {
        requestedModeRef.current = null;
        setPendingMode(null);
        const actualStatus = sessionStatusRef.current;
        if (actualStatus) {
          session.setStatus(actualStatus);
          setPendingRuntime(runtimeFromSession(actualStatus, baseDir));
        }
      }
      messagesHook.addMessage({
        kind: "error",
        text: `Permission mode switch failed: ${e.message}`,
      });
    } finally {
      modeSwitchInFlightRef.current = false;
      if (requestedModeRef.current) {
        queueMicrotask(() => processModeSwitchRef.current());
      }
    }
  }, [baseDir, client, messagesHook, session]);
  processModeSwitchRef.current = () => {
    void processRequestedMode();
  };

  const requestModeSwitch = useCallback((targetMode: string) => {
    requestedModeRef.current = targetMode;
    setPendingMode(targetMode);
    processModeSwitchRef.current();
  }, []);

  useEffect(() => {
    if (!messagesHook.streamInFlight) {
      processModeSwitchRef.current();
    }
  }, [messagesHook.streamInFlight]);

  const refreshCapabilityAliases = useCallback(async () => {
    const [skills, plugins, agents] = await Promise.all([
      client.listSkills({}),
      client.listPlugins(),
      client.listAvailableAgents({}),
    ]);
    setSkillNames(collectSkillAliases(skills.skills || []));
    setPluginNames((plugins.plugins || []).map((plugin) => plugin.name).filter(Boolean));
    setAvailableAgentNames((agents.agents || []).map((agent) => agent.name).filter(Boolean));
  }, [client]);

  const refreshActorSessions = useCallback(async (statusOverride?: SessionStatus | null) => {
    if (!statusOverride && !session.status) {
      setActorSessionsReport(null);
      setAsyncTasks([]);
      return null;
    }
    // 并发拉 actor sessions 与 active async tasks，互不阻塞。
    // 任一失败都不应影响另一路：单独 catch 后吞掉错误。
    const [report, tasks] = await Promise.all([
      client.describeActorSessions().catch(() => null),
      client
        .listAsyncTasks({ statuses: ["pending", "running"] })
        .catch(() => [] as AsyncTaskState[]),
    ]);
    if (report) {
      setActorSessionsReport(report);
    }
    setAsyncTasks(tasks);
    return report;
  }, [client, session.status]);

  const visibleMessages = useMemo(
    () => resolveActorTranscriptMessages(actorSessionsReport, activeActorName, messagesHook.messages),
    [actorSessionsReport, activeActorName, messagesHook.messages]
  );

  // resume / clear-and-load 历史：alt-screen 架构下历史块直接进 messages 数组，
  // 由 VirtualScrollList 统一摊平渲染（不再 inkWrite 直写 scrollback）。
  const commitResumedHistory = useCallback(
    (historyBlocks: MessageBlock[]) => {
      if (historyBlocks.length === 0) return;
      messagesHook.addMessages(historyBlocks);
    },
    [messagesHook.addMessages]
  );

  const applyDeferredModelSwitch = useCallback(async (options: { allowWhileStreamInFlight?: boolean } = {}) => {
    const deferred = deferredModelSwitchRef.current;
    if (
      !deferred ||
      (!options.allowWhileStreamInFlight && messagesHook.streamInFlight) ||
      !session.status
    ) {
      return;
    }
    deferredModelSwitchRef.current = null;
    try {
      await client.switchModel({
        model_name: deferred.modelName,
        model_effort: deferred.modelEffort,
      });
      const currentStatus = await session.refreshStatus();
      await refreshActorSessions(currentStatus ?? session.status);
    } catch (e: any) {
      messagesHook.addMessage({
        kind: "error",
        text: `Deferred model switch failed: ${e.message}`,
      });
    }
  }, [client, messagesHook, messagesHook.streamInFlight, refreshActorSessions, session]);

  useEffect(() => {
    if (!messagesHook.streamInFlight) {
      void applyDeferredModelSwitch();
    }
  }, [applyDeferredModelSwitch, messagesHook.streamInFlight]);

  useEffect(() => {
    if (!session.status) {
      return;
    }
    setPendingRuntime(runtimeFromSession(session.status, baseDir));
  }, [baseDir, session.status]);

  const ensureSession = useCallback(async () => {
    if (session.status) {
      return session.status;
    }

    // 等 init() 把 workspace 配置填进 pendingRuntime，否则会用初始默认值建 runner。
    await runtimeReadyPromiseRef.current;

    const nextStatus = await session.startSession({
      base_dir: baseDir,
      permission_mode: pendingRuntime.permission_mode,
      agent_mode: pendingRuntime.agent_mode,
      agent_type: pendingRuntime.agent_type,
      model_name: pendingRuntime.model_name || undefined,
      model_effort: normalizeModelEffort(pendingRuntime.model_effort),
      ...(worktree !== undefined ? { worktree } : {}),
    });
    setPendingRuntime(runtimeFromSession(nextStatus, baseDir));
    return nextStatus;
  }, [baseDir, pendingRuntime, session, worktree]);

  useEffect(() => {
    const init = async () => {
      try {
        const workspaceConfig = await client.loadWorkspaceConfig({ base_dir: baseDir });
        const runtimeConfig = workspaceConfig.runtime || {};
        const runtimeModels = await client.listModels({ base_dir: baseDir });
        setRuntimeModels(runtimeModels);

        if (resumeRunnerId) {
          const resumedStatus = await session.resumeSession({
            runner_id: resumeRunnerId,
            base_dir: baseDir,
            model_name: runtimeConfig.model_name,
            model_effort: normalizeModelEffort(runtimeConfig.model_effort),
          });
          setPendingRuntime(runtimeFromSession(resumedStatus, baseDir));
          const report = await refreshActorSessions(resumedStatus);
          const historyBlocks = loadHistoryFromActorSessions(report);
          commitResumedHistory(historyBlocks);
        } else {
          setPendingRuntime(
            buildPendingRuntimeState({
              baseDir,
              // 两个维度都继承 workspace 配置；CLI 入参优先，只影响本次启动。
              permissionMode: permissionMode || runtimeConfig.permission_mode || "default",
              agentMode: agentMode || runtimeConfig.agent_mode || "agent",
              agentType: agentType || runtimeConfig.agent_type || "react",
              modelName: runtimeConfig.model_name,
              modelEffort: runtimeConfig.model_effort,
              modelEffortSource: runtimeModels.find((item) => item.model_name === runtimeConfig.model_name) || runtimeModels[0],
              fallbackModelName: runtimeModels[0]?.model_name,
            })
          );
        }
        await refreshCapabilityAliases();
      } catch (e: any) {
        messagesHook.addMessage({
          kind: "error",
          text: `Failed to initialize CLI runtime: ${e.message}`,
        });
      } finally {
        // 无论加载成功还是失败，都解除首屏 IntroPanel 的等待态：
        // - 成功：pendingRuntime 已经被刷成真实模型，可以放心渲染
        // - 失败：错误已通过 messagesHook 透出，放行以免界面卡在占位
        setRuntimeReady(true);
        runtimeReadyResolveRef.current?.();
      }
    };

    init();
  }, []);

  useEffect(() => {
    completion.updateCandidates(inputValue, inputCursor);
  }, [inputValue, inputCursor, completion.updateCandidates]);

  useEffect(() => {
    const updateTerminalSize = () => {
      setTerminalRows(stdout.rows);
      setTerminalColumns(stdout.columns);
    };
    updateTerminalSize();
    stdout.on("resize", updateTerminalSize);
    return () => {
      stdout.off("resize", updateTerminalSize);
    };
  }, [stdout]);

  const openConfigPanel = useCallback(async () => {
    const generation = ++configPanelLoadGenerationRef.current;
    setConfigPanelLoad(openConfigPanelLoad<ConfigPanelState>(generation));

    try {
      // `/model` 已使用启动时的目录缓存；`/config` 也复用它，避免每次打开
      // 面板都多排一条串行 stdio RPC。初始化尚未完成时再按需读取一次即可。
      const modelsRequest = runtimeModels.length > 0
        ? Promise.resolve(runtimeModels)
        : client.listModels({ base_dir: baseDir });
      const [memory, workspaceConfig, models, skillsConfig, selfEvolutionConfig, graphsConfig] = await Promise.all([
        client.memoryStatus(),
        client.loadWorkspaceConfig({ base_dir: baseDir }),
        modelsRequest,
        client.skillsConfigStatus({ base_dir: baseDir }),
        client.selfEvolutionConfigStatus({ base_dir: baseDir }),
        client.graphsConfigStatus({ base_dir: baseDir }),
      ]);
      const currentStatus = session.status || buildPendingSessionPreview(pendingRuntime);
      const panel: ConfigPanelState = {
        initialDraft: createConfigPanelDraft({
          session: currentStatus,
          memory,
          models,
          skillsConfig,
          workspaceConfig,
          selfEvolutionEnabled: selfEvolutionConfig.enabled,
          graphsEnabled: graphsConfig.enabled,
        }),
        models,
      };

      setConfigPanelLoad((current) => resolveConfigPanelLoad(current, generation, panel));
    } catch (e: any) {
      // 取消后的旧请求失败不应向用户报错，也不能关闭一次新的打开尝试。
      if (configPanelLoadGenerationRef.current !== generation) {
        return;
      }
      setConfigPanelLoad(closeConfigPanelLoad<ConfigPanelState>(generation));
      messagesHook.addMessage({
        kind: "error",
        text: `Config load failed: ${e.message}`,
      });
    }
  }, [baseDir, client, messagesHook, pendingRuntime, runtimeModels, session.status]);

  const handleSubmit = useCallback(
    async (text: string, fromQueue = false) => {
      const commandToken = getCommandToken(text);
      const executionPolicy = getCommandExecutionPolicy(text);

      // Safe root-level commands keep their slash-command meaning in an actor
      // transcript, matching the global Ctrl+S/Ctrl+T shortcuts. Other input
      // remains an actor message exactly as before.
      if (activeActorName && executionPolicy !== "immediate") {
        try {
          const result = await client.sendActorMessage({
            actor_name: activeActorName,
            message: text,
          });
          const suffix = result.started_async_task_id
            ? ` · started ${result.started_async_task_id}`
            : "";
          messagesHook.addMessage({
            kind: result.accepted ? "system" : "error",
            text: result.accepted
              ? `Sent to ${activeActorName}${suffix}`
              : `Actor message rejected for ${activeActorName}: ${result.error || result.status || "not_live"}`,
          });
          const currentStatus = await session.refreshStatus();
          await refreshActorSessions(currentStatus ?? session.status);
        } catch (e: any) {
          messagesHook.addMessage({
            kind: "error",
            text: `Actor message failed: ${e.message || String(e)}`,
          });
        }
        return;
      }

      if (shouldQueueSubmission({
        input: text,
        fromQueue,
        streaming: messagesHook.streaming,
        queuedCount: queuedMessages.length,
      })) {
        setQueuedMessages((prev) => [...prev, text]);
        return;
      }

      if (commandToken) {
        try {
          if (executionPolicy === "fifo") {
            setCommandStatus(buildCommandStatus(text));
          }
          let result: CommandResult;
          try {
            result = await executeCommand(text, client, baseDir, {
              sessionStatus: session.status,
              pendingRuntime,
            });
          } finally {
            if (executionPolicy === "fifo") {
              setCommandStatus(null);
            }
          }

          if (result.pendingRuntime) {
            setPendingRuntime(result.pendingRuntime);
          }

          if (result.permissionModeSwitchRequest) {
            requestModeSwitch(result.permissionModeSwitchRequest.permission_mode);
          }

          if (result.clearMessages) {
            messagesHook.clearMessages();
          }

          if (shouldEchoSlashCommand(text, result)) {
            messagesHook.addMessage({ kind: "user", text });
          }

          // append-only 模型下不再"跳到底部"——所有完成的消息已经在 <Static> 中。
          // 仍然调用 helper 以保留对外契约，结果用作命令是否应立即 flush 的标记。
          // 实际的 flush 由监听 streamInFlight + messages.length 的 useEffect 接管。
          shouldJumpTranscriptForCommandResult(result);

          if (result.blocks.length > 0) {
            messagesHook.addMessages(result.blocks);
          }

          if (result.loadHistory) {
            try {
              const currentStatus = await session.refreshStatus();
              const report = await refreshActorSessions(currentStatus ?? session.status);
              const historyBlocks = loadHistoryFromActorSessions(report);
              commitResumedHistory(historyBlocks);
            } catch (e: any) {
              console.error("Failed to load history:", e);
            }
          }

          if (result.exitRequested) {
            setExitRequested(true);
            return;
          }

          if (result.forwardMessage) {
            await applyDeferredModelSwitch({ allowWhileStreamInFlight: true });
            const outcome = await messagesHook.sendMessage(result.forwardMessage, result.forwardAgentMode);
            if (outcome === "failed") return "retry" as const;
            if (outcome === "superseded") return;
            const currentStatus = await session.refreshStatus();
            await refreshActorSessions(currentStatus ?? session.status);
            return;
          }

          // Handle interactive actions
          if (result.interactiveAction === "model-selector") {
            const models = runtimeModels;
            if (models.length === 0) {
              messagesHook.addMessage({
                kind: "error",
                text: "Model catalog is still loading. Try /model again after initialization finishes.",
              });
              return;
            }
            const currentModel = session.status?.model_name || pendingRuntime.model_name;
            const currentModelInfo = models.find((m) => m.model_name === currentModel);
            selector.open(
              "Select Model",
              models.map((m) => ({
                label: m.model_name,
                value: m.model_name,
                description: `${m.backend} | ${m.provider_model_name}`,
                active: m.model_name === currentModel,
                backend: m.backend,
                supported_efforts: m.supported_efforts || ["disabled"],
              })),
              async (item, effort) => {
                const selectedEffort = normalizeEffortForModel(effort, item);
                await client.saveWorkspaceConfig({
                  base_dir: baseDir,
                  config: {
                    runtime: {
                      model_name: item.value,
                      model_effort: selectedEffort,
                    },
                  },
                });

                if (session.status) {
                  if (messagesHook.streamInFlight) {
                    deferredModelSwitchRef.current = {
                      modelName: item.value,
                      modelEffort: selectedEffort,
                    };
                    messagesHook.addMessage({
                      kind: "system",
                      text: `Saved model switch for next turn: ${item.value} · effort: ${selectedEffort}`,
                    });
                    return;
                  }
                  await client.switchModel({
                    model_name: item.value,
                    model_effort: selectedEffort,
                  });
                  const currentStatus = await session.refreshStatus();
                  await refreshActorSessions(currentStatus ?? session.status);
                  messagesHook.addMessage({
                    kind: "system",
                    text: `Switched to model: ${item.value} · effort: ${selectedEffort}`,
                  });
                  return;
                }

                setPendingRuntime((current) =>
                  buildPendingRuntimeState({
                    baseDir,
                    permissionMode: current.permission_mode,
                    agentMode: current.agent_mode,
                    agentType: current.agent_type,
                    modelName: item.value,
                    modelEffort: selectedEffort,
                    modelEffortSource: item,
                  })
                );
                messagesHook.addMessage({
                  kind: "system",
                  text: `Saved pending model: ${item.value} · effort: ${selectedEffort}`,
                });
              },
              { effort: normalizeEffortForModel(session.status?.model_effort || pendingRuntime.model_effort, currentModelInfo) }
            );
            return;
          }

          if (result.interactiveAction === "mode-selector") {
            const currentAgentMode = session.status?.agent_mode || pendingRuntime.agent_mode || "agent";
            const currentAgentType = session.status?.agent_type || pendingRuntime.agent_type || "react";
            const agentModeOptions = [
              { label: "agent", value: "agent", description: "Single agent" },
              { label: "plan", value: "plan", description: "Read-only planning" },
              { label: "team", value: "team", description: "Team" },
              { label: "group", value: "group", description: "Group" },
            ];
            selector.open(
              "Select Agent Mode",
              agentModeOptions.map((opt) => ({
                ...opt,
                active: opt.value === currentAgentMode,
              })),
              async (item) => {
                await client.saveWorkspaceConfig({
                  base_dir: baseDir,
                  config: { runtime: { agent_mode: item.value } },
                });

                if (session.status) {
                  await client.switchAgentMode({
                    agent_mode: item.value,
                    agent_type: currentAgentType,
                  });
                  const currentStatus = await session.refreshStatus();
                  await refreshActorSessions(currentStatus ?? session.status);
                  await refreshCapabilityAliases();
                } else {
                  setPendingRuntime((current) =>
                    buildPendingRuntimeState({
                      baseDir,
                      permissionMode: current.permission_mode,
                      agentMode: item.value,
                      agentType: current.agent_type,
                      modelName: current.model_name,
                      modelEffort: current.model_effort,
                    })
                  );
                }
                messagesHook.addMessage({
                  kind: "system",
                  text: `${session.status ? "Switched" : "Saved pending default"} agent mode: ${item.value}`,
                });
              }
            );
            return;
          }

          if (result.interactiveAction === "permissions-selector") {
            const currentPermissionMode =
              pendingMode || session.status?.permission_mode || pendingRuntime.permission_mode;
            const permissionOptions = [
              { label: "default", value: "default", description: "Ask before mutations" },
              { label: "accept", value: "accept", description: "Allow workspace edits" },
            ];
            selector.open(
              "Select Permission Mode",
              permissionOptions.map((opt) => ({
                ...opt,
                active: opt.value === currentPermissionMode,
              })),
              async (item) => {
                // 权限模式不重建 root agent，走 latest-wins coordinator。
                requestModeSwitch(item.value);
              }
            );
            return;
          }

          if (result.interactiveAction === "agent-type-selector") {
            const currentAgentType = pendingRuntime.agent_type || session.status?.agent_type || "react";
            const agentTypeOptions = [
              { label: "react", value: "react", description: "ReAct reasoning (thought + action)" },
              { label: "codeact", value: "codeact", description: "CodeAct reasoning (code-based actions)" },
            ];
            selector.open(
              "Select Agent Type",
              agentTypeOptions.map((opt) => ({
                ...opt,
                active: opt.value === currentAgentType,
              })),
              async (item) => {
                await client.saveWorkspaceConfig({
                  base_dir: baseDir,
                  config: { runtime: { agent_type: item.value } },
                });

                setPendingRuntime((current) =>
                  buildPendingRuntimeState({
                    baseDir,
                    permissionMode: current.permission_mode,
                    agentMode: current.agent_mode,
                    agentType: item.value,
                    modelName: current.model_name,
                    modelEffort: current.model_effort,
                  })
                );
                messagesHook.addMessage(presentAgentTypeConfigSaved(item.value));
              }
            );
            return;
          }

          if (result.interactiveAction === "resume-selector") {
            const sessions = await client.listSessions({ base_dir: baseDir });
            const currentRunnerId = session.status?.runner_id;

            if (sessions.length === 0) {
              messagesHook.addMessage({
                kind: "error",
                text: "No sessions available to resume",
              });
              return;
            }

            selector.open(
              "Select Session",
              sessions.map((s) => ({
                label: s.first_user_request_preview || s.goal_objective_preview || s.runner_id,
                value: s.runner_id,
                description: `${s.runner_id} | ${s.agent_mode || "agent"}/${s.permission_mode} | ${s.root_actor_name} | ${s.updated_at}`,
                active: s.runner_id === currentRunnerId,
              })),
              async (item) => {
                await client.resumeSession({
                  runner_id: item.value,
                  base_dir: baseDir,
                });
                const currentStatus = await session.refreshStatus();
                messagesHook.clearMessages();
                const report = await refreshActorSessions(currentStatus ?? session.status);
                const historyBlocks = loadHistoryFromActorSessions(report);
                commitResumedHistory(historyBlocks);
              }
            );
            return;
          }

          if (result.interactiveAction === "config-panel") {
            // Loading owns its own error handling and input layer. Do not keep the
            // command submit promise pending while the user is deciding to cancel.
            void openConfigPanel();
            return;
          }

          if (result.interactiveAction === "actor-selector") {
            handleToggleActors();
            return;
          }

          if (result.interactiveAction === "tasks-panel") {
            await handleToggleTasks();
            return;
          }

          if (result.syncSession) {
            const currentStatus = await session.refreshStatus();
            await refreshActorSessions(currentStatus ?? session.status);
            // `/agents` completion is mode-scoped. Refresh it with the
            // authoritative post-switch Runner state rather than retaining
            // names collected before `/mode` changed the available workers.
            await refreshCapabilityAliases();
          }
        } catch (e: any) {
          messagesHook.addMessage({
            kind: "error",
            text: `Command failed: ${e.message}`,
          });
          return "retry" as const;
        }
        return;
      }

      const currentStatus = await ensureSession();
      await applyDeferredModelSwitch({ allowWhileStreamInFlight: true });
      const outcome = await messagesHook.sendMessage(text);
      if (outcome === "failed") return "retry" as const;
      // A cancelled generation may settle after the next turn already started.
      // Only the still-current completion is allowed to refresh session state.
      if (outcome === "completed") {
        const refreshedStatus = await session.refreshStatus();
        await refreshActorSessions(refreshedStatus ?? currentStatus);
      }
    },
    [applyDeferredModelSwitch, activeActorName, baseDir, client, ensureSession, messagesHook, openConfigPanel, pendingRuntime, queuedMessages.length, refreshActorSessions, refreshCapabilityAliases, requestModeSwitch, runtimeModels, selector, session]
  );

  const queueFlushInFlight = useRef(false);
  const cronFireInFlight = useRef(false);

  useEffect(() => {
    if (
      messagesHook.streaming ||
      queuedMessages.length === 0 ||
      queueFlushInFlight.current
    ) {
      return;
    }

    const next = queuedMessages[0];
    queueFlushInFlight.current = true;
    void handleSubmit(next, true)
      .then(() => {
        // A failed stream is already represented in the transcript. This queue
        // item was attempted, so dequeue it and allow later messages to run.
        setQueuedMessages((prev) => prev.slice(1));
      })
      .finally(() => {
        queueFlushInFlight.current = false;
      });
  }, [handleSubmit, messagesHook.streaming, queuedMessages]);

  useEffect(() => {
    if (
      !shouldPollCron({
        exitRequested,
        streaming: messagesHook.streaming,
        queuedCount: queuedMessages.length,
        askPending: Boolean(pendingAsk),
        configOpen: Boolean(configPanelLoading || configPanel || skillsConfigPanel),
        selectorOpen: selector.isOpen,
      })
    ) {
      return;
    }

    const tick = async () => {
      if (cronFireInFlight.current) {
        return;
      }
      cronFireInFlight.current = true;
      try {
        const result = await client.fireDueCronTasks({ base_dir: baseDir });
        const fired = result.fired || [];
        if (fired.length === 0) {
          // 即使 cron 没事可做，也借这次心跳刷新一下任务徽标。
          // 失败不打扰用户，吞掉异常。
          try {
            const tasks = await client.listAsyncTasks({ statuses: ["pending", "running"] });
            setAsyncTasks(tasks);
          } catch {
            // ignore — 下一拍再试
          }
          return;
        }
        for (const task of fired) {
          messagesHook.addMessage({
            kind: "system",
            title: "Cron",
            text: `scheduled task fired: ${task.id} · ${task.cron}`,
          });
        }
        setQueuedMessages((prev) => [...prev, ...fired.map((task) => task.prompt).filter(Boolean)]);
      } catch (e: any) {
        // 过滤掉锁竞争错误（多实例并发时正常现象，无需展示）
        if (!e.message?.includes('cron lock is held')) {
          messagesHook.addMessage({
            kind: "error",
            text: `Cron tick failed: ${e.message}`,
          });
        }
      } finally {
        cronFireInFlight.current = false;
      }
    };

    const timer = setInterval(() => {
      void tick();
    }, 1000);
    void tick();
    return () => clearInterval(timer);
  }, [
    baseDir,
    client,
    configPanel,
    exitRequested,
    messagesHook,
    messagesHook.streaming,
    pendingAsk,
    queuedMessages.length,
    selector.isOpen,
    skillsConfigPanel,
  ]);

  const handleTab = useCallback(
    (cursor: number) => completion.accept(inputValue, cursor),
    [completion, inputValue]
  );

  const handleCycleMode = useCallback(async () => {
    const currentAgentMode = session.status?.agent_mode || pendingRuntime.agent_mode || "agent";
    const nextAgentMode = getNextAgentMode(currentAgentMode);

    // 切 agent_mode 会重建 root agent，不能走 latest-wins coordinator（那只适配 permission）。
    await client.saveWorkspaceConfig({
      base_dir: baseDir,
      config: { runtime: { agent_mode: nextAgentMode } },
    });

    if (session.status) {
      const currentAgentType = session.status.agent_type || "react";
      await client.switchAgentMode({
        agent_mode: nextAgentMode,
        agent_type: currentAgentType,
      });
      const currentStatus = await session.refreshStatus();
      await refreshActorSessions(currentStatus ?? session.status);
      await refreshCapabilityAliases();
    } else {
      setPendingRuntime((current) =>
        buildPendingRuntimeState({
          baseDir,
          permissionMode: current.permission_mode,
          agentMode: nextAgentMode,
          agentType: current.agent_type,
          modelName: current.model_name,
          modelEffort: current.model_effort,
        })
      );
    }
  }, [baseDir, client, pendingRuntime.agent_mode, pendingRuntime.agent_type, pendingRuntime.model_effort, pendingRuntime.model_name, pendingRuntime.permission_mode, refreshActorSessions, refreshCapabilityAliases, session, setPendingRuntime]);

  const handleEscape = useCallback(() => {
    if (activeActorName) {
      setActiveActorName(null);
      return;
    }
    completion.dismiss();
  }, [activeActorName, completion]);

  const handleExit = useCallback(() => {
    if (activeActorName) {
      client.interruptActor({ actor_name: activeActorName }).then((result) => {
        messagesHook.addMessage({
          kind: result.interrupted ? "system" : "error",
          text: result.interrupted
            ? `Interrupted ${activeActorName}`
            : `Actor interrupt skipped for ${activeActorName}: ${result.status}`,
        });
        return refreshActorSessions(session.status);
      }).catch((e: any) => {
        messagesHook.addMessage({
          kind: "error",
          text: `Actor interrupt failed: ${e.message || String(e)}`,
        });
      });
      return;
    }
    setExitRequested(true);
    client.stopSession().catch(() => {});
  }, [activeActorName, client, messagesHook, refreshActorSessions, session.status]);

  const handleToggleActors = useCallback(async () => {
    if (selector.isOpen) {
      selector.close();
      return;
    }
    const selectActorItem = async (item: SelectorItem) => {
      if (item.value === "__empty_actor_sessions__") {
        return;
      }
      setActiveActorName(item.value ? item.value : null);
    };

    selector.open(
      ACTOR_SELECTOR_TITLE,
      buildActorSelectorItems(actorSessionsReport, activeActorName),
      selectActorItem
    );
  }, [selector, actorSessionsReport, activeActorName]);

  // /tasks 与 Ctrl+T 都走这里。打开后立刻拉一次最新任务，确保面板看到的是新鲜数据。
  const handleToggleTasks = useCallback(async () => {
    if (tasksDialog.state.isOpen) {
      tasksDialog.close();
      return;
    }
    tasksDialog.open();
    try {
      const tasks = await client.listAsyncTasks({ statuses: ["pending", "running"] });
      setAsyncTasks(tasks);
    } catch {
      // 静默失败：列表会显示当前缓存的快照，cron 轮询下次还会刷新。
    }
  }, [client, tasksDialog]);

  // 面板打开时每秒驱动重渲染，让 duration 字段持续走表。
  useEffect(() => {
    if (!tasksDialog.state.isOpen) {
      return;
    }
    const timer = setInterval(() => {
      setTasksNow(Date.now() / 1000);
    }, 1000);
    return () => clearInterval(timer);
  }, [tasksDialog.state.isOpen]);

  // 列表长度变化时把选中下标钳到合法范围（任务结束会让列表缩短）。
  useEffect(() => {
    if (tasksDialog.state.isOpen && tasksDialog.state.mode === "list") {
      tasksDialog.clampSelectedIndex(asyncTasks.length);
    }
  }, [asyncTasks.length, tasksDialog]);

  // 进入 detail 时按 selectedTaskId 拉取 stdout/stderr 末尾，并在 detail 视图下每 2 秒刷新一次。
  useEffect(() => {
    const targetId = tasksDialog.state.selectedTaskId;
    if (!tasksDialog.state.isOpen || tasksDialog.state.mode !== "detail" || !targetId) {
      return;
    }
    let cancelled = false;
    let requestInFlight = false;
    const fetchOutput = async (initial: boolean) => {
      // Slow stdio reads must never accumulate behind a 2s polling timer.
      if (requestInFlight) {
        return;
      }
      requestInFlight = true;
      if (initial) {
        setTaskDetailLoading(true);
        setTaskDetailError(null);
        setTaskDetailOutput(null);
      }
      try {
        const output = await client.readAsyncTaskOutput({
          async_task_id: targetId,
          max_lines: 200,
        });
        if (cancelled) return;
        setTaskDetailOutput(output);
        setTaskDetailError(null);
      } catch (err: any) {
        if (cancelled) return;
        // 后台刷新失败不覆盖已有输出，只在初次加载时把错误打到面板。
        if (initial) {
          setTaskDetailError(String(err?.message || err || "failed"));
        }
      } finally {
        requestInFlight = false;
        if (!cancelled && initial) {
          setTaskDetailLoading(false);
        }
      }
    };
    void fetchOutput(true);
    const refresh = setInterval(() => {
      void fetchOutput(false);
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(refresh);
    };
  }, [client, tasksDialog.state.isOpen, tasksDialog.state.mode, tasksDialog.state.selectedTaskId]);

  const handleAskSubmit = useCallback(
    async (response: AskResponse) => {
      const requestId = pendingAsk?.request_id || response.request_id;
      try {
        await client.answerAsk({ request_id: requestId, response });
        // 仅在后端 accepted 后清掉 AskPrompt，避免 mismatch 时卡死。
        setPendingAsk(null);
      } catch (e: any) {
        // 后端拒绝（含 request_id mismatch）时保留 pendingAsk，用户可重新提交。
        messagesHook.addMessage({
          kind: "error",
          text: `Ask response failed: ${e.message}`,
        });
      }
    },
    [client, messagesHook, pendingAsk]
  );

  const handleConfigSave = useCallback(
    async (plan: ConfigApplyPlan, draft: ConfigPanelDraft) => {
      try {
        const hasLiveSession = Boolean(session.status);

        if (hasLiveSession) {
          if (plan.switchPermissionMode) {
            await client.switchPermissionMode(plan.switchPermissionMode);
          }
          if (plan.switchAgentMode) {
            await client.switchAgentMode(plan.switchAgentMode);
          }
          if (plan.switchModel) {
            await client.switchModel(plan.switchModel);
          }
        }
        if (Object.keys(plan.runtimePatch).length > 0) {
          await client.saveWorkspaceConfig({
            base_dir: baseDir,
            config: { runtime: plan.runtimePatch },
          });
        }
        for (const change of plan.memoryChanges) {
          await client.setMemoryConfig(change);
        }
        for (const change of plan.browserChanges) {
          await client.setBrowserConfig({ base_dir: baseDir, ...change });
        }
        for (const change of plan.imageChanges) {
          await client.setImageConfig({ base_dir: baseDir, ...change });
        }
        if (plan.skillsChange) {
          await client.setSkillsConfig({ base_dir: baseDir, ...plan.skillsChange });
          await refreshCapabilityAliases();
        }
        if (plan.selfEvolutionChange) {
          await client.setSelfEvolutionConfig({ base_dir: baseDir, ...plan.selfEvolutionChange });
        }
        if (plan.graphsChange) {
          await client.setGraphsConfig({ base_dir: baseDir, ...plan.graphsChange });
        }
        dismissConfigPanel();
        if (hasLiveSession) {
          const currentStatus = await session.refreshStatus();
          await refreshActorSessions(currentStatus ?? session.status);
          if (plan.switchAgentMode) {
            await refreshCapabilityAliases();
          }
        } else {
          setPendingRuntime(
            buildPendingRuntimeState({
              baseDir,
              permissionMode: pendingRuntime.permission_mode,
              agentMode: draft.agentMode,
              agentType: draft.agentType,
              modelName: draft.modelName,
              modelEffort: draft.modelEffort,
            })
          );
        }
        messagesHook.addMessage({
          kind: "system",
          text: plan.runtimePatch.agent_type
            ? `Config saved.\n${presentAgentTypeConfigSaved(plan.runtimePatch.agent_type).text}`
            : "Config saved.",
        });
      } catch (e: any) {
        messagesHook.addMessage({
          kind: "error",
          text: `Config save failed: ${e.message}`,
        });
      }
    },
    [baseDir, client, dismissConfigPanel, messagesHook, pendingRuntime.permission_mode, refreshActorSessions, refreshCapabilityAliases, session],
  );

  const handleSkillsConfigSave = useCallback(
    async (plan: SkillsConfigApplyPlan) => {
      try {
        await client.setSkillsConfig({ base_dir: baseDir, ...plan });
        await refreshCapabilityAliases();
        setSkillsConfigPanel(null);
        if (session.status) {
          const currentStatus = await session.refreshStatus();
          await refreshActorSessions(currentStatus ?? session.status);
        }
        messagesHook.addMessage({
          kind: "system",
          text: "Skills config saved.",
        });
      } catch (e: any) {
        messagesHook.addMessage({
          kind: "error",
          text: `Skills config save failed: ${e.message}`,
        });
      }
    },
    [baseDir, client, messagesHook, refreshActorSessions, refreshCapabilityAliases, session],
  );

  const handleCandidateUp = useCallback(() => {
    completion.selectPrev();
  }, [completion]);

  const handleCandidateDown = useCallback(() => {
    completion.selectNext();
  }, [completion]);

  const introPanel = buildIntroPanel(session.status || buildPendingSessionPreview(pendingRuntime));

  // IntroPanel 始终显示（只要有 session），不管消息多少。
  // runtimeReady 在 init() 完成（成功或失败）后才会置为 true，
  // 这样首屏不会出现 "unknown-model · disabled" 的半成品状态。
  const showIntroPanel = shouldShowIntroPanel({ introPanel, activeActorName, runtimeReady });
  const actorViewHint = buildActorViewHint(activeActorName);

  // Ink broadcasts each keypress to every active useInput subscriber. Keep
  // overlay prompts and the main composer mutually exclusive so Enter/Esc
  // cannot be handled by two components during the same frame.
  const inputLayer = resolveInputLayer({
    configOpen: Boolean(configPanel),
    configLoading: configPanelLoading,
    skillsConfigOpen: Boolean(skillsConfigPanel),
    selectorOpen: selector.isOpen,
    tasksDialogOpen: tasksDialog.state.isOpen,
    askPending: Boolean(pendingAsk),
    streaming: messagesHook.streaming,
  });

  // 新预算系统：统一计算 overlay + LiveTail 的高度分配
  const hasOverlay = Boolean(
    configPanelLoading || configPanel || skillsConfigPanel || selector.isOpen ||
    tasksDialog.state.isOpen || pendingAsk
  );
  const reservedExtraRows = queuedMessages.length;
  const budget = useMemo(
    () => computeOverlayBudget({
      terminalRows,
      hasOverlay,
      hasComposer: inputLayer.composerActive,
      reservedExtraRows,
    }),
    [terminalRows, hasOverlay, inputLayer.composerActive, reservedExtraRows]
  );
  // 动态区（transcript 虚拟滚动区）可用行数。alt-screen 下由 budget 统一约束，
  // 保证 transcript + status + queue + composer 总和 ≤ terminalRows - guard。
  const transcriptWindowSize = budget.transcriptMaxRows;

  // alt-screen + 虚拟滚动架构下，IntroPanel 与所有消息都进入 React 树的
  // VirtualScrollList（行级摊平 + 窗口裁剪），不再 inkWrite 直写 scrollback。
  // 旧的 IntroPanel 直写 effect、finalize effect、__rendered 标记全部移除——
  // 那套机制与 Ink log-update 动态区并存正是「重复显示 + 滚动上滑」的根因。
  //
  // transcript 摊平为扁平 RowVM[]：
  //   - 主会话：[可选 IntroPanel 行] + 全部消息行
  //   - actor 子视图：actor banner 行 + 该 actor 的 transcript 行
  // VirtualScrollList 按 viewportHeight 行级裁剪，保证渲染行数 ≤ 视口，永不触发
  // Ink 的 clearTerminal 灾难分支。
  const transcriptRows = useMemo<RowVM[]>(() => {
    const cols = terminalColumns || 80;
    if (activeActorName !== null) {
      // actor 只读视图：banner + 该 actor messages（visibleMessages 已是 actor transcript）
      const bannerRow: RowVM = {
        key: "actor-banner",
        prefix: "",
        prefixColor: "",
        text: actorViewHint || `Viewing: ${activeActorName}`,
        textColor: "#888888",
        dim: true,
      };
      return [bannerRow, ...flattenBlocksToRows(visibleMessages, cols, (_, i) => `a${i}`)];
    }
    const introRows =
      showIntroPanel && runtimeReady ? flattenIntroToRows(introPanel, cols) : [];
    const msgRows = flattenBlocksToRows(visibleMessages, cols, (b, i) => b.id ?? `m${i}`);
    return [...introRows, ...msgRows];
    // actorViewHint / introPanel 是每帧重算的轻量对象，纳入依赖确保内容变化即重算。
  }, [
    activeActorName,
    visibleMessages,
    showIntroPanel,
    runtimeReady,
    introPanel,
    terminalColumns,
    actorViewHint,
  ]);

  // streaming 期间独占 ESC / Ctrl+C：立即恢复 composer 并通知后端停止。
  useInput(
    (input, key) => {
      if (key.escape || (input === "c" && key.ctrl)) {
        // 后端 cancel_stream 会自动退出 ask 等待，前端同步清理 UI 状态。
        if (pendingAsk) {
          setPendingAsk(null);
        }
        void messagesHook.cancelCurrentStream().catch(() => {});
      }
    },
    { isActive: inputLayer.streamingCancelActive }
  );

  // Config 的数据还未到齐时，ConfigPanel 本身尚未挂载，因此由 App 接管 Esc。
  // 该 handler 只在 loading 期激活，不会与 ready 面板的 useInput 重复处理 Esc。
  useInput(
    (_input, key) => {
      if (key.escape) {
        dismissConfigPanel();
      }
    },
    { isActive: configPanelLoading }
  );
  const currentWorkStatusInteractive = !inputLayer.composerActive || completion.visible;

  if (exitRequested) {
    const runnerId = session.status?.runner_id;
    return (
      <Box flexDirection="column">
        {runnerId ? (
          <>
            <Text dimColor>To continue this session:</Text>
            <Text dimColor>  juice --resume {runnerId}</Text>
          </>
        ) : null}
      </Box>
    );
  }

  return (
    <MouseEmitterContext.Provider value={mouseEmitter}>
    <AlternateScreen>
      {/* alt-screen 内单一渲染管线：IntroPanel + 全部消息经 VirtualScrollList
          行级摊平 + 窗口裁剪，渲染行数恒 ≤ viewportHeight，永不触发 Ink 的
          clearTerminal 灾难分支。历史回看靠应用内滚动（PgUp/PgDn），不碰原生
          scrollback，从根上消除「重复显示 + 滚动上滑」竞态。 */}
      <Box
        flexDirection="column"
        paddingX={1}
        paddingBottom={1}
      >
        {transcriptWindowSize > 0 ? (
          <VirtualScrollList<RowVM>
            rows={transcriptRows}
            viewportHeight={transcriptWindowSize}
            rowKey={(r) => r.key}
            renderRow={(r) => <TranscriptRow row={r} />}
            // 主视图 + composer 活跃时启用滚动键（navOnly: 仅 PgUp/PgDn，
            // 与 composer 文本输入无冲突）；overlay/ask 打开时让出。
            scrollActive={inputLayer.composerActive}
            stickyBottom
          />
        ) : null}

      <CurrentWorkStatus
        streaming={messagesHook.streaming}
        interactive={currentWorkStatusInteractive}
        summary={commandStatus?.summary}
        items={commandStatus?.items}
        lifecycleEvent={messagesHook.lifecycleEvent}
      />
      <QueuedMessages queuedMessages={queuedMessages} />

      {configPanel && inputLayer.configActive && (
        <Box marginTop={1}>
          <ConfigPanel
            initialDraft={configPanel.initialDraft}
            models={configPanel.models}
            isActive={inputLayer.configActive}
            onSave={handleConfigSave}
            onCancel={dismissConfigPanel}
            maxRows={budget.overlayMaxRows}
          />
        </Box>
      )}

      {configPanelLoading && inputLayer.configActive && (
        <Box marginTop={1} marginBottom={1}>
          <LoadingState
            message="Loading configuration..."
            subtitle="Esc cancel"
            shimmer={false}
          />
        </Box>
      )}

      {skillsConfigPanel && inputLayer.skillsConfigActive && (
        <Box marginTop={1}>
          <SkillsConfigPanel
            initialDraft={skillsConfigPanel.initialDraft}
            isActive={inputLayer.skillsConfigActive}
            onSave={handleSkillsConfigSave}
            onCancel={() => setSkillsConfigPanel(null)}
            maxRows={budget.overlayMaxRows}
          />
        </Box>
      )}

      {selector.isOpen && inputLayer.selectorActive && (
        <Box marginTop={1}>
          <InteractiveSelector
            title={selector.title}
            items={selector.items}
            selectedIndex={selector.selectedIndex}
            isActive={inputLayer.selectorActive}
            onSelect={selector.confirm}
            onCancel={selector.cancel}
            onUp={selector.selectPrev}
            onDown={selector.selectNext}
            effort={selector.effort}
            onLeft={selector.selectEffortLeft}
            onRight={selector.selectEffortRight}
            maxRows={budget.overlayMaxRows}
          />
        </Box>
      )}

      {tasksDialog.state.isOpen && inputLayer.tasksDialogActive && (
        <Box marginTop={1}>
          <AsyncTasksDialog
            tasks={asyncTasks}
            isActive={inputLayer.tasksDialogActive}
            selectedIndex={tasksDialog.state.selectedIndex}
            mode={tasksDialog.state.mode}
            detailOutput={taskDetailOutput}
            detailLoading={taskDetailLoading}
            detailError={taskDetailError}
            detailTask={
              tasksDialog.state.mode === "detail" && tasksDialog.state.selectedTaskId
                ? asyncTasks.find(
                    (t) => t.async_task_id === tasksDialog.state.selectedTaskId
                  ) || null
                : null
            }
            now={tasksNow}
            onUp={tasksDialog.moveUp}
            onDown={() => tasksDialog.moveDown(asyncTasks.length - 1)}
            onEnter={() => {
              if (tasksDialog.state.mode === "list") {
                const sorted = sortAsyncTasks(asyncTasks);
                const target = sorted[tasksDialog.state.selectedIndex];
                if (target) {
                  tasksDialog.enterDetail(target.async_task_id);
                }
              }
            }}
            onCancel={() => {
              if (tasksDialog.state.mode === "detail") {
                tasksDialog.backToList();
              } else {
                tasksDialog.close();
              }
            }}
            maxRows={budget.overlayMaxRows}
          />
        </Box>
      )}

      {pendingAsk && inputLayer.askActive && (
        <Box marginTop={1}>
          <AskPrompt
            requestId={pendingAsk.request_id}
            question={pendingAsk.question}
            options={pendingAsk.options || []}
            multiple={Boolean(pendingAsk.multiple)}
            allowCustom={Boolean(pendingAsk.allow_custom)}
            isActive={inputLayer.askActive}
            onSubmit={handleAskSubmit}
            maxRows={budget.overlayMaxRows}
          />
        </Box>
      )}

      {shouldRenderComposerDock(inputLayer) ? (
        <Box flexDirection="column" marginTop={1}>
          <ComposerDock
            permissionMode={session.status?.permission_mode || pendingRuntime.permission_mode || "default"}
            pendingMode={pendingMode}
            agentMode={session.status?.agent_mode || pendingRuntime.agent_mode || "agent"}
            value={inputValue}
            onChange={setInputValue}
            cursor={inputCursor}
            onCursorChange={setInputCursor}
            controller={composerController}
            onControllerChange={setComposerController}
            onSubmit={handleSubmit}
            onTab={handleTab}
            onCycleMode={handleCycleMode}
            onEscape={handleEscape}
            onExit={handleExit}
            onToggleActors={handleToggleActors}
            onToggleTasks={handleToggleTasks}
            onCandidateUp={handleCandidateUp}
            onCandidateDown={handleCandidateDown}
            disabled={!inputLayer.composerActive}
            isInputActive={inputLayer.composerActive}
            loading={session.loading}
            candidates={completion.candidates}
            selectedIndex={completion.selectedIndex}
            completionVisible={completion.visible}
            ghostText={completion.ghostText}
            inlineHint={completion.inlineHint}
            actorViewHint={actorViewHint}
            asyncRunningCount={asyncTasks.length}
          />
        </Box>
      ) : null}
      </Box>
    </AlternateScreen>
    </MouseEmitterContext.Provider>
  );
}
