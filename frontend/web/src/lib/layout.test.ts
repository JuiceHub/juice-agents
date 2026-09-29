import test from "node:test";
import assert from "node:assert/strict";
import {
  LEFT_NAV_ACTIONS,
  PREVIEW_FEATURES,
  buildSubactorNavItems,
  createBrowserTab,
  derivePreviewIntent,
  isPreviewFeature,
  moveBrowserHistory,
  navigateBrowserTab,
  projectRunnerLabel,
  refreshBrowserTab,
  syncBrowserTabsForRunner,
  syncBrowserTabsFromPreview,
} from "./layout.js";
import type { ActionStep, ActorSessionsReport, RunnerStreamEvent } from "@juice-agents/shared/gateway/types";

test("layout keeps controls on the left and separates right-panel features", () => {
  assert.deepEqual([...LEFT_NAV_ACTIONS], ["New chat", "Search", "Skills"]);
  assert.deepEqual([...PREVIEW_FEATURES], ["browser", "files"]);
  assert.equal(isPreviewFeature("browser"), true);
  assert.equal(isPreviewFeature("files"), true);
  assert.equal(isPreviewFeature("settings"), false);
});

test("browser tabs normalize urls, keep history, and refresh browser reload keys", () => {
  const start = createBrowserTab({ id: "browser-1", browserTabId: "tab_1" });
  assert.equal(start.browserTabId, "tab_1");

  const first = navigateBrowserTab(start, "baidu.com");
  assert.equal(first.url, "https://baidu.com");
  assert.equal(first.title, "baidu.com");
  assert.deepEqual(first.history, ["https://baidu.com"]);
  assert.equal(first.historyIndex, 0);

  const second = navigateBrowserTab(first, "https://example.com/docs");
  assert.deepEqual(second.history, ["https://baidu.com", "https://example.com/docs"]);
  assert.equal(second.historyIndex, 1);

  const back = moveBrowserHistory(second, "back");
  assert.equal(back.url, "https://baidu.com");
  assert.equal(back.historyIndex, 0);

  const refreshed = refreshBrowserTab(back);
  assert.equal(refreshed.reloadKey, back.reloadKey + 1);
});

test("browser preview tabs mirror backend Playwright tabs without duplicates", () => {
  const first = syncBrowserTabsFromPreview([], "", {
    connected: true,
    title: "Example",
    url: "https://example.com",
    image_url: "",
    active_tab_id: "tab_1",
    tabs: [{ tab_id: "tab_1", url: "https://example.com", title: "Example", active: true }],
  });
  const second = syncBrowserTabsFromPreview(first.tabs, first.activeTabId, {
    connected: true,
    title: "Docs",
    url: "https://docs.example.com",
    image_url: "",
    active_tab_id: "tab_2",
    tabs: [
      { tab_id: "tab_1", url: "https://example.com", title: "Example", active: false },
      { tab_id: "tab_2", url: "https://docs.example.com", title: "Docs", active: true },
    ],
  });

  assert.equal(first.tabs.length, 1);
  assert.equal(second.tabs.length, 2);
  assert.deepEqual(second.tabs.map((tab) => tab.browserTabId), ["tab_1", "tab_2"]);
  assert.equal(new Set(second.tabs.map((tab) => tab.id)).size, 2);
  assert.equal(second.activeTabId, "browser-tab_2");
});

test("runner browser sync replaces browser tabs without carrying local-only tabs", () => {
  const oldBrowser = createBrowserTab({ id: "browser-tab_1", browserTabId: "tab_1", url: "https://old.example" });
  const switched = syncBrowserTabsForRunner([oldBrowser], "browser-tab_1", {
    connected: true,
    title: "New Runner",
    url: "https://new.example",
    image_url: "",
    active_tab_id: "tab_1",
    tabs: [{ tab_id: "tab_1", url: "https://new.example", title: "New Runner", active: true }],
  });

  const browserTab = switched.tabs[0];
  assert.equal(switched.tabs.length, 1);
  assert.ok(browserTab);
  if (!browserTab) return;
  assert.equal(browserTab.url, "https://new.example");
  assert.deepEqual(browserTab.history, ["https://new.example"]);
  assert.equal(switched.activeTabId, "browser-tab_1");
});

