/** Idle scheduling heartbeat; React renders must not reset its cadence. */

import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import type { AsyncTaskState } from "@juice-agents/shared/gateway/types";
import type { MessageBlock } from "@juice-agents/shared/presenter/stream";
import type { GatewayClient } from "../gateway/client.js";

interface CronPollingOptions {
  client: Pick<GatewayClient, "fireDueCronTasks" | "listAsyncTasks">;
  baseDir: string;
  /** Task badges are scoped to this Runner; Cron prompts remain workspace-wide. */
  runnerId?: string;
  enabled: boolean;
  addMessage: (message: MessageBlock) => void;
  setAsyncTasks: Dispatch<SetStateAction<AsyncTaskState[]>>;
  setQueuedMessages: Dispatch<SetStateAction<string[]>>;
}

export function useCronPolling({
  client,
  baseDir,
  runnerId,
  enabled,
  addMessage,
  setAsyncTasks,
  setQueuedMessages,
}: CronPollingOptions): void {
  // Keep the latch across pause/resume and workspace changes: an older RPC
  // may still be settling when a new effect is installed.
  const inFlight = useRef(false);
  // Busy/idle transitions control requests, not the timer's lifetime. In
  // particular, a due task already claimed by an in-flight RPC must still be
  // queued if the user starts another round before its response arrives.
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const runnerIdRef = useRef(runnerId);
  runnerIdRef.current = runnerId;

  useEffect(() => {
    let active = true;

    const tick = async () => {
      if (!active || !enabledRef.current || inFlight.current) return;
      inFlight.current = true;
      try {
        const result = await client.fireDueCronTasks({ base_dir: baseDir });
        if (!active) return;
        const fired = result.fired || [];
        if (fired.length === 0) {
          if (!enabledRef.current) return;
          // Reuse the idle heartbeat to refresh the task badge. A fresh array
          // can rerender App, but cannot restart this effect or trigger a tick.
          try {
            const taskRunnerId = runnerIdRef.current;
            const tasks = await client.listAsyncTasks({ statuses: ["pending", "running"] });
            if (active && enabledRef.current && taskRunnerId === runnerIdRef.current) setAsyncTasks(tasks);
          } catch {
            // Badge refresh is best-effort; retry on the next scheduled tick.
          }
          return;
        }
        for (const task of fired) {
          addMessage({
            kind: "system",
            title: "Cron",
            text: `scheduled task fired: ${task.id} · ${task.cron}`,
          });
        }
        setQueuedMessages((previous) => [
          ...previous,
          ...fired.map((task) => task.prompt).filter(Boolean),
        ]);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (active && enabledRef.current && !message.includes("cron lock is held")) {
          addMessage({ kind: "error", text: `Cron tick failed: ${message}` });
        }
      } finally {
        inFlight.current = false;
      }
    };

    const timer = setInterval(() => { void tick(); }, 1000);
    void tick();
    return () => {
      // Workspace changes and unmounts cannot deliver an old response into a
      // different UI. Pausing only skips requests on the existing heartbeat.
      active = false;
      clearInterval(timer);
    };
  }, [client, baseDir, addMessage, setAsyncTasks, setQueuedMessages]);
}
