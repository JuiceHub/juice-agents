import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  buildActorSelectorItems,
  buildActorViewHint,
  buildCommandStatus,
  getNextAgentMode,
  resolveInputLayer,
  resolveActorTranscriptMessages,
  shouldJumpTranscriptForCommandResult,
  shouldPollCron,
  shouldRenderComposerDock,
  shouldRenderIntroPanel,
} from "./app.js";

test("renders the intro panel only while the static transcript is empty", () => {
  // append-only 模式: firstVisibleIndex=0 表示 staticItems 还为空，IntroPanel 应渲染。
  assert.equal(
    shouldRenderIntroPanel({ hasSession: true, firstVisibleIndex: 0 }),
    true
  );
  assert.equal(
    shouldRenderIntroPanel({ hasSession: true, firstVisibleIndex: 1 }),
    false
  );
  assert.equal(
    shouldRenderIntroPanel({ hasSession: false, firstVisibleIndex: 0 }),
    false
  );
});

test("CLI input ownership gives selector priority over ask and composer", () => {
  assert.deepEqual(
    resolveInputLayer({ configOpen: false, selectorOpen: true, askPending: true }),
    {
      configActive: false,
      skillsConfigActive: false,
      selectorActive: true,
      tasksDialogActive: false,
      askActive: false,
      streamingCancelActive: false,
      composerActive: false,
    }
  );

  assert.deepEqual(
    resolveInputLayer({ configOpen: false, selectorOpen: false, askPending: true }),
    {
      configActive: false,
      skillsConfigActive: false,
      selectorActive: false,
      tasksDialogActive: false,
      askActive: true,
      streamingCancelActive: false,
      composerActive: false,
    }
  );

  assert.deepEqual(
    resolveInputLayer({ configOpen: false, selectorOpen: false, askPending: false }),
    {
      configActive: false,
      skillsConfigActive: false,
      selectorActive: false,
      tasksDialogActive: false,
      askActive: false,
      streamingCancelActive: false,
      composerActive: true,
    }
  );

  assert.deepEqual(
    resolveInputLayer({ configOpen: true, selectorOpen: true, askPending: true }),
    {
      configActive: true,
      skillsConfigActive: false,
      selectorActive: false,
      tasksDialogActive: false,
      askActive: false,
      streamingCancelActive: false,
      composerActive: false,
    }
  );
});

test("config loading owns input before the configuration panel is ready", () => {
  assert.deepEqual(
    resolveInputLayer({
      configLoading: true,
      selectorOpen: true,
      tasksDialogOpen: true,
      askPending: true,
      streaming: true,
    }),
    {
      configActive: true,
      skillsConfigActive: false,
      selectorActive: false,
      tasksDialogActive: false,
      askActive: false,
      streamingCancelActive: false,
      composerActive: false,
    }
  );
});

test("Ask input preempts the tasks dialog at the same render boundary", () => {
  assert.deepEqual(
    resolveInputLayer({
      selectorOpen: false,
      tasksDialogOpen: true,
      askPending: true,
      streaming: true,
    }),
    {
      configActive: false,
      skillsConfigActive: false,
      selectorActive: false,
      tasksDialogActive: false,
      askActive: true,
      streamingCancelActive: false,
      composerActive: false,
    },
  );
});

test("Ask closes tasks and task detail polling has a single-request latch", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");
  const askHandler = appSource.slice(
    appSource.indexOf("const handleAskRequest"),
    appSource.indexOf("const messagesHook"),
  );
  const taskDetailEffect = appSource.slice(
    appSource.indexOf("// 进入 detail 时"),
    appSource.indexOf("const handleAskSubmit"),
  );

  assert.match(askHandler, /tasksDialog\.close\(\)/);
  assert.match(taskDetailEffect, /let requestInFlight = false/);
  assert.match(taskDetailEffect, /if \(requestInFlight\)/);
  assert.match(taskDetailEffect, /requestInFlight = false/);
});

