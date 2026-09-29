import React, { useEffect, useMemo, useRef, useState } from "react";
import type {
  ActorSessionsReport,
  AskOption,
  AskRequest,
  AskResponse,
  BrowserPreview,
  FileTreeNode,
  PluginInfo,
  AvailableAgentInfo,
  RuntimeModelInfo,
  RunnerStreamEvent,
  SessionStatus,
  SessionSummary,
  SkillInfo,
  SkillsConfigStatusResponse,
} from "@juice-agents/shared/gateway/types";
import { presentActorSession, type MessageBlock } from "@juice-agents/shared/presenter/stream";
import { useConversationStream } from "@juice-agents/shared/conversation";
import { WebGatewayClient, type BootstrapPayload } from "./gateway/client.js";
import { Sidebar } from "./components/Sidebar.js";
import { MODEL_EFFORT_OPTIONS, ThreadView, getEffortsForModel, type ModelEffort } from "./components/ThreadView.js";
import { PreviewPanel } from "./components/PreviewPanel.js";
import type { FilePreviewSelection } from "./components/FilePreview.js";
import { SearchPage } from "./components/SearchPage.js";
import { SkillsPage } from "./components/SkillsPage.js";
import { ArchivedRunnersPage } from "./components/ArchivedRunnersPage.js";
import {
  derivePreviewIntent,
  findActorByName,
  findRootActor,
  moveBrowserHistory,
  navigateBrowserTab,
  refreshBrowserTab,
  rootRunnerTitle,
  syncBrowserTabsForRunner,
  syncBrowserTabsFromPreview,
  type BrowserTabState,
  type PreviewFeature,
} from "./lib/layout.js";
import { executeWebCommand } from "./lib/commands.js";

const DEFAULT_GATEWAY = import.meta.env.VITE_JUICE_WEB_GATEWAY || "http://127.0.0.1:8003";
const PREVIEW_WIDTH_STORAGE_KEY = "juice-web.previewWidth";
const DEFAULT_PREVIEW_WIDTH = 560;
const MIN_PREVIEW_WIDTH = 360;
const MAX_PREVIEW_WIDTH = 900;
export type MainView = "thread" | "search" | "skills";
export type AppView = MainView | "settings";

function resolveBaseDir(): string {
  return new URLSearchParams(window.location.search).get("base_dir") || ".";
}

function normalizeModelEffort(value: unknown, model?: RuntimeModelInfo): ModelEffort {
  const normalized = String(value || "disabled").trim().toLowerCase();
  const candidate = MODEL_EFFORT_OPTIONS.includes(normalized as ModelEffort) ? (normalized as ModelEffort) : "disabled";
  const efforts = getEffortsForModel(model);
  return efforts.includes(candidate) ? candidate : efforts[0] || "disabled";
}

function resolveRuntimeModelName(models: RuntimeModelInfo[], configured?: unknown): string {
  const configuredName = String(configured || "").trim();
  if (configuredName && models.some((model) => model.model_name === configuredName)) {
    return configuredName;
  }
  return models[0]?.model_name || configuredName || "doubao_lite";
}

function clampPreviewWidth(value: number): number {
  return Math.min(Math.max(Math.round(value), MIN_PREVIEW_WIDTH), MAX_PREVIEW_WIDTH);
}

function loadPreviewWidth(): number {
  if (typeof window === "undefined") return DEFAULT_PREVIEW_WIDTH;
  const stored = Number(window.localStorage.getItem(PREVIEW_WIDTH_STORAGE_KEY));
  return Number.isFinite(stored) ? clampPreviewWidth(stored) : DEFAULT_PREVIEW_WIDTH;
}

