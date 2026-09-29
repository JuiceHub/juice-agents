import type { RunnerLifecycleEvent } from "../gateway/types.js";
import { INTERRUPT_MESSAGE, type MessageBlock } from "../presenter/stream.js";

/**
 * Conversation state is transport-aware: every submitted request owns a token
 * until its Promise settles.  Keeping all tokens prevents an older request's
 * `finally` block from making a newer request look idle.
 */
export interface ConversationState {
  messages: MessageBlock[];
  streaming: boolean;
  streamInFlight: boolean;
  streamFailed: boolean;
  activeGeneration: number | null;
  inFlightTokens: string[];
  lifecycleEvent: RunnerLifecycleEvent | null;
}

export type ConversationAction =
  | {
      type: "stream_started";
      generation: number;
      token: string;
      message: string;
    }
  | {
      type: "stream_event";
      generation: number;
      blocks: MessageBlock[];
      lifecycleEvent?: RunnerLifecycleEvent | null;
    }
  | { type: "stream_cancelled"; generation: number }
  | { type: "stream_settled"; generation: number; token: string }
  | { type: "stream_failed"; generation: number; message: string }
  | { type: "stream_succeeded"; generation: number }
  | { type: "transport_failed"; message: string }
  | {
      type: "ambient_event";
      blocks: MessageBlock[];
      lifecycleEvent?: RunnerLifecycleEvent | null;
    }
  | { type: "messages_added"; messages: MessageBlock[] }
  | { type: "messages_replaced"; messages: MessageBlock[] }
  | { type: "message_removed"; id: string }
  | { type: "messages_cleared" };

export function createConversationState(messages: MessageBlock[] = []): ConversationState {
  return {
    messages,
    streaming: false,
    streamInFlight: false,
    streamFailed: false,
    activeGeneration: null,
    inFlightTokens: [],
    lifecycleEvent: null,
  };
}

export function conversationReducer(
  state: ConversationState,
  action: ConversationAction,
): ConversationState {
  if (action.type === "stream_started") {
    const inFlightTokens = state.inFlightTokens.includes(action.token)
      ? state.inFlightTokens
      : [...state.inFlightTokens, action.token];
    return {
      ...state,
      messages: [...state.messages, { kind: "user", text: action.message }],
      streaming: true,
      streamInFlight: inFlightTokens.length > 0,
      streamFailed: false,
      activeGeneration: action.generation,
      inFlightTokens,
      lifecycleEvent: null,
    };
  }

  if (action.type === "stream_event") {
    if (state.activeGeneration !== action.generation) return state;
    return {
      ...state,
      messages: action.blocks.length > 0
        ? [...state.messages, ...action.blocks]
        : state.messages,
      lifecycleEvent: action.lifecycleEvent === undefined
        ? state.lifecycleEvent
        : action.lifecycleEvent,
    };
  }

  if (action.type === "messages_added") {
    return action.messages.length > 0
      ? { ...state, messages: [...state.messages, ...action.messages] }
      : state;
  }
  if (action.type === "ambient_event") {
    return {
      ...state,
      messages: action.blocks.length > 0 ? [...state.messages, ...action.blocks] : state.messages,
      lifecycleEvent: action.lifecycleEvent === undefined
        ? state.lifecycleEvent
        : action.lifecycleEvent,
    };
  }
  if (action.type === "messages_replaced") {
    return { ...state, messages: action.messages };
  }
  if (action.type === "message_removed") {
    return { ...state, messages: state.messages.filter((message) => message.id !== action.id) };
  }
  if (action.type === "messages_cleared") {
    return { ...state, messages: [] };
  }
  if (action.type === "transport_failed") {
    return {
      ...state,
      streamFailed: true,
      messages: [...state.messages, { kind: "error", text: action.message }],
    };
  }
  if (action.type === "stream_failed") {
    if (state.activeGeneration !== action.generation) return state;
    return {
      ...state,
      streamFailed: true,
      messages: [...state.messages, { kind: "error", text: action.message }],
    };
  }
  if (action.type === "stream_succeeded") {
    return state.activeGeneration === action.generation
      ? { ...state, streamFailed: false }
      : state;
  }

  if (action.type === "stream_cancelled") {
    if (state.activeGeneration !== action.generation) return state;
    const last = state.messages[state.messages.length - 1];
    const messages = last?.kind === "system" && last.text === INTERRUPT_MESSAGE
      ? state.messages
      : [...state.messages, { kind: "system" as const, text: INTERRUPT_MESSAGE }];
    return {
      ...state,
      messages,
      streaming: false,
      activeGeneration: null,
      lifecycleEvent: null,
    };
  }

  const inFlightTokens = state.inFlightTokens.filter((token) => token !== action.token);
  const isActive = state.activeGeneration === action.generation;
  return {
    ...state,
    streaming: isActive ? false : state.streaming,
    streamInFlight: inFlightTokens.length > 0,
    activeGeneration: isActive ? null : state.activeGeneration,
    inFlightTokens,
    lifecycleEvent: isActive ? null : state.lifecycleEvent,
  };
}