test("composer dock is rendered only when no blocking overlay is active", () => {
  assert.equal(
    shouldRenderComposerDock(resolveInputLayer({ selectorOpen: false, askPending: false })),
    true
  );
  assert.equal(
    shouldRenderComposerDock(resolveInputLayer({ selectorOpen: true, askPending: false })),
    false
  );
  assert.equal(
    shouldRenderComposerDock(resolveInputLayer({ selectorOpen: false, askPending: true })),
    false
  );
  assert.equal(
    shouldRenderComposerDock(resolveInputLayer({
      configOpen: true,
      skillsConfigOpen: true,
      selectorOpen: true,
      askPending: true,
    })),
    false
  );
});

test("overlay row calculation only reserves the highest priority panel", () => {
});

test("command result jump-to-end is skipped for pure interactive selectors", () => {
  assert.equal(
    shouldJumpTranscriptForCommandResult({
      blocks: [],
      clearMessages: false,
      exitRequested: false,
      interactiveAction: "model-selector",
    }),
    false
  );

  assert.equal(
    shouldJumpTranscriptForCommandResult({
      blocks: [{ kind: "system", text: "updated" }],
      clearMessages: false,
      exitRequested: false,
    }),
    true
  );

  assert.equal(
    shouldJumpTranscriptForCommandResult({
      blocks: [],
      clearMessages: false,
      exitRequested: false,
      forwardMessage: "run task",
    }),
    true
  );

  assert.equal(
    shouldJumpTranscriptForCommandResult({
      blocks: [],
      clearMessages: true,
      exitRequested: false,
      loadHistory: true,
    }),
    true
  );
});

test("builds visible pending status for worktree commands", () => {
  assert.deepEqual(buildCommandStatus("/worktree enter feature-x"), {
    summary: "Creating worktree...",
    items: [{ label: "name", value: "feature-x" }],
  });
  assert.deepEqual(buildCommandStatus("/worktree status"), {
    summary: "Checking worktree status...",
  });
  assert.deepEqual(buildCommandStatus("/help"), {
    summary: "Running command...",
  });
});

test("streaming activates the cancel layer above composer but below overlays", () => {
  // streaming + 无 overlay：composer 仍可编辑（composerActive=true），
  // 同时 streamingCancelActive=true 让独立监听器拦截 Esc/Ctrl+C 用于取消。
  assert.deepEqual(
    resolveInputLayer({
      configOpen: false,
      selectorOpen: false,
      askPending: false,
      streaming: true,
    }),
    {
      configActive: false,
      skillsConfigActive: false,
      selectorActive: false,
      tasksDialogActive: false,
      askActive: false,
      streamingCancelActive: true,
      composerActive: true,
    }
  );

  // streaming + ask pending：ask 仍优先，cancel layer 让位（避免误吃 ESC 退选项）。
  assert.deepEqual(
    resolveInputLayer({
      configOpen: false,
      selectorOpen: false,
      askPending: true,
      streaming: true,
    }),
    {
      configActive: false,
      skillsConfigActive: false,
      selectorActive: false,
      tasksDialogActive: false,
      askActive: true,
      streamingCancelActive: false,
      composerActive: false,
    }
  );

  // streaming + selector：selector 优先。
  assert.deepEqual(
    resolveInputLayer({
      configOpen: false,
      selectorOpen: true,
      askPending: false,
      streaming: true,
    }),
    {
      configActive: false,
      skillsConfigActive: false,
      selectorActive: true,
      tasksDialogActive: false,
      askActive: false,
      streamingCancelActive: false,
      composerActive: false,
    }
  );
});

test("cron idle polling is disabled while user-facing work is active", () => {
  assert.equal(shouldPollCron({}), true);
  assert.equal(shouldPollCron({ streaming: true }), false);
  assert.equal(shouldPollCron({ queuedCount: 1 }), false);
  assert.equal(shouldPollCron({ askPending: true }), false);
  assert.equal(shouldPollCron({ configOpen: true }), false);
  assert.equal(shouldPollCron({ selectorOpen: true }), false);
  assert.equal(shouldPollCron({ exitRequested: true }), false);
});