export function App() {
  const baseDir = useMemo(resolveBaseDir, []);
  const client = useMemo(() => new WebGatewayClient(DEFAULT_GATEWAY), []);
  const bootstrapped = useRef(false);
  const queueFlushInFlight = useRef(false);
  const activeRunnerIdRef = useRef("");
  const [bootstrap, setBootstrap] = useState<BootstrapPayload | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [models, setModels] = useState<RuntimeModelInfo[]>([]);
  const [selectedModelName, setSelectedModelName] = useState("doubao_lite");
  const [selectedModelEffort, setSelectedModelEffort] = useState<ModelEffort>("disabled");
  const [runtimeSwitching, setRuntimeSwitching] = useState(false);
  const [status, setStatus] = useState<SessionStatus | null>(null);
  const [actorSessionsReport, setActorSessionsReport] = useState<ActorSessionsReport | null>(null);
  const [activeActorName, setActiveActorName] = useState<string | null>(null);
  const [queuedMessages, setQueuedMessages] = useState<string[]>([]);
  const [pendingAsk, setPendingAsk] = useState<AskRequest | null>(null);
  const [error, setError] = useState<string>("");
  const [fileTree, setFileTree] = useState<FileTreeNode | null>(null);
  const [browserPreview, setBrowserPreview] = useState<BrowserPreview | null>(null);
  // The panel feature and the resources shown inside each feature are separate
  // state domains. In particular, a workspace file can never become a browser
  // page tab, and browser tabs always mirror real Playwright pages.
  const [activePreviewFeature, setActivePreviewFeature] = useState<PreviewFeature>("browser");
  const [browserTabs, setBrowserTabs] = useState<BrowserTabState[]>([]);
  const [activeBrowserTabId, setActiveBrowserTabId] = useState("");
  const [selectedFile, setSelectedFile] = useState<FilePreviewSelection | null>(null);
  const [filePreviewError, setFilePreviewError] = useState("");
  const [previewCollapsed, setPreviewCollapsed] = useState(false);
  const [previewWidth, setPreviewWidth] = useState(loadPreviewWidth);
  const [activeView, setActiveView] = useState<AppView>("thread");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [availableAgents, setAvailableAgents] = useState<AvailableAgentInfo[]>([]);
  const [skillsConfig, setSkillsConfig] = useState<SkillsConfigStatusResponse | null>(null);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillsError, setSkillsError] = useState("");
  const [archivedSessions, setArchivedSessions] = useState<SessionSummary[]>([]);
  const [archivedLoading, setArchivedLoading] = useState(false);
  const [archivedError, setArchivedError] = useState("");
  const conversation = useConversationStream(client, {
    acceptEvent: acceptConversationEvent,
    onEvent: handleConversationEvent,
    onActorSessionsReport: setActorSessionsReport,
    onAskRequest: setPendingAsk,
  });
  const { messages, streaming } = conversation;

  const activeSession = sessions.find((session) => session.runner_id === status?.runner_id) || null;
  const selectedActor = findActorByName(actorSessionsReport, activeActorName);
  const visibleMessages = activeActorName ? presentActorSession(selectedActor) : messages;
  const threadTitle = activeActorName
    ? selectedActor?.actor_name || activeActorName
    : rootRunnerTitle(activeSession) || status?.runner_id || "New runner";
  const threadSubtitle = activeActorName
    ? `${selectedActor?.actor_kind || selectedActor?.actor_role || "actor"} · interactive`
    : undefined;
  const activeActorBusy = Boolean(activeActorName);
  const browserLiveSocketUrl = browserPreview?.external_window
    ? client.browserExternalSocketUrl(bootstrap?.base_dir || baseDir, status?.runner_id || "")
    : client.browserLiveSocketUrl(bootstrap?.base_dir || baseDir, status?.runner_id || "");

  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    void initialize();
  }, []);

  useEffect(() => {
    activeRunnerIdRef.current = status?.runner_id || "";
  }, [status?.runner_id]);

  useEffect(() => {
    window.localStorage.setItem(PREVIEW_WIDTH_STORAGE_KEY, String(previewWidth));
  }, [previewWidth]);

  useEffect(() => {
    if (
      streaming ||
      queuedMessages.length === 0 ||
      queueFlushInFlight.current
    ) return;
    const next = queuedMessages[0];
    queueFlushInFlight.current = true;
    void sendComposerText(next, true)
      .then((result) => {
        if (result !== "retry") {
          setQueuedMessages((prev) => prev.slice(1));
        }
      })
      .finally(() => {
        queueFlushInFlight.current = false;
      });
  }, [streaming, queuedMessages]);

  useEffect(() => {
    if (previewCollapsed || activePreviewFeature !== "browser") return;
    const timer = window.setInterval(() => {
      void refreshLiveBrowserMetadata();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activePreviewFeature, previewCollapsed, status?.runner_id]);

  async function initialize(): Promise<void> {
    try {
      const initial = await client.bootstrap(baseDir);
      const initialModelName = resolveRuntimeModelName(initial.models, initial.workspace_config.runtime?.model_name);
      const initialModelEffort = normalizeModelEffort(
        initial.workspace_config.runtime?.model_effort,
        initial.models.find((model) => model.model_name === initialModelName),
      );
      setBootstrap(initial);
      setSessions(initial.sessions);
      setModels(initial.models);
      setSelectedModelName(initialModelName);
      setSelectedModelEffort(initialModelEffort);
      const [tree, browser] = await Promise.all([
        client.fileTree(initial.base_dir),
        client.browserLivePreview(initial.base_dir),
      ]);
      setFileTree(tree.tree);
      setBrowserPreview(browser);
      await client.connect();
      void Promise.all([client.listSkills(), client.listPlugins(), client.listAvailableAgents()]).then(([skillResult, pluginResult, agentsResult]) => {
        setSkills(skillResult.skills || []);
        setPlugins(pluginResult.plugins || []);
        setAvailableAgents(agentsResult.agents || []);
      }).catch((exc) => {
        console.warn("Failed to preload completion aliases", exc);
      });
      if (initial.sessions[0]?.runner_id) {
        await resumeInitialRunner(initial.sessions[0].runner_id, initial.base_dir, initialModelName, initialModelEffort);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function resumeInitialRunner(
    runnerId: string,
    baseDirOverride: string,
    fallbackModelName: string,
    fallbackModelEffort: string
  ): Promise<void> {
    try {
      await resumeRunner(runnerId, {
        baseDirOverride,
        fallbackModelName,
        fallbackModelEffort,
      });
    } catch (exc) {
      console.warn("Failed to auto-resume initial runner", exc);
      activeRunnerIdRef.current = "";
      setStatus(null);
      conversation.clearMessages();
      setActorSessionsReport(null);
      setActiveActorName(null);
      setError("");
      const sessions = await client.command<SessionSummary[]>("list_sessions", { base_dir: baseDirOverride });
      setSessions(sessions);
    }
  }

  function acceptConversationEvent(event: RunnerStreamEvent): boolean {
    const eventRunnerId = event.runner_id || event.actor_sessions_report?.runner_id || "";
    return !(eventRunnerId && activeRunnerIdRef.current && eventRunnerId !== activeRunnerIdRef.current);
  }

  function handleConversationEvent(event: RunnerStreamEvent): void {
    const previewIntent = derivePreviewIntent(event);
    if (previewIntent?.kind === "file") {
      void openFile(previewIntent.path);
    } else if (previewIntent) {
      setActivePreviewFeature("browser");
      setPreviewCollapsed(false);
      void refreshLiveBrowserMetadata();
    }
  }

  function cancelRootStream(): void {
    setError("");
    void conversation.cancelCurrentStream().catch((exc) => {
      setError(exc instanceof Error ? exc.message : String(exc));
    });
  }

  async function newRunner(options: { clearMessages?: boolean } = { clearMessages: true }): Promise<SessionStatus> {
    setError("");
    setActiveView("thread");
    if (options.clearMessages !== false) {
      conversation.clearMessages();
    }
    setActiveActorName(null);
    setActorSessionsReport(null);
    const result = await client.command<SessionStatus>("start_session", {
      base_dir: bootstrap?.base_dir || baseDir,
      mode: "default",
      agent_mode: "agent",
      agent_type: bootstrap?.workspace_config.runtime?.agent_type || "react",
      model_name: selectedModelName || models[0]?.model_name || "doubao_lite",
      model_effort: selectedModelEffort,
    });
    activeRunnerIdRef.current = result.runner_id;
    setStatus(result);
    setSelectedModelName(result.model_name);
    setSelectedModelEffort(normalizeModelEffort(result.model_effort, models.find((model) => model.model_name === result.model_name)));
    await syncBrowserForRunner(result.runner_id);
    await refreshActorSessions({ updateRootMessages: true });
    await refreshSessions();
    return result;
  }

  async function resumeRunner(
    runnerId: string,
    options: { baseDirOverride?: string; fallbackModelName?: string; fallbackModelEffort?: string } = {}
  ): Promise<void> {
    setError("");
    setActiveView("thread");
    conversation.clearMessages();
    setActiveActorName(null);
    setActorSessionsReport(null);
    const result = await client.command<SessionStatus>("resume_session", {
      runner_id: runnerId,
      base_dir: options.baseDirOverride || bootstrap?.base_dir || baseDir,
      model_name: options.fallbackModelName || selectedModelName || models[0]?.model_name || "doubao_lite",
      model_effort: normalizeModelEffort(
        options.fallbackModelEffort || selectedModelEffort,
        models.find((model) => model.model_name === (options.fallbackModelName || selectedModelName)),
      ),
    });
    activeRunnerIdRef.current = result.runner_id;
    setStatus(result);
    setSelectedModelName(result.model_name);
    setSelectedModelEffort(normalizeModelEffort(result.model_effort, models.find((model) => model.model_name === result.model_name)));
    await syncBrowserForRunner(result.runner_id);
    await refreshActorSessions({ updateRootMessages: true });
  }

  async function switchRuntime(modelName: string, modelEffort: ModelEffort): Promise<void> {
    const nextModelName = modelName || selectedModelName || models[0]?.model_name || "doubao_lite";
    const nextModelEffort = normalizeModelEffort(
      modelEffort,
      models.find((model) => model.model_name === nextModelName),
    );
    if (nextModelName === selectedModelName && nextModelEffort === selectedModelEffort) return;

    const previousModelName = selectedModelName;
    const previousModelEffort = selectedModelEffort;
    setSelectedModelName(nextModelName);
    setSelectedModelEffort(nextModelEffort);
    setRuntimeSwitching(true);
    setError("");
    try {
      const result = await client.command<SessionStatus>("switch_model", {
        base_dir: bootstrap?.base_dir || baseDir,
        model_name: nextModelName,
        model_effort: nextModelEffort,
      });
      activeRunnerIdRef.current = result.runner_id;
      setStatus(result);
      setSelectedModelName(result.model_name);
      setSelectedModelEffort(normalizeModelEffort(result.model_effort, models.find((model) => model.model_name === result.model_name)));
      console.info("Switched web runtime model", {
        model_name: result.model_name,
        model_effort: result.model_effort,
      });
    } catch (exc) {
      setSelectedModelName(previousModelName);
      setSelectedModelEffort(previousModelEffort);
      const message = exc instanceof Error ? exc.message : String(exc);
      setError(message);
      console.warn("Failed to switch web runtime model", exc);
    } finally {
      setRuntimeSwitching(false);
    }
  }

  async function refreshSessions(): Promise<void> {
    const result = await client.command<SessionSummary[]>("list_sessions", {
      base_dir: bootstrap?.base_dir || baseDir,
    });
    setSessions(result);
  }

  async function refreshArchivedSessions(): Promise<void> {
    setArchivedLoading(true);
    setArchivedError("");
    try {
      setArchivedSessions(await client.listArchivedSessions(bootstrap?.base_dir || baseDir));
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setArchivedError(message);
      console.warn("Failed to load archived runners", exc);
    } finally {
      setArchivedLoading(false);
    }
  }

  async function refreshActorSessions(options: { updateRootMessages?: boolean } = {}): Promise<ActorSessionsReport | null> {
    try {
      const report = await client.describeActorSessions();
      setActorSessionsReport(report);
      if (options.updateRootMessages) {
        conversation.replaceMessages(loadRootMessages(report));
      }
      return report;
    } catch (exc) {
      // A transport failure must not erase the transcript or the last usable
      // actor report.  The user can still inspect and copy already shown work.
      setError(exc instanceof Error ? exc.message : String(exc));
      return null;
    }
  }

  async function sendMessage(
    text: string,
    agentModeOverride?: string,
  ): Promise<"consumed" | "retry"> {
    const trimmed = text.trim();
    if (!trimmed) return "consumed";
    setActiveActorName(null);
    setError("");
    try {
      if (!activeRunnerIdRef.current) {
        await newRunner({ clearMessages: false });
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      return "retry";
    }
    const outcome = await conversation.sendMessage(trimmed, agentModeOverride);
    if (outcome === "failed") return "retry";
    if (outcome === "completed") {
      try {
        const browser = await client.browserLivePreview(bootstrap?.base_dir || baseDir, activeRunnerIdRef.current);
        updateBrowserTabsFromPreview(browser);
        await refreshActorSessions();
        await refreshSessions();
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    }
    return "consumed";
  }

  async function sendComposerText(
    text: string,
    fromQueue = false,
  ): Promise<"consumed" | "retry"> {
    const trimmed = text.trim();
    if (!trimmed) return "consumed";
    if (activeActorName) {
      setError("");
      try {
        const result = await client.sendActorMessage({ actor_name: activeActorName, message: trimmed });
        if (!result.accepted) {
          setError(`Actor message rejected: ${result.error || result.status || "not_live"}`);
        }
        await refreshActorSessions();
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
      return "consumed";
    }
    // A previous transport failure is display state, not a conversation lock.
    // The next real stream_started action clears it.  Treating streamFailed as
    // busy here while also blocking the queue consumer on streamFailed creates
    // a closed loop in which no later message can ever start.
    if (!fromQueue && (streaming || queuedMessages.length > 0)) {
      setQueuedMessages((prev) => [...prev, trimmed]);
      return "consumed";
    }
    if (trimmed.startsWith("/")) {
      await runSlashCommand(trimmed);
      return "consumed";
    }
    return sendMessage(trimmed);
  }

  async function runSlashCommand(commandText: string): Promise<void> {
    if (streaming) return;
    setError("");
    setActiveActorName(null);
    try {
      const result = await executeWebCommand(commandText, client, {
        baseDir: bootstrap?.base_dir || baseDir,
        status,
        sessions,
        models,
      });
      if (result.clearMessages) {
        conversation.clearMessages();
      } else if (result.blocks.length > 0) {
        conversation.addMessages([
          { id: `user-command-${Date.now()}`, kind: "user", text: commandText },
          ...result.blocks.map((block, index) => ({ ...block, id: `command-${Date.now()}-${index}` })),
        ]);
      }
      if (result.status) {
        activeRunnerIdRef.current = result.status.runner_id || activeRunnerIdRef.current;
        setStatus(result.status);
        setSelectedModelName(result.status.model_name || selectedModelName);
        setSelectedModelEffort(normalizeModelEffort(result.status.model_effort, models.find((model) => model.model_name === result.status?.model_name)));
        void client.listAvailableAgents({ mode_id: result.status.agent_mode }).then((agentsResult) => {
          setAvailableAgents(agentsResult.agents || []);
        }).catch((exc) => console.warn("Failed to refresh available agents", exc));
      }
      if (result.workspaceAgentType) {
        setBootstrap((current) => current ? {
          ...current,
          workspace_config: {
            ...current.workspace_config,
            runtime: {
              ...current.workspace_config.runtime,
              agent_type: result.workspaceAgentType,
            },
          },
        } : current);
      }
      if (result.runtimeChange) {
        await switchRuntime(result.runtimeChange.modelName, result.runtimeChange.modelEffort);
      }
      if (result.resumeRunnerId) {
        await resumeRunner(result.resumeRunnerId);
      }
      if (result.forwardMessage) {
        await sendMessage(result.forwardMessage, result.forwardAgentMode);
      }
      if (result.refreshSessions) {
        await refreshSessions();
      }
      if (result.refreshSkills) {
        await refreshSkills();
      }
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setError(message);
      conversation.addMessage({ id: `command-error-${Date.now()}`, kind: "error", text: message });
    }
  }

  async function answerAsk(selected: AskOption[], customResponse = ""): Promise<void> {
    if (!pendingAsk) return;
    const response: AskResponse = {
      status: "answered",
      request_id: pendingAsk.request_id,
      question: pendingAsk.question,
      selected,
      custom_response: customResponse,
      error: "",
    };
    await client.command("answer_ask", {
      request_id: pendingAsk.request_id,
      response,
    });
    setPendingAsk(null);
  }

  async function openFile(path: string): Promise<void> {
    setActivePreviewFeature("files");
    setPreviewCollapsed(false);
    setFilePreviewError("");
    try {
      const file = await client.readFile(bootstrap?.base_dir || baseDir, path);
      setSelectedFile({
        path: file.path,
        content: file.content,
        language: file.language,
        fileKind: file.kind || "text",
        imageUrl: client.fileAssetUrl(file.image_url || ""),
        mediaType: file.media_type,
      });
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setFilePreviewError(message);
      console.warn("Failed to open file preview", exc);
    }
  }

  async function addBrowserTab(): Promise<void> {
    try {
      const runnerId = activeRunnerIdRef.current || (await newRunner({ clearMessages: false })).runner_id;
      const preview = await client.browserLiveNewTab(bootstrap?.base_dir || baseDir, runnerId, "", true);
      updateBrowserTabsFromPreview(preview);
      setActivePreviewFeature("browser");
      setPreviewCollapsed(false);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  function updateBrowserTabsFromPreview(preview: BrowserPreview): void {
    setBrowserPreview(preview);
    setBrowserTabs((current) => {
      const synced = syncBrowserTabsFromPreview(current, activeBrowserTabId, preview);
      setActiveBrowserTabId(synced.activeTabId);
      return synced.tabs;
    });
  }

  async function syncBrowserForRunner(runnerId: string): Promise<void> {
    try {
      const preview = await client.browserLivePreview(bootstrap?.base_dir || baseDir, runnerId);
      setBrowserPreview(preview);
      setBrowserTabs((current) => {
        const synced = syncBrowserTabsForRunner(
          current,
          activeBrowserTabId,
          preview
        );
        setActiveBrowserTabId(synced.activeTabId);
        return synced.tabs;
      });
    } catch (exc) {
      console.warn("Failed to sync browser preview for runner", exc);
    }
  }

  async function refreshLiveBrowserMetadata(): Promise<void> {
    try {
      updateBrowserTabsFromPreview(await client.browserLivePreview(bootstrap?.base_dir || baseDir, activeRunnerIdRef.current));
    } catch (exc) {
      console.warn("Failed to refresh live browser preview", exc);
    }
  }

  function updateBrowserUrl(tabId: string, url: string): void {
    setBrowserTabs((current) =>
      current.map((tab) =>
        tab.id === tabId ? navigateBrowserTab(tab, url) : tab
      )
    );
  }

  function moveBrowser(tabId: string, direction: "back" | "forward"): void {
    setBrowserTabs((current) =>
      current.map((tab) =>
        tab.id === tabId ? moveBrowserHistory(tab, direction) : tab
      )
    );
  }

  function refreshBrowser(tabId: string): void {
    setBrowserTabs((current) =>
      current.map((tab) => (tab.id === tabId ? refreshBrowserTab(tab) : tab))
    );
  }

  async function openExternalBrowser(url = ""): Promise<void> {
    try {
      updateBrowserTabsFromPreview(
        await client.browserLiveOpenExternal(bootstrap?.base_dir || baseDir, activeRunnerIdRef.current, url)
      );
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setError(message);
      console.warn("Failed to open external browser", exc);
    }
  }

  function startPreviewResize(event: React.PointerEvent<HTMLDivElement>): void {
    event.preventDefault();
    const startX = event.clientX;
    // CSS may clamp the persisted preference to keep the center workspace on
    // screen.  Start dragging from the panel's rendered width so the first
    // pointer movement does not jump back toward an off-screen stored value.
    const renderedPreviewWidth = event.currentTarget.nextElementSibling?.getBoundingClientRect().width;
    const startWidth = renderedPreviewWidth && renderedPreviewWidth > 0 ? renderedPreviewWidth : previewWidth;
    document.body.classList.add("is-resizing-preview");

    function move(pointerEvent: PointerEvent): void {
      setPreviewWidth(clampPreviewWidth(startWidth - (pointerEvent.clientX - startX)));
    }

    function stop(): void {
      document.body.classList.remove("is-resizing-preview");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    }

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
  }

  async function refreshSkills(): Promise<void> {
    setSkillsLoading(true);
    setSkillsError("");
    try {
      const [result, config] = await Promise.all([
        client.listSkills(),
        client.skillsConfigStatus(),
      ]);
      setSkills(result.skills || []);
      setSkillsConfig(config);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setSkillsError(message);
      console.warn("Failed to load installed skills", exc);
    } finally {
      setSkillsLoading(false);
    }
  }

  async function saveSkillsConfig(next: { enabled: boolean; disabled: string[] }): Promise<void> {
    setSkillsLoading(true);
    setSkillsError("");
    try {
      const config = await client.setSkillsConfig(next);
      const result = await client.listSkills();
      setSkillsConfig(config);
      setSkills(result.skills || []);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setSkillsError(message);
      console.warn("Failed to save skills config", exc);
    } finally {
      setSkillsLoading(false);
    }
  }

  async function archiveRunner(runnerId: string): Promise<void> {
    if (!runnerId) return;
    const confirmed = window.confirm("Archive this runner? It will disappear from the sidebar and can be restored from Settings > Archived chats.");
    if (!confirmed) return;
    setError("");
    try {
      const archivingActive = runnerId === activeRunnerIdRef.current;
      if (archivingActive) {
        if (conversation.streaming) {
          await conversation.cancelCurrentStream().catch(() => {});
        }
        await client.stopSession().catch((exc) => {
          console.warn("Failed to stop active session before archive", exc);
        });
      }
      await client.archiveRunner(bootstrap?.base_dir || baseDir, runnerId);
      if (archivingActive) {
        activeRunnerIdRef.current = "";
        setStatus(null);
        conversation.clearMessages();
        setActorSessionsReport(null);
        setActiveActorName(null);
        setPendingAsk(null);
      }
      await refreshSessions();
      if (activeView === "settings") {
        await refreshArchivedSessions();
      }
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setError(message);
      setArchivedError(message);
      console.warn("Failed to archive runner", exc);
    }
  }

  async function restoreRunner(runnerId: string): Promise<void> {
    if (!runnerId) return;
    setArchivedError("");
    try {
      await client.restoreRunner(bootstrap?.base_dir || baseDir, runnerId);
      await Promise.all([refreshSessions(), refreshArchivedSessions()]);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setArchivedError(message);
      console.warn("Failed to restore runner", exc);
    }
  }

  function openSettings(): void {
    setActiveView("settings");
    void refreshArchivedSessions();
  }

  function backToApp(): void {
    setActiveView("thread");
  }

  function selectView(view: MainView): void {
    setActiveView(view);
    if (view === "skills") {
      void refreshSkills();
    }
  }

  const mainContent =
    activeView === "settings" ? (
      <ArchivedRunnersPage
        sessions={archivedSessions}
        loading={archivedLoading}
        error={archivedError}
        onRefresh={() => void refreshArchivedSessions()}
        onRestoreRunner={(runnerId) => void restoreRunner(runnerId)}
      />
    ) : activeView === "skills" ? (
      <SkillsPage
        skills={skills}
        config={skillsConfig}
        loading={skillsLoading}
        error={skillsError}
        onRefresh={() => void refreshSkills()}
        onSaveConfig={(next) => void saveSkillsConfig(next)}
      />
    ) : activeView === "search" ? (
      <SearchPage
        sessions={sessions}
        actorSessionsReport={actorSessionsReport}
        messages={visibleMessages}
        onResumeRunner={(runnerId) => void resumeRunner(runnerId)}
        onSelectSubactor={(actorName) => {
          setActiveView("thread");
          setActiveActorName(actorName);
        }}
        onShowThread={() => setActiveView("thread")}
      />
    ) : (
      <ThreadView
        status={status}
        title={threadTitle}
        subtitle={threadSubtitle}
        activeActorName={activeActorName}
        models={models}
        skills={skills}
        plugins={plugins}
        availableAgentNames={availableAgents.map((agent) => agent.name)}
        selectedModelName={selectedModelName}
        selectedModelEffort={selectedModelEffort}
        messages={visibleMessages}
        streaming={streaming}
        actorBusy={activeActorBusy}
        queuedMessages={queuedMessages}
        runtimeSwitching={runtimeSwitching}
        error={error}
        pendingAsk={pendingAsk}
        onSend={(text) => void sendComposerText(text)}
        onCancel={() => {
          if (activeActorName) {
            void client.interruptActor({ actor_name: activeActorName }).then(() => refreshActorSessions()).catch((exc) => {
              setError(exc instanceof Error ? exc.message : String(exc));
            });
            return;
          }
          void cancelRootStream();
        }}
        onRuntimeChange={(modelName, modelEffort) => void switchRuntime(modelName, modelEffort)}
        onAnswerAsk={(selected, custom) => void answerAsk(selected, custom)}
        onOpenFile={(path) => void openFile(path)}
      />
    );

  return (
    <div
      className={`app-shell ${previewCollapsed ? "preview-collapsed" : ""}`}
      style={{ "--preview-width": `${previewWidth}px` } as React.CSSProperties}
    >
      <Sidebar
        baseDir={bootstrap?.base_dir || baseDir}
        sessions={sessions}
        activeRunnerId={status?.runner_id || ""}
        activeActorName={activeActorName}
        actorSessionsReport={actorSessionsReport}
        activeView={activeView}
        onNewRunner={() => void newRunner({ clearMessages: true })}
        onSelectView={selectView}
        onResumeRunner={(runnerId) => void resumeRunner(runnerId)}
        onSelectSubactor={(actorName) => setActiveActorName(actorName)}
        onArchiveRunner={(runnerId) => void archiveRunner(runnerId)}
        onOpenSettings={openSettings}
        onBackToApp={backToApp}
      />
      {mainContent}
      {activeView === "settings" ? null : (
        <>
          {previewCollapsed ? null : (
            <div
              className="preview-resize-handle"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize preview panel"
              onPointerDown={startPreviewResize}
            />
          )}
          <PreviewPanel
            browserPreview={browserPreview}
            browserLiveSocketUrl={browserLiveSocketUrl}
            fileTree={fileTree}
            activeFeature={activePreviewFeature}
            browserTabs={browserTabs}
            activeBrowserTabId={activeBrowserTabId}
            selectedFile={selectedFile}
            fileError={filePreviewError}
            collapsed={previewCollapsed}
            onToggleCollapsed={() => setPreviewCollapsed((current) => !current)}
            onSelectFeature={setActivePreviewFeature}
            onAddBrowserTab={() => void addBrowserTab()}
            onBrowserStatus={updateBrowserTabsFromPreview}
            onOpenFile={(path) => void openFile(path)}
            onUpdateBrowserUrl={(url) => activeBrowserTabId && updateBrowserUrl(activeBrowserTabId, url)}
            onBrowserBack={() => activeBrowserTabId && moveBrowser(activeBrowserTabId, "back")}
            onBrowserForward={() => activeBrowserTabId && moveBrowser(activeBrowserTabId, "forward")}
            onRefreshBrowser={() => activeBrowserTabId && refreshBrowser(activeBrowserTabId)}
            onOpenExternalBrowser={openExternalBrowser}
          />
        </>
      )}
    </div>
  );
}

function loadRootMessages(report: ActorSessionsReport | null): MessageBlock[] {
  const rootActor = findRootActor(report);
  if (!rootActor) return [];
  return presentActorSession(rootActor).slice(1);
}
