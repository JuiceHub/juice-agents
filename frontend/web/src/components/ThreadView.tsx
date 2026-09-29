import React, { useMemo, useRef, useState } from "react";
import { ArrowUp, CheckCircle2, Copy, Square, XCircle } from "lucide-react";
import type { AskOption, AskRequest, PluginInfo, RuntimeModelInfo, SessionStatus, SkillInfo } from "@juice-agents/shared/gateway/types";
import type { MessageBlock } from "@juice-agents/shared/presenter/stream";
import { messageBlockToText } from "@juice-agents/shared/presenter/command";
import { MarkdownContent } from "./MarkdownContent.js";
import {
  acceptWebCompletion,
  getVisibleWebCompletions,
  getWebCommandCandidates,
  type CompletionCandidate,
} from "../lib/completion.js";

export const MODEL_EFFORT_OPTIONS = ["disabled", "low", "medium", "high", "xhigh", "max", "auto"] as const;
export type ModelEffort = (typeof MODEL_EFFORT_OPTIONS)[number];

export function getEffortsForModel(model: Pick<RuntimeModelInfo, "supported_efforts"> | undefined): ModelEffort[] {
  if (!model) return [...MODEL_EFFORT_OPTIONS];
  const efforts = (model.supported_efforts || [])
    .map((effort) => String(effort || "").trim().toLowerCase())
    .filter((effort): effort is ModelEffort => MODEL_EFFORT_OPTIONS.includes(effort as ModelEffort));
  const unique = [...new Set(efforts)];
  return unique.length > 0 ? unique : ["disabled"];
}

function normalizeEffortForModel(effort: string | undefined, model: RuntimeModelInfo | undefined): ModelEffort {
  const normalized = String(effort || "disabled").trim().toLowerCase();
  const candidate = MODEL_EFFORT_OPTIONS.includes(normalized as ModelEffort) ? (normalized as ModelEffort) : "disabled";
  const efforts = getEffortsForModel(model);
  return efforts.includes(candidate) ? candidate : efforts[0] || "disabled";
}

interface ThreadViewProps {
  status: SessionStatus | null;
  title?: string;
  subtitle?: string;
  activeActorName: string | null;
  models: RuntimeModelInfo[];
  skills: SkillInfo[];
  plugins: PluginInfo[];
  availableAgentNames: string[];
  selectedModelName: string;
  selectedModelEffort: ModelEffort;
  messages: MessageBlock[];
  streaming: boolean;
  actorBusy?: boolean;
  queuedMessages: string[];
  runtimeSwitching: boolean;
  error: string;
  pendingAsk: AskRequest | null;
  onSend: (text: string) => void;
  onCancel: () => void;
  onRuntimeChange: (modelName: string, modelEffort: ModelEffort) => void;
  onAnswerAsk: (selected: AskOption[], customResponse?: string) => void;
  onOpenFile: (path: string) => void;
}