test("Ctrl+S actor session selector lists main and subagent sessions", () => {
  const items = buildActorSelectorItems(
    {
      runner_id: "team-001",
      permission_mode: "default",
      agent_mode: "team",
      root_actor_name: "teamlead",
      actors: [
        {
          actor_id: "root",
          actor_name: "teamlead",
          actor_kind: "teamlead",
          actor_role: "root",
          status: "idle",
          is_root: true,
          current_async_task_id: "",
          metadata: {},
          session: {},
          steps: [],
        },
        {
          actor_id: "team:researcher",
          actor_name: "researcher",
          actor_kind: "teammate",
          actor_role: "worker",
          status: "active",
          is_root: false,
          current_async_task_id: "teammate_researcher_0001",
          metadata: {},
          session: {},
          steps: [],
        },
      ],
    },
    "researcher"
  );

  assert.deepEqual(items.map((item) => item.value), ["", "researcher"]);
  assert.equal(items[0].label, "Main · teamlead");
  assert.equal(items[1].label, "researcher");
  assert.equal(items[1].active, true);
});

test("Ctrl+S actor session selector still opens before subagents exist", () => {
  const items = buildActorSelectorItems(
    {
      runner_id: "team-001",
      permission_mode: "default",
      agent_mode: "team",
      root_actor_name: "teamlead",
      actors: [
        {
          actor_id: "root",
          actor_name: "teamlead",
          actor_kind: "teamlead",
          actor_role: "root",
          status: "idle",
          is_root: true,
          current_async_task_id: "",
          metadata: {},
          session: {},
          steps: [],
        },
      ],
    },
    null
  );

  assert.deepEqual(
    items.map((item) => item.value),
    ["", "__empty_actor_sessions__"]
  );
  assert.equal(items[1].label, "No subagent sessions yet");
});

test("Ctrl+S actor session selector has a visible fallback before RPC refresh", () => {
  const items = buildActorSelectorItems(null, null);

  assert.deepEqual(
    items.map((item) => item.value),
    ["", "__empty_actor_sessions__"]
  );
  assert.equal(items[0].label, "Main · main");
});

test("Ctrl+S actor session selector does not render blank actor labels", () => {
  const items = buildActorSelectorItems(
    {
      runner_id: "team-001",
      permission_mode: "default",
      agent_mode: "team",
      root_actor_name: "teamlead",
      actors: [
        {
          actor_id: "root",
          actor_name: "teamlead",
          actor_kind: "teamlead",
          actor_role: "root",
          status: "idle",
          is_root: true,
          current_async_task_id: "",
          metadata: {},
          session: {},
          steps: [],
        },
        {
          actor_id: "team:researcher",
          actor_name: "",
          actor_kind: "teammate",
          actor_role: "worker",
          status: "active",
          is_root: false,
          current_async_task_id: "",
          metadata: {},
          session: {},
          steps: [],
        },
      ],
    },
    null
  );

  assert.equal(items.length, 2);
  assert.equal(items[0].value, "");
  assert.equal(items[1].label.length > 0, true);
  assert.notEqual(items[1].value, "");
});

test("subagent actor session transcript is interactive while main messages remain separate", () => {
  const mainMessages = [{ kind: "user" as const, text: "main prompt" }];
  const messages = resolveActorTranscriptMessages(
    {
      runner_id: "team-001",
      permission_mode: "default",
      agent_mode: "team",
      root_actor_name: "teamlead",
      actors: [
        {
          actor_id: "team:researcher",
          actor_name: "researcher",
          actor_kind: "teammate",
          actor_role: "worker",
          status: "idle",
          is_root: false,
          current_async_task_id: "",
          metadata: {},
          session: {},
          steps: [{ type: "task", task: "subagent prompt" }],
        },
      ],
    },
    "researcher",
    mainMessages
  );

  assert.equal(messages[0].text, "researcher: idle · teammate");
  assert.equal(messages[1].text, "subagent prompt");
  assert.equal(buildActorViewHint("researcher"), "Viewing: researcher · interactive · Esc return");
});

test("streamed actor session reports refresh the actor selector without blocking RPC", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");
  const messagesSource = readFileSync(
    fileURLToPath(new URL("../../shared/src/conversation/useConversationStream.ts", import.meta.url)),
    "utf-8"
  );
  const selectorSource = readFileSync(
    fileURLToPath(new URL("./hooks/useInteractiveSelector.ts", import.meta.url)),
    "utf-8"
  );

  assert.match(messagesSource, /onActorSessionsReport/);
  assert.match(messagesSource, /event\.actor_sessions_report/);
  assert.match(appSource, /useConversationStream/);
  assert.match(appSource, /onActorSessionsReport:\s*handleStreamActorSessionsReport/);
  assert.match(appSource, /selector\.updateItemsIfOpen/);
  assert.match(selectorSource, /updateItemsIfOpen/);
});

