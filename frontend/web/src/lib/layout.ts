import type {
  ActiveAgentsReport,
  ActiveAgentSnapshot,
  BrowserPreview,
  BrowserTabInfo,
  RunnerStreamEvent,
  SessionSummary,
} from "@juice-agents/shared/gateway/types";

export const LEFT_NAV_ACTIONS = ["New chat", "Search", "Skills"] as const;
export const PREVIEW_FEATURES = ["browser", "files"] as const;

export type PreviewFeature = (typeof PREVIEW_FEATURES)[number];

export interface BrowserTabState {
  id: string;
  title: string;
  url: string;
  history: string[];
  historyIndex: number;
  reloadKey: number;
  browserTabId: string;
  imageUrl?: string;
}

export type PreviewIntent =
  | { kind: "browser"; title?: string; url?: string; imageUrl?: string }
  | { kind: "file"; path: string; title?: string };

export interface SubactorNavItem {
  actorId: string;
  actorName: string;
  label: string;
  description: string;
  active: boolean;
}

export function projectRunnerLabel(params: {
  runnerId: string;
  firstUserRequestPreview?: string;
  goalPreview?: string;
  updatedAt?: string;
}): string {
  const title = (
    params.firstUserRequestPreview ||
    params.goalPreview ||
    params.runnerId ||
    "Untitled runner"
  ).trim();
  const suffix = params.updatedAt ? ` · ${params.updatedAt}` : "";
  return `${title}${suffix}`;
}

export function isPreviewFeature(value: string): value is PreviewFeature {
  return PREVIEW_FEATURES.includes(value as PreviewFeature);
}

export function findRootActor(report: ActiveAgentsReport | null): ActiveAgentSnapshot | null {
  if (!report) return null;
  return (
    report.agents.find((actor) => actor.is_root) ||
    report.agents.find((actor) => actor.agent_name === report.root_agent_name) ||
    null
  );
}

export function findActorByName(
  report: ActiveAgentsReport | null,
  actorName: string | null
): ActiveAgentSnapshot | null {
  if (!report || !actorName) return null;
  return (
    report.agents.find((actor) => {
      const name = String(actor.agent_name || "").trim();
      const id = String(actor.agent_id || "").trim();
      return name === actorName || id === actorName;
    }) || null
  );
}

export function buildSubactorNavItems(
  report: ActiveAgentsReport | null,
  activeActorName: string | null
): SubactorNavItem[] {
  const root = findRootActor(report);
  const rootName = root?.agent_name || report?.root_agent_name || "";
  return (report?.agents || [])
    .filter((actor) => !actor.is_root && actor.agent_name !== rootName)
    .map((actor) => {
      const actorName = String(actor.agent_name || actor.agent_id || "").trim();
      const actorId = String(actor.agent_id || actorName).trim();
      const description = [
        actor.agent_kind || actor.agent_role || "actor",
        actor.status || "unknown",
        actor.current_async_task_id || "",
      ].filter(Boolean).join(" · ");
      return {
        actorId,
        actorName,
        label: actorName || "Unnamed actor",
        description,
        active: activeActorName === actorName || activeActorName === actorId,
      };
    })
    .filter((item) => item.actorName);
}

export function createBrowserTab(params: Partial<BrowserTabState> = {}): BrowserTabState {
  const url = params.url || "";
  const history = params.history || (url ? [url] : []);
  const historyIndex = Math.min(Math.max(params.historyIndex ?? history.length - 1, 0), Math.max(history.length - 1, 0));
  return {
    id: params.id || `browser-${Date.now()}`,
    title: params.title || "Browser",
    url,
    history,
    historyIndex,
    reloadKey: params.reloadKey || 0,
    browserTabId: params.browserTabId || "",
    imageUrl: params.imageUrl,
  };
}

export function syncBrowserTabsFromPreview(
  tabs: BrowserTabState[],
  activeTabId: string,
  preview: BrowserPreview
): { tabs: BrowserTabState[]; activeTabId: string } {
  const browserTabs = preview.tabs || [];
  if (browserTabs.length === 0) {
    return { tabs: [], activeTabId: "" };
  }

  const existingByBrowserTabId = new Map(
    tabs.map((tab) => [tab.browserTabId, tab])
  );
  const nextBrowsers = browserTabs.map((browserTab) => browserPreviewTab(existingByBrowserTabId.get(browserTab.tab_id), browserTab));
  const nextActiveBrowser = nextBrowsers.find((tab) => tab.browserTabId === preview.active_tab_id) || nextBrowsers.find((tab) => {
    const source = browserTabs.find((item) => item.tab_id === tab.browserTabId);
    return Boolean(source?.active);
  });
  const nextTabs = nextBrowsers;
  const activeStillExists = nextTabs.some((tab) => tab.id === activeTabId);
  return {
    tabs: nextTabs,
    activeTabId: nextActiveBrowser?.id || (activeStillExists ? activeTabId : nextTabs[0]?.id || ""),
  };
}

