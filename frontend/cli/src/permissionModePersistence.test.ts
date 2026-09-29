import assert from "node:assert/strict";
import test from "node:test";

import {
  applyPermissionModeAlreadyActive,
  applyPermissionModeWithoutSession,
  persistPermissionMode,
} from "./app.js";
import type { PendingSessionRuntime } from "@juice-agents/shared/gateway/types";

/** 记录 saveWorkspaceConfig 收到的调用，替代真实 gateway。 */
function createRecordingClient() {
  const calls: Array<{ base_dir: string; config: Record<string, any> }> = [];
  return {
    calls,
    async saveWorkspaceConfig(params: { base_dir: string; config: Record<string, any> }) {
      calls.push(params);
      return { saved: true };
    },
  };
}

/**
 * agent_mode 固定用 `agent`：agent/plan profile 都承载 CodeAct，
 * team/group 会被 normalizePendingAgentType 强制回 react。
 */
function baseRuntime(): PendingSessionRuntime {
  return {
    base_dir: "/tmp/workspace",
    permission_mode: "default",
    agent_mode: "agent",
    agent_type: "codeact",
    model_name: "doubao_lite",
    model_effort: "high",
  };
}

test("cold-start permission switch persists the mode to workspace config", async () => {
  // 回归守卫：冷启动分支曾只更新内存态，导致 /permissions 的选择退出后丢失。
  const client = createRecordingClient();
  let runtime = baseRuntime();

  await applyPermissionModeWithoutSession({
    client: client as any,
    baseDir: "/tmp/workspace",
    permissionMode: "accept",
    setPendingMode: () => {},
    setPendingRuntime: (updater) => {
      runtime = updater(runtime);
    },
  });

  assert.equal(client.calls.length, 1, "无会话时也必须落盘 permission_mode");
  assert.deepEqual(client.calls[0], {
    base_dir: "/tmp/workspace",
    config: { runtime: { permission_mode: "accept" } },
  });
});

test("cold-start permission switch keeps the other runtime dimensions intact", async () => {
  const client = createRecordingClient();
  let runtime = baseRuntime();

  await applyPermissionModeWithoutSession({
    client: client as any,
    baseDir: "/tmp/workspace",
    permissionMode: "accept",
    setPendingMode: () => {},
    setPendingRuntime: (updater) => {
      runtime = updater(runtime);
    },
  });

  // 切权限不得连带改写执行模式 / agent 类型 / 模型选择。
  assert.equal(runtime.permission_mode, "accept");
  assert.equal(runtime.agent_mode, "agent");
  assert.equal(runtime.agent_type, "codeact");
  assert.equal(runtime.model_name, "doubao_lite");
  assert.equal(runtime.model_effort, "high");
});

test("cold-start permission switch clears the pending mode indicator", async () => {
  const client = createRecordingClient();
  const pendingModes: Array<string | null> = [];

  await applyPermissionModeWithoutSession({
    client: client as any,
    baseDir: "/tmp/workspace",
    permissionMode: "accept",
    setPendingMode: (mode) => pendingModes.push(mode),
    setPendingRuntime: (updater) => updater(baseRuntime()),
  });

  assert.deepEqual(pendingModes, [null]);
});

test("re-selecting the mode the session already runs still persists it", async () => {
  // 场景：用 --permission-mode accept 启动，但配置文件里没有该字段。用户执行
  // /permissions accept 是在表达「记住它」，不能因为「已经是该模式」就跳过落盘。
  const client = createRecordingClient();
  const pendingModes: Array<string | null> = [];

  await applyPermissionModeAlreadyActive({
    client: client as any,
    baseDir: "/tmp/workspace",
    permissionMode: "accept",
    setPendingMode: (mode) => pendingModes.push(mode),
  });

  assert.equal(client.calls.length, 1, "已处于目标模式时也必须落盘");
  assert.deepEqual(client.calls[0], {
    base_dir: "/tmp/workspace",
    config: { runtime: { permission_mode: "accept" } },
  });
  assert.deepEqual(pendingModes, [null]);
});

test("persistPermissionMode only touches runtime.permission_mode", async () => {
  const client = createRecordingClient();

  await persistPermissionMode(client as any, "/tmp/workspace", "default");

  const config = client.calls[0].config;
  assert.deepEqual(Object.keys(config), ["runtime"]);
  assert.deepEqual(Object.keys(config.runtime), ["permission_mode"]);
});

test("persistPermissionMode propagates gateway failures to the caller", async () => {
  // coordinator 依赖 rejection 渲染 "switch failed"；静默吞掉会让用户误以为已保存。
  const failing = {
    async saveWorkspaceConfig() {
      throw new Error("gateway down");
    },
  };

  await assert.rejects(
    () => persistPermissionMode(failing as any, "/tmp/workspace", "accept"),
    /gateway down/
  );
});
