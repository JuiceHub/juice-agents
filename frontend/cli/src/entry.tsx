#!/usr/bin/env node
/**
 * Entry point for the Juice TypeScript + Ink CLI.
 */

import React from "react";
import { render } from "ink";
import { GatewayClient } from "./gateway/client.js";
import { App } from "./app.js";
import { createScrollbackSafeStdout } from "./lib/scrollbackSafeStdout.js";
import { resolveWorkspaceDir } from "./lib/workspace.js";
import { createMouseAwareStdin, DISABLE_MOUSE_TRACKING } from "./lib/mouseAwareStdin.js";

async function main() {
  // Parse command line arguments
  const args = process.argv.slice(2);
  const baseDir = resolveWorkspaceDir({
    env: process.env,
    cwd: process.cwd(),
  });
  let permissionMode: string | undefined;
  let agentMode: string | undefined;
  let agentType: string | undefined;
  let resumeRunnerId: string | undefined;
  let worktree: string | undefined;

  // Simple argument parsing
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--mode" && i + 1 < args.length) {
      // --mode 与 /mode 对应，只表示执行模式；审批策略走 --permissions。
      agentMode = args[i + 1];
      i++;
    } else if (args[i] === "--permissions" && i + 1 < args.length) {
      permissionMode = args[i + 1];
      i++;
    } else if (args[i] === "--agent-type" && i + 1 < args.length) {
      agentType = args[i + 1];
      i++;
    } else if (args[i] === "--resume" && i + 1 < args.length) {
      resumeRunnerId = args[i + 1];
      i++;
    } else if (args[i] === "--worktree") {
      if (i + 1 < args.length && !args[i + 1].startsWith("--")) {
        worktree = args[i + 1];
        i++;
      } else {
        worktree = "";
      }
    } else if (args[i] === "--help" || args[i] === "-h") {
      console.log(`
Juice CLI - TypeScript + Ink

Usage:
  juice [options]

Options:
  --mode <mode>           Set agent mode (agent, plan, team, group)
  --permissions <mode>    Set permission mode (default, accept)
  --agent-type <type>     Set agent type (react, codeact)
  --resume <runner_id>    Resume an existing session
  --worktree [name]       Start in a managed git worktree
  --help, -h              Show this help message

Examples:
  juice
  juice --mode team
  juice --permissions accept --agent-type codeact
  juice --resume abc123
  juice --worktree feature-x
      `);
      process.exit(0);
    }
  }

  // Create and start gateway client
  const client = new GatewayClient();

  // 包装 stdin：过滤 SGR mouse 序列（不让 mouse 字节泄漏到 ink useInput）
  // 非 TTY 时跳过（CI / 管道），emitter 为 null，App 退化为仅键盘滚动。
  const mouseHandle = process.stdin.isTTY
    ? createMouseAwareStdin(process.stdin)
    : null;

  // 异常退出兜底：确保 mouse tracking 在进程强退时也被禁用
  if (mouseHandle) {
    process.on("exit", () => process.stdout.write(DISABLE_MOUSE_TRACKING));
  }

  try {
    await client.start();

    // Render the app
    const stdout = createScrollbackSafeStdout(process.stdout);
    const stdinForInk = mouseHandle ? mouseHandle.stdin : process.stdin;
    const { waitUntilExit } = render(
      <App
        client={client}
        baseDir={baseDir}
        permissionMode={permissionMode}
        agentMode={agentMode}
        agentType={agentType}
        resumeRunnerId={resumeRunnerId}
        worktree={worktree}
        mouseEmitter={mouseHandle?.emitter ?? null}
      />,
      { exitOnCtrlC: false, stdout, stdin: stdinForInk as any }
    );

    // Wait for the app to exit
    await waitUntilExit();
  } catch (error) {
    console.error("Fatal error:", error);
    process.exit(1);
  } finally {
    client.stop();
    mouseHandle?.dispose();
  }
}

main().catch((error) => {
  console.error("Unhandled error:", error);
  process.exit(1);
});
