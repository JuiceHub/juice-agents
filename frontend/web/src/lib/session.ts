import type { SessionStatus, WorkspaceRuntimeConfig } from "@juice-agents/shared/gateway/types";
import type { WebGatewayClient } from "../gateway/client.js";

export interface WebSessionContext {
  baseDir: string;
  status: SessionStatus | null;
  config: WorkspaceRuntimeConfig;
  modelName: string;
  modelEffort: string;
}

/** Create the replacement before committing any visible conversation changes. */
export async function startWebSession(
  client: Pick<WebGatewayClient, "command" | "describeSession">,
  context: WebSessionContext,
  callbacks: {
    commit: (status: SessionStatus) => void;
    reconcile: (status: SessionStatus | null) => void;
  },
): Promise<SessionStatus> {
  const runtime = context.config.runtime;
  let result: SessionStatus;
  try {
    result = await client.command<SessionStatus>("start_session", {
      base_dir: context.baseDir,
      permission_mode: context.status?.permission_mode || runtime?.permission_mode || "default",
      agent_mode: context.status?.agent_mode || runtime?.agent_mode || "agent",
      agent_type: context.status?.agent_type || runtime?.agent_type || "react",
      model_name: context.status?.model_name || context.modelName,
      model_effort: context.status?.model_effort || context.modelEffort,
    });
  } catch (error) {
    // Startup can fail after the gateway has stopped the previous Runner.
    // Never retain its ID solely because the old transcript is still visible.
    const actual = await client.describeSession().catch(() => null);
    // `started` describes Agent execution, not session validity: a newly
    // created empty Runner has an ID before its first round starts.
    callbacks.reconcile(actual?.runner_id ? actual : null);
    throw error;
  }
  callbacks.commit(result);
  return result;
}

export function isClearCommand(text: string): boolean {
  return /^\/clear(?:\s|$)/.test(text.trim());
}

/** A retained child transcript is interactive only in its original Runner. */
export function webActorMessageTarget(
  text: string,
  actorName: string | null,
  reportRunnerId: string | undefined,
  activeRunnerId: string,
): string | null {
  return actorName && activeRunnerId && reportRunnerId === activeRunnerId && !isClearCommand(text)
    ? actorName
    : null;
}

export type WebInputOutcome = "consumed" | "retry";

/**
 * Commands wait for a round boundary. Once a command is queued, every later
 * input follows it, including normal messages submitted from a child view.
 * Keep the queue outside React state so rapid submissions cannot overtake a
 * session switch before its state updates have rendered.
 */
export class WebComposerQueue {
  private pending: string[] = [];
  private flushing = false;
  private directInFlight = 0;
  private holds = 0;

  constructor(private readonly callbacks: {
    isBusy: () => boolean;
    execute: (text: string) => Promise<WebInputOutcome>;
    onChange: (pending: string[]) => void;
  }) {}

  async submit(text: string, waitForRound: boolean): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed) return;
    if (waitForRound || this.pending.length > 0 || this.flushing || this.holds > 0) {
      this.pending.push(trimmed);
      this.callbacks.onChange([...this.pending]);
      return;
    }
    // A cold first message starts by awaiting start_session, before shared
    // conversation state can report stream_started. Track that whole request
    // synchronously so a queued /clear cannot overtake its startup or round.
    // Other ordinary input can still go directly to an existing Runner inbox.
    this.directInFlight++;
    try {
      await this.callbacks.execute(trimmed);
    } finally {
      this.directInFlight--;
      if (this.pending.length > 0) this.callbacks.onChange([...this.pending]);
    }
  }

  /** Protect session creation initiated outside the composer, such as Browser. */
  hold(): () => void {
    this.holds++;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.holds--;
      if (this.holds === 0 && this.pending.length > 0) this.callbacks.onChange([...this.pending]);
    };
  }

  async flush(): Promise<void> {
    if (this.flushing || this.holds > 0 || this.directInFlight > 0 || this.callbacks.isBusy() || this.pending.length === 0) return;
    this.flushing = true;
    let consumed = false;
    try {
      consumed = await this.callbacks.execute(this.pending[0]) !== "retry";
      if (consumed) this.pending.shift();
    } finally {
      this.flushing = false;
      // Notify after releasing the lock so the next committed render can
      // drain the next item with the newly selected Runner and Agent view.
      // A failed send stays queued until a later input or state change.
      if (consumed) this.callbacks.onChange([...this.pending]);
    }
  }
}