export function syncBrowserTabsForRunner(
  _tabs: BrowserTabState[],
  _activeTabId: string,
  preview: BrowserPreview
): { tabs: BrowserTabState[]; activeTabId: string } {
  const browserTabs = preview.tabs || [];
  const nextBrowsers =
    browserTabs.length > 0
      ? browserTabs.map((browserTab) => browserPreviewTab(undefined, browserTab))
      : [];
  const nextActiveBrowser =
    nextBrowsers.find((tab) => tab.browserTabId === preview.active_tab_id) ||
    nextBrowsers.find((tab) => {
      const source = browserTabs.find((item) => item.tab_id === tab.browserTabId);
      return Boolean(source?.active);
    }) ||
    nextBrowsers[0];
  return {
    tabs: nextBrowsers,
    activeTabId: nextActiveBrowser?.id || "",
  };
}

function browserPreviewTab(
  existing: BrowserTabState | undefined,
  browserTab: BrowserTabInfo
): BrowserTabState {
  const url = browserTab.url || existing?.url || "";
  const tab = createBrowserTab({
    id: existing?.id || `browser-${browserTab.tab_id}`,
    title: browserTab.title || titleFromUrl(url) || existing?.title || "Browser",
    url,
    history: existing?.history,
    historyIndex: existing?.historyIndex,
    reloadKey: existing?.reloadKey,
    browserTabId: browserTab.tab_id,
    imageUrl: existing?.imageUrl,
  });
  return url && url !== existing?.url && /^https?:\/\//i.test(url)
    ? { ...navigateBrowserTab(tab, url), title: browserTab.title || titleFromUrl(url) || tab.title, browserTabId: browserTab.tab_id }
    : tab;
}

export function derivePreviewIntent(event: RunnerStreamEvent): PreviewIntent | null {
  const step = event.action_step;
  if (!step) return null;

  const joined = [
    step.model_output,
    step.thought,
    step.code_action,
    ...(step.tool_calls || []).flatMap((call) => [
      call.name,
      ...Object.values(call.args || {}).map((value) => String(value || "")),
    ]),
    ...(step.observations || []),
  ].join("\n");
  const browserCalls = (step.tool_calls || []).filter((call) => String(call.name || "").startsWith("browser_"));
  if (browserCalls.length > 0) {
    const browserText = browserCalls.flatMap((call) => [
      call.name,
      ...Object.values(call.args || {}).map((value) => String(value || "")),
    ]).join("\n");
    const url = findFirstUrl(browserText);
    return url ? { kind: "browser", url, title: new URL(url).hostname } : { kind: "browser" };
  }

  const filePath = findWorkspaceFilePath(joined);
  return filePath ? { kind: "file", path: filePath } : null;
}

function findFirstUrl(text: string): string {
  const match = text.match(/https?:\/\/[^\s"'<>]+/);
  return match?.[0]?.replace(/[),.;]+$/, "") || "";
}

function findWorkspaceFilePath(text: string): string {
  const matches = text.matchAll(/(?:^|[\s"'`(])((?:\.\/)?[\w@./-]+\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml|css|html|txt))(?:$|[\s"'`),])/gm);
  for (const match of matches) {
    const candidate = (match[1] || "").replace(/^\.\//, "");
    if (!candidate || candidate.startsWith("/") || candidate.includes("://") || candidate.includes("..")) {
      continue;
    }
    return candidate;
  }
  return "";
}

export function rootRunnerTitle(session: SessionSummary | null | undefined): string {
  if (!session) return "New runner";
  return projectRunnerLabel({
    runnerId: session.runner_id,
    firstUserRequestPreview: session.first_user_request_preview,
    goalPreview: session.goal_objective_preview,
  });
}

function titleFromUrl(url: string): string {
  try {
    return url ? new URL(url).hostname : "";
  } catch {
    return "";
  }
}

export function normalizeBrowserUrl(url: string): string {
  const trimmed = url.trim();
  if (!trimmed) return "";
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

export function navigateBrowserTab(
  tab: BrowserTabState,
  url: string
): BrowserTabState {
  const normalized = normalizeBrowserUrl(url);
  if (!normalized) return tab;
  let title = "Browser";
  try {
    title = new URL(normalized).hostname || title;
  } catch {
    title = normalized;
  }
  const previous = tab.history.slice(0, tab.historyIndex + 1);
  const history = previous[previous.length - 1] === normalized ? previous : [...previous, normalized];
  return {
    ...tab,
    title,
    url: normalized,
    history,
    historyIndex: history.length - 1,
    reloadKey: tab.reloadKey + 1,
  };
}

export function moveBrowserHistory(
  tab: BrowserTabState,
  direction: "back" | "forward"
): BrowserTabState {
  const delta = direction === "back" ? -1 : 1;
  const nextIndex = tab.historyIndex + delta;
  if (nextIndex < 0 || nextIndex >= tab.history.length) return tab;
  const url = tab.history[nextIndex] || "";
  let title = tab.title;
  try {
    title = new URL(url).hostname || title;
  } catch {
    title = url || title;
  }
  return {
    ...tab,
    title,
    url,
    historyIndex: nextIndex,
    reloadKey: tab.reloadKey + 1,
  };
}

export function refreshBrowserTab(
  tab: BrowserTabState
): BrowserTabState {
  return { ...tab, reloadKey: tab.reloadKey + 1 };
}