export function ThreadView(props: ThreadViewProps) {
  const [draft, setDraft] = useState("");
  const title = props.title || props.status?.runner_id || "New runner";
  const isSubactor = Boolean(props.activeActorName);

  function submit(): void {
    if (!draft.trim()) return;
    props.onSend(draft);
    setDraft("");
  }

  return (
    <main className="thread">
      <header className="thread-header">
        <div>
          <div className="thread-title">{title}</div>
          <div className="thread-subtitle">
            {props.subtitle
              ? props.subtitle
              : props.status
              ? `${props.status.agent_mode} · ${props.status.permission_mode} · ${props.status.model_name} ${props.status.model_effort}`
              : "Start a runner thread from the composer"}
          </div>
        </div>
      </header>

      <section className="transcript">
        {isSubactor ? (
          <div className="readonly-context-banner">
            Viewing subactor context. Messages are sent to this actor.
          </div>
        ) : null}
        {props.messages.length === 0 ? (
          <div className="welcome-copy">
            {isSubactor
              ? "This subactor has no transcript entries yet."
              : "你好！我在这儿。你想让我帮你看代码、跑模型脚本，还是先聊聊当前项目要做什么？"}
          </div>
        ) : (
          props.messages.map((message, index) => (
            <MessageArticle key={message.id || index} message={message} onOpenFile={props.onOpenFile} />
          ))
        )}
        {props.streaming ? <div className="thinking-placeholder">Thinking...</div> : null}
        {props.error ? <div className="error-banner">{props.error}</div> : null}
      </section>

      {props.queuedMessages.length > 0 ? (
        <div className="queued-messages">
          <div className="queued-messages-header">queued ({props.queuedMessages.length})</div>
          {props.queuedMessages.slice(0, 3).map((msg, index) => (
            <div key={index} className="queued-message-item">
              {index + 1}. {msg.length > 72 ? `${msg.slice(0, 69)}...` : msg}
            </div>
          ))}
          {props.queuedMessages.length > 3 ? (
            <div className="queued-message-overflow">...and {props.queuedMessages.length - 3} more</div>
          ) : null}
        </div>
      ) : null}

      {props.pendingAsk ? (
        <AskPrompt request={props.pendingAsk} onAnswer={props.onAnswerAsk} />
      ) : null}

      <ComposerDock
        draft={draft}
        placeholder={isSubactor ? `Send instruction to ${props.activeActorName}` : "Ask for follow-up changes"}
        models={props.models}
        skills={props.skills}
        plugins={props.plugins}
        availableAgentNames={props.availableAgentNames}
        selectedModelName={props.selectedModelName}
        selectedModelEffort={props.selectedModelEffort}
        streaming={props.streaming}
        actorBusy={Boolean(props.actorBusy)}
        runtimeSwitching={props.runtimeSwitching}
        onChange={setDraft}
        onSubmit={submit}
        onCancel={props.onCancel}
        onRuntimeChange={props.onRuntimeChange}
      />
    </main>
  );
}

function MessageArticle({ message, onOpenFile }: { message: MessageBlock; onOpenFile: (path: string) => void }) {
  const copyText = message.display
    ? messageBlockToText(message)
    : message.toolCall
    ? `${message.text}\n${Object.entries(message.toolCall.parameters || {})
        .map(([key, value]) => `${key}: ${value}`)
        .join("\n")}`
    : message.text;
  return (
    <article className={`message ${message.kind}`}>
      {message.kind === "tool" && message.toolCall ? (
        <ToolCallCard message={message} />
      ) : message.display ? (
        <StructuredMessageBody message={message} />
      ) : shouldRenderMarkdown(message) ? (
        <MarkdownContent text={message.text} onOpenFile={onOpenFile} />
      ) : (
        <div className="message-body">{message.text}</div>
      )}
      <div className="message-actions">
        <button
          className="message-action-button"
          title="Copy message"
          onClick={() => {
            if (!navigator.clipboard) {
              console.warn("Clipboard API is unavailable");
              return;
            }
            void navigator.clipboard.writeText(copyText).catch((exc) => {
              console.warn("Failed to copy message", exc);
            });
          }}
        >
          <Copy size={14} />
        </button>
      </div>
    </article>
  );
}

function shouldRenderMarkdown(message: MessageBlock): boolean {
  return !message.display && ["assistant", "final", "system", "thinking"].includes(message.kind);
}

