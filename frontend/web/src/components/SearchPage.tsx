import React, { useMemo, useState } from "react";
import { GitBranch, MessageSquareText, Search, Workflow } from "lucide-react";
import type { ActorSessionsReport, SessionSummary } from "@juice-agents/shared/gateway/types";
import type { MessageBlock } from "@juice-agents/shared/presenter/stream";
import { buildWorkspaceSearchResults } from "../lib/search.js";

export function SearchPage(props: {
  sessions: SessionSummary[];
  actorSessionsReport: ActorSessionsReport | null;
  messages: MessageBlock[];
  onResumeRunner: (runnerId: string) => void;
  onSelectSubactor: (actorName: string) => void;
  onShowThread: () => void;
}) {
  const [query, setQuery] = useState("");
  const results = useMemo(
    () =>
      buildWorkspaceSearchResults({
        query,
        sessions: props.sessions,
        actorSessionsReport: props.actorSessionsReport,
        messages: props.messages,
      }),
    [query, props.sessions, props.actorSessionsReport, props.messages]
  );

  return (
    <main className="workspace-page search-page">
      <header className="workspace-page-toolbar search-only-toolbar">
        <label className="toolbar-search wide">
          <Search size={14} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search sessions, actors, and messages"
            aria-label="Search workspace"
            autoFocus
          />
        </label>
      </header>

      <section className="workspace-page-body">
        <div className="page-heading">
          <h1>Search</h1>
          <p>Find runner threads, subactors, and visible transcript entries.</p>
        </div>

        {results.length === 0 ? (
          <div className="page-empty">
            <strong>No results</strong>
            <span>Search another term or start a runner thread.</span>
          </div>
        ) : (
          <div className="search-results">
            {results.map((result) => {
              const Icon =
                result.kind === "session" ? Workflow : result.kind === "actor" ? GitBranch : MessageSquareText;
              return (
                <button
                  key={result.id}
                  className="search-result"
                  onClick={() => {
                    if (result.kind === "session") {
                      props.onResumeRunner(result.runnerId);
                    } else if (result.kind === "actor") {
                      props.onSelectSubactor(result.actorName);
                    } else {
                      props.onShowThread();
                    }
                  }}
                >
                  <Icon size={16} />
                  <span>
                    <strong>{result.title}</strong>
                    <small>{result.detail}</small>
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </section>
    </main>
  );
}
