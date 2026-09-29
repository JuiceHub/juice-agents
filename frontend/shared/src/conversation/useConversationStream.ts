import { useCallback, useEffect, useReducer, useRef } from "react";

import type {
  ActorSessionsReport,
  AskRequest,
  RunnerLifecycleEvent,
  RunnerStreamEvent,
} from "../gateway/types.js";
import {
  presentStreamEvent,
  type MessageBlock,
} from "../presenter/stream.js";
import type { ConversationStreamClient } from "./client.js";
import { conversationReducer, createConversationState } from "./state.js";

export type SendMessageOutcome = "completed" | "superseded" | "failed";

export interface UseConversationStreamOptions {
  onEvent?: (event: RunnerStreamEvent) => void;
  onActorSessionsReport?: (report: ActorSessionsReport) => void;
  onAskRequest?: (request: AskRequest | null) => void;
  acceptEvent?: (event: RunnerStreamEvent) => boolean;
  presentEvent?: (event: RunnerStreamEvent) => MessageBlock[];
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Owns the cross-surface conversation lifecycle.  Generation checks isolate UI
 * events while request tokens independently describe transport work that has
 * not settled yet.
 */
export function useConversationStream(
  client: ConversationStreamClient,
  options: UseConversationStreamOptions = {},
) {
  const [state, dispatch] = useReducer(conversationReducer, undefined, () => createConversationState());
  const generationRef = useRef(0);
  const activeGenerationRef = useRef<number | null>(null);
  const tokenRef = useRef(0);
  const cancellationRef = useRef<{
    generation: number;
    promise: Promise<{ cancelled: boolean }>;
  } | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const deliverEvent = useCallback((event: RunnerStreamEvent, generation?: number) => {
    if (generation !== undefined && generationRef.current !== generation) return;
    if (optionsRef.current.acceptEvent && !optionsRef.current.acceptEvent(event)) return;
    optionsRef.current.onEvent?.(event);
    if (event.actor_sessions_report) {
      optionsRef.current.onActorSessionsReport?.(event.actor_sessions_report);
    }
    const lifecycleEvent: RunnerLifecycleEvent | null | undefined =
      event.kind === "runner_lifecycle" ? event.lifecycle_event ?? null : undefined;
    dispatch({
      type: "stream_event",
      generation: generation ?? activeGenerationRef.current ?? generationRef.current,
      blocks: (optionsRef.current.presentEvent ?? presentStreamEvent)(event),
      lifecycleEvent,
    });
  }, []);

  const sendMessage = useCallback(async (
    message: string,
    agentModeOverride?: string,
  ): Promise<SendMessageOutcome> => {
    const generation = ++generationRef.current;
    const token = `${generation}:${++tokenRef.current}`;
    activeGenerationRef.current = generation;
    dispatch({ type: "stream_started", generation, token, message });

    try {
      const result = await client.streamMessage(
        message,
        (event) => deliverEvent(event, generation),
        (request) => {
          if (generationRef.current === generation) {
            optionsRef.current.onAskRequest?.(request);
          }
        },
        agentModeOverride,
      );
      if (generationRef.current !== generation) return "superseded";
      if (result?.stopped) {
        dispatch({ type: "stream_cancelled", generation });
        activeGenerationRef.current = null;
        optionsRef.current.onAskRequest?.(null);
      }
      dispatch({ type: "stream_succeeded", generation });
      return "completed";
    } catch (error) {
      if (generationRef.current !== generation) return "superseded";
      dispatch({ type: "stream_failed", generation, message: errorMessage(error) });
      optionsRef.current.onAskRequest?.(null);
      return "failed";
    } finally {
      dispatch({ type: "stream_settled", generation, token });
      if (activeGenerationRef.current === generation) {
        activeGenerationRef.current = null;
      }
    }
  }, [client, deliverEvent]);

  const cancelCurrentStream = useCallback((): Promise<{ cancelled: boolean }> => {
    const generation = activeGenerationRef.current;
    if (generation === null) {
      return cancellationRef.current?.promise ?? Promise.resolve({ cancelled: false });
    }
    if (cancellationRef.current?.generation === generation) {
      return cancellationRef.current.promise;
    }

    activeGenerationRef.current = null;
    generationRef.current++;
    dispatch({ type: "stream_cancelled", generation });
    optionsRef.current.onAskRequest?.(null);

    const promise = client.cancelStream().catch((error) => {
      dispatch({ type: "transport_failed", message: `Cancel failed: ${errorMessage(error)}` });
      throw error;
    }).finally(() => {
      if (cancellationRef.current?.generation === generation) {
        cancellationRef.current = null;
      }
    });
    cancellationRef.current = { generation, promise };
    return promise;
  }, [client]);

  const addMessage = useCallback((message: MessageBlock) => {
    dispatch({ type: "messages_added", messages: [message] });
  }, []);
  const addMessages = useCallback((messages: MessageBlock[]) => {
    dispatch({ type: "messages_added", messages });
  }, []);
  const replaceMessages = useCallback((messages: MessageBlock[]) => {
    dispatch({ type: "messages_replaced", messages });
  }, []);
  const removeMessage = useCallback((id: string) => {
    dispatch({ type: "message_removed", id });
  }, []);
  const clearMessages = useCallback(() => {
    dispatch({ type: "messages_cleared" });
  }, []);

  useEffect(() => {
    client.setAmbientStreamHandler((event) => {
      if (optionsRef.current.acceptEvent && !optionsRef.current.acceptEvent(event)) return;
      optionsRef.current.onEvent?.(event);
      if (event.actor_sessions_report) {
        optionsRef.current.onActorSessionsReport?.(event.actor_sessions_report);
      }
      dispatch({
        type: "ambient_event",
        blocks: (optionsRef.current.presentEvent ?? presentStreamEvent)(event),
        lifecycleEvent: event.kind === "runner_lifecycle" ? event.lifecycle_event ?? null : undefined,
      });
    });
    client.setAmbientAskHandler((request) => optionsRef.current.onAskRequest?.(request));
    return () => {
      client.setAmbientStreamHandler(null);
      client.setAmbientAskHandler(null);
    };
  }, [client]);

  return {
    messages: state.messages,
    streaming: state.streaming,
    streamInFlight: state.streamInFlight,
    streamFailed: state.streamFailed,
    lifecycleEvent: state.lifecycleEvent,
    addMessage,
    addMessages,
    replaceMessages,
    removeMessage,
    clearMessages,
    sendMessage,
    cancelCurrentStream,
  };
}