test("runner browser sync removes browser tabs when the runner has no live session", () => {
  const oldBrowser = createBrowserTab({ id: "browser-tab_1", browserTabId: "tab_1", url: "https://old.example" });
  const switched = syncBrowserTabsForRunner([oldBrowser], "browser-tab_1", {
    connected: false,
    title: "",
    url: "",
    image_url: "",
    active_tab_id: "",
    tabs: [],
  });

  assert.equal(switched.tabs.length, 0);
  assert.equal(switched.activeTabId, "");
});

test("runner label treats runners as agent task threads", () => {
  assert.equal(
    projectRunnerLabel({
      runnerId: "abc12345",
      firstUserRequestPreview: "Implement runner titles",
      goalPreview: "Fix tests",
      updatedAt: "2026-05-16",
    }),
    "Implement runner titles · 2026-05-16"
  );
  assert.equal(projectRunnerLabel({ runnerId: "abc12345", goalPreview: "Fix tests" }), "Fix tests");
  assert.equal(projectRunnerLabel({ runnerId: "abc12345" }), "abc12345");
});

test("subactor nav items are built under the active root runner", () => {
  const report: ActorSessionsReport = {
    runner_id: "run-1",
    permission_mode: "default",
    agent_mode: "team",
    root_actor_name: "teamlead",
    actors: [
      makeActor({ actor_name: "teamlead", is_root: true }),
      makeActor({ actor_name: "researcher", actor_kind: "teammate", status: "active" }),
    ],
  };

  const items = buildSubactorNavItems(report, "researcher");
  assert.equal(items.length, 1);
  assert.equal(items[0].label, "researcher");
  assert.equal(items[0].active, true);
  assert.match(items[0].description, /teammate/);
});

test("stream events derive browser intents only from real browser tool calls", () => {
  const browserIntent = derivePreviewIntent(
    makeEvent({
      action_step: makeStep({
        tool_calls: [
          {
            name: "browser_open_url",
            args: { url: "https://www.baidu.com" },
            mode: "sync",
            status: "success",
            observation_index: 0,
            is_terminal: false,
          },
        ],
      }),
    })
  );
  assert.deepEqual(browserIntent, { kind: "browser", url: "https://www.baidu.com", title: "www.baidu.com" });

  const mentionedUrl = derivePreviewIntent(
    makeEvent({
      action_step: makeStep({
        model_output: "Read more at https://example.com/docs",
      }),
    })
  );
  assert.equal(mentionedUrl, null);
});

test("stream events keep deriving workspace file preview intents", () => {
  const fileIntent = derivePreviewIntent(
    makeEvent({
      action_step: makeStep({
        observations: ["read frontend/web/src/App.tsx successfully"],
      }),
    })
  );
  assert.deepEqual(fileIntent, { kind: "file", path: "frontend/web/src/App.tsx" });

  const imageIntent = derivePreviewIntent(
    makeEvent({
      action_step: makeStep({
        observation_images: [{ image_url: ".juice/runners/run/observation_images/out.png", description: "demo" }],
      }),
    })
  );
  assert.equal(imageIntent, null);
});

function makeActor(overrides: Record<string, unknown> = {}) {
  return {
    actor_id: String(overrides.actor_name || "actor"),
    actor_name: "actor",
    actor_kind: "agent",
    actor_role: "worker",
    status: "idle",
    is_root: false,
    current_async_task_id: "",
    metadata: {},
    session: {},
    steps: [],
    ...overrides,
  };
}

function makeEvent(overrides: Partial<RunnerStreamEvent>): RunnerStreamEvent {
  return {
    kind: "action_step",
    permission_mode: "default",
    agent_mode: "agent",
    runner_id: "run-1",
    actor_name: "root",
    actor_role: "root",
    step_num: 1,
    action_step: makeStep({}),
    team_event: null,
    round_id: "round-1",
    ...overrides,
  };
}

function makeStep(overrides: Partial<ActionStep>): ActionStep {
  return {
    step_num: 1,
    model_output: "",
    thought: "",
    tool_calls: [],
    code_action: "",
    reasoning_content: "",
    observations: [],
    observation_images: [],
    attachments: [],
    error: null,
    round_outcome: "continue",
    output: null,
    ...overrides,
  };
}
