import type {
  AskRequest,
  RunnerStreamEvent,
  StreamMessageResult,
} from "../gateway/types.js";

/** Transport contract shared by the stdio CLI and WebSocket browser client. */
export interface ConversationStreamClient {
  streamMessage(
    message: string,
    onEvent: (event: RunnerStreamEvent) => void,
    onAsk?: (request: AskRequest) => void,
    agentModeOverride?: string,
  ): Promise<StreamMessageResult>;

  cancelStream(): Promise<{ cancelled: boolean }>;
  setAmbientStreamHandler(handler: ((event: RunnerStreamEvent) => void) | null): void;
  setAmbientAskHandler(handler: ((request: AskRequest) => void) | null): void;
}
