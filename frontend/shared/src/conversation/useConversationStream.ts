import { useCallback, useEffect, useRef, useState } from "react";
import type { ActiveAgentsReport, AskRequest, RunnerStreamEvent } from "../gateway/types.js";
import type { MessageBlock } from "../presenter/stream.js";
import type { ConversationStreamClient } from "./client.js";
import { conversationReducer, createConversationState, type ConversationAction } from "./state.js";
export type SendMessageOutcome = "completed" | "superseded" | "failed";
export interface UseConversationStreamOptions {
  runnerId?: string;
  onEvent?: (event: RunnerStreamEvent) => void;
  onActiveAgentsReport?: (report: ActiveAgentsReport) => void;
  onAskRequest?: (request: AskRequest | null) => void;
  acceptEvent?: (event: RunnerStreamEvent) => boolean;
  presentEvent?: (event: RunnerStreamEvent) => MessageBlock[];
}
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error); }
/** The hook coordinates I/O; all lifecycle decisions belong to the reducer. */
export function useConversationStream(client: ConversationStreamClient, options: UseConversationStreamOptions = {}) {
  const [state, setState] = useState(() => ({ ...createConversationState(), runnerId: options.runnerId ?? "" }));
  const stateRef = useRef(state);
  const generationRef = useRef(0);
  const tokenRef = useRef(0);
  const runnerScopeRef = useRef(options.runnerId);
  const displayedAskRef = useRef<AskRequest | null>(null);
  const cancellationRef = useRef<{
    runnerId: string;
    roundId: string;
    generation: number | null;
    promise: Promise<{
      cancelled: boolean;
    }>;
  } | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  // Commit synchronously so two notifications in the same React batch share
  // the reducer's deduplication and current round state.
  const dispatch = useCallback((action: ConversationAction) => {
    const previous = stateRef.current;
    const next = conversationReducer(previous, action);
    if (next === previous)
      return false;
    stateRef.current = next;
    setState(next);
    return true;
  }, []);
  const bindRunnerScope = useCallback((runnerId: string | undefined) => {
    if (runnerScopeRef.current === runnerId)
      return;
    runnerScopeRef.current = runnerId;
    generationRef.current++;
    cancellationRef.current = null;
    if (displayedAskRef.current) {
      displayedAskRef.current = null;
      optionsRef.current.onAskRequest?.(null);
    }
    dispatch({ type: "scope_changed", runnerId: runnerId ?? "" });
  }, [dispatch]);
  const deliverAsk = useCallback((request: AskRequest) => {
    const runnerId = runnerScopeRef.current;
    if (runnerId === "" || (request.runner_id && runnerId !== undefined && request.runner_id !== runnerId))
      return;
    displayedAskRef.current = request;
    optionsRef.current.onAskRequest?.(request);
  }, []);
  const deliverEvent = useCallback((event: RunnerStreamEvent) => {
    const runnerId = runnerScopeRef.current;
    if (runnerId !== undefined && (!runnerId || event.runner_id !== runnerId))
      return;
    if (optionsRef.current.acceptEvent && !optionsRef.current.acceptEvent(event))
      return;
    if (runnerId === undefined)
      runnerScopeRef.current = event.runner_id;
    if (!dispatch({ type: "stream_event", event,
      blocks: optionsRef.current.presentEvent?.(event) }))
      return;
    optionsRef.current.onEvent?.(event);
    if (stateRef.current.agentReport)
      optionsRef.current.onActiveAgentsReport?.(stateRef.current.agentReport);
    const ask = displayedAskRef.current;
    if (event.kind === "round_end" && ask && (!ask.runner_id || ask.runner_id === event.runner_id)
      && (ask.round_id === event.round_id || ask.cancellation_round_id === event.round_id)) {
      displayedAskRef.current = null;
      optionsRef.current.onAskRequest?.(null);
    }
  }, [dispatch]);
  const applySnapshot = useCallback((report: ActiveAgentsReport) => {
    if (dispatch({ type: "snapshot_received", report }))
      optionsRef.current.onActiveAgentsReport?.(report);
  }, [dispatch]);
  const sendMessage = useCallback(async (message: string, agentModeOverride?: string, runnerId?: string): Promise<SendMessageOutcome> => {
    if (runnerId !== undefined)
      bindRunnerScope(runnerId);
    const scope = runnerScopeRef.current;
    const generation = ++generationRef.current;
    const token = `${generation}:${++tokenRef.current}`;
    dispatch({ type: "stream_started", generation, token, message });
    try {
      const result = await client.streamMessage(message, deliverEvent, (request) => {
        if (scope === runnerScopeRef.current)
          deliverAsk(request);
      }, agentModeOverride);
      if (scope !== runnerScopeRef.current)
        return "superseded";
      dispatch({ type: "stream_succeeded", generation, roundId: result.result?.round_id });
      // Results inform the caller; only the permanent event stream commits UI
      // terminal state. A rejected cancellation never invents round_end.
      return result.result?.status === "failed" ? "failed" : "completed";
    }
    catch (error) {
      if (scope !== runnerScopeRef.current)
        return "superseded";
      dispatch({ type: "stream_failed", generation, message: errorMessage(error) });
      return "failed";
    }
    finally {
      if (scope === runnerScopeRef.current)
        dispatch({ type: "stream_settled", generation, token });
    }
  }, [bindRunnerScope, client, deliverAsk, deliverEvent, dispatch]);
  const cancelCurrentStream = useCallback((): Promise<{
    cancelled: boolean;
  }> => {
    const current = stateRef.current;
    const round = Object.values(current.activeRounds).find((event) => event.agent_role === "root");
    if (!round && !current.streamInFlight && !current.streaming)
      return Promise.resolve({ cancelled: false });
    const roundId = round?.round_id || "";
    const generation = roundId ? null : current.activeGeneration;
    const runnerId = current.runnerId;
    if (cancellationRef.current?.roundId === roundId && cancellationRef.current.runnerId === runnerId
      && cancellationRef.current.generation === generation)
      return cancellationRef.current.promise;
    dispatch({ type: "cancel_requested", roundId, generation: generation ?? undefined });
    const promise = client.cancelStream(round?.round_id ? { runner_id: round.runner_id, round_id: round.round_id } : undefined)
      .then((result) => {
      if (!result.cancelled && runnerScopeRef.current === runnerId) {
        dispatch({ type: "cancel_rejected", roundId, generation: generation ?? undefined, message: "Cancellation was not accepted; execution state is unchanged." });
      }
      return result;
    }).catch((error) => {
      if (runnerScopeRef.current === runnerId)
        dispatch({ type: "cancel_rejected", roundId, generation: generation ?? undefined, message: `Cancel failed: ${errorMessage(error)}` });
      throw error;
    }).finally(() => {
      if (cancellationRef.current?.promise === promise)
        cancellationRef.current = null;
    });
    cancellationRef.current = { runnerId, roundId, generation, promise };
    return promise;
  }, [client, dispatch]);
  const addMessage = useCallback((message: MessageBlock) => dispatch({ type: "messages_added", messages: [message] }), [dispatch]);
  const addMessages = useCallback((messages: MessageBlock[]) => dispatch({ type: "messages_added", messages }), [dispatch]);
  const replaceMessages = useCallback((messages: MessageBlock[]) => dispatch({ type: "messages_replaced", messages }), [dispatch]);
  const removeMessage = useCallback((id: string) => dispatch({ type: "message_removed", id }), [dispatch]);
  const clearMessages = useCallback(() => dispatch({ type: "messages_cleared" }), [dispatch]);
  useEffect(() => {
    client.setAmbientStreamHandler(deliverEvent);
    client.setTransportErrorHandler?.((error) => dispatch({ type: "transport_failed", message: error.message }));
    client.setAmbientAskHandler(deliverAsk);
    return () => { client.setAmbientStreamHandler(null); client.setAmbientAskHandler(null); client.setTransportErrorHandler?.(null); };
  }, [client, deliverAsk, deliverEvent, dispatch]);
  useEffect(() => { bindRunnerScope(options.runnerId); }, [bindRunnerScope, options.runnerId]);
  return { ...state, addMessage, addMessages, replaceMessages, removeMessage, clearMessages,
    bindRunner: bindRunnerScope, applySnapshot, sendMessage, cancelCurrentStream };
}
