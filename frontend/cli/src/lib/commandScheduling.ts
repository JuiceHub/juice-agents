/**
 * CLI command scheduling policy.
 *
 * Only commands whose complete first token is listed here may bypass the
 * ordinary message FIFO. Keeping this classifier pure makes it impossible for
 * prefix lookalikes such as `/tasks-extra` to inherit privileged scheduling.
 */

export type CommandExecutionPolicy = "immediate" | "fifo";

// 只包含不刷新 root 有效配置的轻量指令。`/mode` 会在空闲边界刷新 root，
// 因此不在此列；`/permissions` 只改审批策略，可以在 step 边界插队。
const IMMEDIATE_COMMAND_TOKENS = new Set([
  "/actors",
  "/clear",
  "/exit",
  "/help",
  "/permissions",
  "/status",
  "/tasks",
]);

export function getCommandToken(input: string): string | null {
  const normalized = input.trim();
  if (!normalized.startsWith("/")) {
    return null;
  }
  return normalized.split(/\s+/, 1)[0] || null;
}

export function getCommandExecutionPolicy(input: string): CommandExecutionPolicy {
  // 两个维度拆成独立命令后，token 本身就决定了调度成本，无需再看参数。
  const token = getCommandToken(input);
  return token && IMMEDIATE_COMMAND_TOKENS.has(token) ? "immediate" : "fifo";
}

export function shouldQueueSubmission(params: {
  input: string;
  fromQueue?: boolean;
  streaming?: boolean;
  queuedCount?: number;
}): boolean {
  if (params.fromQueue || getCommandExecutionPolicy(params.input) === "immediate") {
    return false;
  }
  return Boolean(params.streaming || (params.queuedCount || 0) > 0);
}

export function shouldDeferModeSwitch(params: {
  currentMode: string;
  targetMode: string;
  streamInFlight: boolean;
}): boolean {
  return Boolean(
    params.streamInFlight &&
    (params.currentMode === "plan" || params.targetMode === "plan")
  );
}