test("skill aliases are normalized before slash completion state is populated", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");

  assert.match(appSource, /collectSkillAliases/);
  assert.match(appSource, /setSkillNames\(collectSkillAliases\(skills\.skills \|\| \[\]\)\)/);
  assert.doesNotMatch(appSource, /setSkillNames\(\(skills\.skills \|\| \[\]\)\.flatMap/);
});

test("Ctrl+S actor session refresh cannot reopen a selector after Main is selected", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");
  const handlerMatch = appSource.match(
    /const handleToggleActors = useCallback\(async \(\) => \{[\s\S]*?\n  \}, \[selector/
  );

  assert.ok(handlerMatch, "handleToggleActors callback should be present");
  const handlerSource = handlerMatch[0];

  assert.equal(
    (handlerSource.match(/selector\.open\(/g) || []).length,
    1,
    "Ctrl+S should open the actor selector only once per toggle"
  );
  assert.doesNotMatch(handlerSource, /selector\.updateItemsIfOpen/);
  assert.doesNotMatch(handlerSource, /await /);
});

test("Ctrl+S actor selector uses cached actor report instead of starting a blocking RPC", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");
  const handlerMatch = appSource.match(
    /const handleToggleActors = useCallback\(async \(\) => \{[\s\S]*?\n  \}, \[selector/
  );

  assert.ok(handlerMatch, "handleToggleActors callback should be present");
  const handlerSource = handlerMatch[0];

  assert.doesNotMatch(handlerSource, /refreshActorSessions/);
  assert.doesNotMatch(handlerSource, /describeActorSessions/);
});

test("new CLI sessions inherit both persisted modes and show them in composer dock", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");

  // 两个维度都继承 workspace 配置，CLI 入参优先。
  assert.match(
    appSource,
    /permissionMode:\s*permissionMode\s*\|\|\s*runtimeConfig\.permission_mode\s*\|\|\s*"default"/,
  );
  assert.match(
    appSource,
    /agentMode:\s*agentMode\s*\|\|\s*runtimeConfig\.agent_mode\s*\|\|\s*"agent"/,
  );
  assert.match(
    appSource,
    /permissionMode=\{session\.status\?\.permission_mode\s*\|\|\s*pendingRuntime\.permission_mode\s*\|\|\s*"default"\}/,
  );
  assert.match(
    appSource,
    /agentMode=\{session\.status\?\.agent_mode\s*\|\|\s*pendingRuntime\.agent_mode\s*\|\|\s*"agent"\}/,
  );
});

test("permission mode persistence lives in the coordinator, not per entry point", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");

  // /permissions、selector 与 Shift+Tab 都汇聚到 processRequestedMode。持久化必须
  // 写在 coordinator 里：曾经只在命令层写，导致 selector 切的 accept 不落盘。
  // 持久化的行为断言在 permissionModePersistence.test.ts（真实调用）；这里只守
  // 「入口不各写一遍」这个无法在语言内表达的结构约束。
  const coordinator = appSource.slice(
    appSource.indexOf("const status = await client.switchPermissionMode"),
    appSource.indexOf("modeSwitchInFlightRef.current = false"),
  );
  assert.match(coordinator, /persistPermissionMode\(client, baseDir, targetMode\)/);

  // selector 回调只委托给 coordinator，不自己写配置（否则会写两遍）。
  const selectorBlock = appSource.slice(
    appSource.indexOf('result.interactiveAction === "permissions-selector"'),
    appSource.indexOf('result.interactiveAction === "agent-type-selector"'),
  );
  assert.match(selectorBlock, /requestModeSwitch\(item\.value\)/);
  assert.equal(/saveWorkspaceConfig/.test(selectorBlock), false);
});

test("ensureSession waits for init() so persisted modes are not lost to a race", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");

  // pendingRuntime 的 useState 初始值刻意不读 workspace 配置，只有异步 init()
  // 才会把持久化的 permission_mode / agent_mode 填进去。若 ensureSession 不等
  // init()，首条消息会用初始默认值建 runner，持久化就被静默丢弃。
  assert.match(appSource, /await runtimeReadyPromiseRef\.current;/);
  // 信号必须在 finally 里释放，否则加载失败会让 ensureSession 永久挂住。
  assert.match(appSource, /finally \{[\s\S]*?runtimeReadyResolveRef\.current\?\.\(\);/);
});

test("Shift+Tab cycles agent modes", () => {
  // 常用执行模式按实用频率循环：agent → team → plan → group → agent
  assert.equal(getNextAgentMode("agent"), "team");
  assert.equal(getNextAgentMode("team"), "plan");
  assert.equal(getNextAgentMode("plan"), "group");
  assert.equal(getNextAgentMode("group"), "agent");
  assert.equal(getNextAgentMode(""), "agent");
});

test("ComposerDock receives the Shift+Tab mode cycle handler", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");

  assert.match(appSource, /const handleCycleMode = useCallback/);
  assert.match(appSource, /onCycleMode=\{handleCycleMode\}/);
  assert.doesNotMatch(appSource, /runtime:\s*\{\s*mode:/);
});

test("mode switch success paths do not append transcript logs", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");

  assert.doesNotMatch(appSource, /Switched to mode:/);
  assert.match(appSource, /Permission mode switch failed:/);
});