function StructuredMessageBody({ message }: { message: MessageBlock }) {
  const display = message.display;
  if (!display) return <div className="message-body">{message.text}</div>;
  return (
    <div className={`structured-block structured-${display.type}`}>
      {message.title ? <div className="structured-title">{message.title}</div> : null}
      {display.type === "table" ? (
        <div className="structured-table-scroll">
          <table className="structured-table">
            <thead>
              <tr>
                {display.columns.map((column) => (
                  <th key={column.key}>{column.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {display.rows.map((row, index) => (
                <tr key={index}>
                  {display.columns.map((column) => (
                    <td key={`${index}-${column.key}`}>{row[column.key] || ""}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {display.type === "kv" ? (
        <dl className="structured-kv-list">
          {display.items.map((item) => (
            <div className="structured-kv-row" key={item.label}>
              <dt>{item.label}</dt>
              <dd>{item.value || " "}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {display.type === "list" ? (
        <div className="structured-list-items">
          {display.items.map((item) => (
            <div className="structured-list-row" key={`${item.label}-${item.description || ""}`}>
              <span>{item.label}</span>
              {item.description ? <small>{item.description}</small> : null}
            </div>
          ))}
        </div>
      ) : null}
      {display.type === "code" ? (
        <pre className="structured-code-block">
          <code>{display.content || " "}</code>
        </pre>
      ) : null}
    </div>
  );
}

function ToolCallCard({ message }: { message: MessageBlock }) {
  const toolCall = message.toolCall;
  if (!toolCall) return <div className="message-body">{message.text}</div>;
  const entries = Object.entries(toolCall.parameters || {});
  const failed = toolCall.status === "error";
  return (
    <details className="tool-call-card">
      <summary className="tool-call-summary">
        <span className={`tool-call-state ${failed ? "failed" : ""}`} aria-label={failed ? "Tool failed" : "Tool succeeded"}>
          {failed ? <XCircle size={15} /> : <CheckCircle2 size={15} />}
        </span>
        <span className="tool-call-title">{message.title || message.text}</span>
        <span className="tool-call-mode">{toolCall.mode}</span>
      </summary>
      {entries.length > 0 ? (
        <dl className="tool-call-params">
          {entries.map(([key, value]) => (
            <div className="tool-call-param" key={`${toolCall.name}-${key}`}>
              <dt>{key}</dt>
              <dd>{value || "(empty)"}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <div className="tool-call-empty">No key parameters</div>
      )}
    </details>
  );
}

function ComposerDock(props: {
  draft: string;
  placeholder: string;
  models: RuntimeModelInfo[];
  skills: SkillInfo[];
  plugins: PluginInfo[];
  availableAgentNames: string[];
  selectedModelName: string;
  selectedModelEffort: ModelEffort;
  streaming: boolean;
  actorBusy?: boolean;
  runtimeSwitching: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  onRuntimeChange: (modelName: string, modelEffort: ModelEffort) => void;
}) {
  const runtimeDisabled = props.streaming || Boolean(props.actorBusy) || props.runtimeSwitching;
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [completionVisible, setCompletionVisible] = useState(false);
  const [selectedCompletionIndex, setSelectedCompletionIndex] = useState(0);
  const runtimeModelNames = useMemo(() => props.models.map((model) => model.model_name), [props.models]);
  const completionCandidates = useMemo(
    () =>
      getWebCommandCandidates({
        input: props.draft,
        cursor: textareaRef.current?.selectionStart ?? props.draft.length,
        runtimeModelNames,
        skills: props.skills,
        plugins: props.plugins,
        availableAgentNames: props.availableAgentNames,
      }),
    [props.availableAgentNames, props.draft, props.plugins, props.skills, runtimeModelNames]
  );
  const visibleCompletionCandidates = useMemo(
    () =>
      getVisibleWebCompletions({
        candidates: completionCandidates,
        selectedIndex: selectedCompletionIndex,
      }),
    [completionCandidates, selectedCompletionIndex]
  );
  const modelOptions = props.models.length > 0
    ? props.models
    : [{ model_name: props.selectedModelName, backend: "", provider_model_name: "" }];
  const selectedModel = modelOptions.find((m) => m.model_name === props.selectedModelName);
  const effortOptions = getEffortsForModel(selectedModel);

  function updateCompletion(nextDraft: string, cursor?: number): void {
    const matches = getWebCommandCandidates({
      input: nextDraft,
      cursor,
      runtimeModelNames,
      skills: props.skills,
      plugins: props.plugins,
      availableAgentNames: props.availableAgentNames,
    });
    setSelectedCompletionIndex(0);
    setCompletionVisible(matches.length > 0);
  }

  function acceptCompletion(candidate: CompletionCandidate): void {
    const textarea = textareaRef.current;
    const result = acceptWebCompletion({
      input: props.draft,
      cursor: textarea?.selectionStart ?? props.draft.length,
      candidate,
    });
    props.onChange(result.input);
    setCompletionVisible(false);
    setSelectedCompletionIndex(0);
    window.requestAnimationFrame(() => {
      textareaRef.current?.setSelectionRange(result.cursor, result.cursor);
      textareaRef.current?.focus();
    });
  }

  return (
    <footer className="composer-area">
      <div className="composer-wrap floating-composer">
        {completionVisible && completionCandidates.length > 0 ? (
          <div className="slash-completion" role="listbox" aria-label="Slash command completions">
            {visibleCompletionCandidates.map((candidate) => (
              <button
                key={`${candidate.label}-${candidate.originalIndex}`}
                className={`slash-completion-row ${candidate.originalIndex === selectedCompletionIndex ? "active" : ""}`}
                type="button"
                role="option"
                aria-selected={candidate.originalIndex === selectedCompletionIndex}
                onMouseDown={(event) => {
                  event.preventDefault();
                  acceptCompletion(candidate);
                }}
              >
                <span>{candidate.label}</span>
                <small>{candidate.description}</small>
              </button>
            ))}
            <div className="slash-completion-hint">Tab accept · Esc close</div>
          </div>
        ) : null}
        <textarea
          ref={textareaRef}
          value={props.draft}
          onChange={(event) => {
            props.onChange(event.target.value);
            updateCompletion(event.target.value, event.target.selectionStart);
          }}
          onSelect={(event) => updateCompletion(props.draft, event.currentTarget.selectionStart)}
          onKeyDown={(event) => {
            if (completionVisible && completionCandidates.length > 0) {
              if (event.key === "Tab") {
                event.preventDefault();
                acceptCompletion(completionCandidates[selectedCompletionIndex] || completionCandidates[0]);
                return;
              }
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setSelectedCompletionIndex((current) => (current + 1) % completionCandidates.length);
                return;
              }
              if (event.key === "ArrowUp") {
                event.preventDefault();
                setSelectedCompletionIndex((current) => (current - 1 + completionCandidates.length) % completionCandidates.length);
                return;
              }
              if (event.key === "Escape") {
                event.preventDefault();
                setCompletionVisible(false);
                setSelectedCompletionIndex(0);
                return;
              }
            }
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              props.onSubmit();
            }
          }}
          placeholder={props.placeholder}
        />
        <div className="composer-controls">
          <label className="composer-select-label" title="Model">
            <select
              value={props.selectedModelName}
              disabled={runtimeDisabled}
              onChange={(event) => {
                const nextModel = modelOptions.find((m) => m.model_name === event.target.value);
                props.onRuntimeChange(event.target.value, normalizeEffortForModel(props.selectedModelEffort, nextModel));
              }}
              aria-label="Model"
            >
              {modelOptions.map((model) => (
                <option key={model.model_name} value={model.model_name}>
                  {model.model_name}
                </option>
              ))}
            </select>
          </label>
          <label className="composer-select-label" title="Model effort">
            <select
              value={normalizeEffortForModel(props.selectedModelEffort, selectedModel)}
              disabled={runtimeDisabled}
              onChange={(event) => props.onRuntimeChange(props.selectedModelName, event.target.value as ModelEffort)}
              aria-label="Model effort"
            >
              {effortOptions.map((effort) => (
                <option key={effort} value={effort}>
                  {effort}
                </option>
              ))}
            </select>
          </label>
          <span className="composer-spacer" />
          {props.streaming || props.actorBusy ? (
            <button
              className="stop-button"
              onClick={props.onCancel}
              aria-label="Stop"
              title="停止当前 agent 运行"
            >
              <Square size={14} fill="currentColor" />
            </button>
          ) : (
            <button
              className="send-button"
              onClick={props.onSubmit}
              disabled={!props.draft.trim()}
            >
              <ArrowUp size={18} />
            </button>
          )}
        </div>
      </div>
    </footer>
  );
}

function AskPrompt(props: {
  request: AskRequest;
  onAnswer: (selected: AskOption[], customResponse?: string) => void;
}) {
  const [custom, setCustom] = useState("");
  const [customActive, setCustomActive] = useState(false);
  const customInputRef = useRef<HTMLInputElement>(null);

  function selectCustom(): void {
    setCustomActive(true);
    customInputRef.current?.focus();
  }

  return (
    <section className="ask-prompt">
      <strong>{props.request.question}</strong>
      <div className="ask-options">
        {props.request.options.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => {
              setCustomActive(false);
              props.onAnswer([option]);
            }}
          >
            {option.label}
          </button>
        ))}
        {props.request.allow_custom ? (
          <button type="button" aria-pressed={customActive} onClick={selectCustom}>
            Other
          </button>
        ) : null}
      </div>
      {props.request.allow_custom ? (
        <div className="ask-custom">
          <input
            ref={customInputRef}
            value={custom}
            onChange={(event) => {
              setCustomActive(true);
              setCustom(event.target.value);
            }}
            onFocus={() => setCustomActive(true)}
            placeholder="Custom answer"
          />
          <button type="button" disabled={!custom.trim()} onClick={() => props.onAnswer([], custom)}>
            Send
          </button>
        </div>
      ) : null}
    </section>
  );
}