test("mode coordinator keeps only the latest target and consumes RPC status directly", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");

  assert.match(appSource, /requestedModeRef\.current = targetMode/);
  assert.match(appSource, /if \(requestedModeRef\.current === targetMode\)/);
  assert.match(appSource, /session\.setStatus\(status\)/);
  assert.match(appSource, /pendingMode \|\| session\.status\?\.permission_mode \|\| pendingRuntime\.permission_mode/);
  assert.doesNotMatch(
    appSource.slice(
      appSource.indexOf("const processRequestedMode"),
      appSource.indexOf("const requestModeSwitch"),
    ),
    /refreshStatus|refreshActorSessions/,
  );
});

test("cold start initialization does not eagerly create a runner", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");
  const initMatch = appSource.match(/const init = async \(\) => \{[\s\S]*?\n    \};/);

  assert.ok(initMatch, "init callback should be present");
  const initSource = initMatch[0];

  assert.doesNotMatch(initSource, /session\.startSession/);
  assert.match(initSource, /setPendingRuntime/);
  assert.match(initSource, /client\.listModels/);
});

test("first freeform submit creates a live session from pending runtime", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");

  assert.match(appSource, /const ensureSession = useCallback/);
  assert.match(appSource, /permission_mode:\s*pendingRuntime\.permission_mode/);
  assert.match(appSource, /agent_mode:\s*pendingRuntime\.agent_mode/);
  assert.match(appSource, /agent_type:\s*pendingRuntime\.agent_type/);
  assert.match(appSource, /const currentStatus = await ensureSession\(\)/);
});

test("agent type selector saves only the workspace default", () => {
  const appSource = readFileSync(fileURLToPath(new URL("./app.tsx", import.meta.url)), "utf-8");
  const selectorMatch = appSource.match(
    /if \(result\.interactiveAction === "agent-type-selector"\) \{[\s\S]*?\n          \}/,
  );

  assert.ok(selectorMatch, "agent-type selector handler should be present");
  const selectorSource = selectorMatch[0];
  assert.match(selectorSource, /saveWorkspaceConfig/);
  assert.match(selectorSource, /setPendingRuntime/);
  assert.match(selectorSource, /presentAgentTypeConfigSaved/);
  assert.doesNotMatch(selectorSource, /switchAgentMode|switchPermissionMode|refreshStatus/);
});

// resolveLiveBlocks 相关回归测试已删除：alt-screen + VirtualScrollList 单一渲染
// 管线从根上消除了「两套机制并存导致重复显示」的竞态（P1 端到端实证 0 重复）。
// 新架构的虚拟滚动算法回归测试见 ink-ext/VirtualScrollList.test.ts。
