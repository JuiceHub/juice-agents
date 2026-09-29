import React from "react";
import { RotateCcw } from "lucide-react";
import type { SessionSummary } from "@juice-agents/shared/gateway/types";
import { projectRunnerLabel } from "../lib/layout.js";

interface ArchivedRunnersPageProps {
  sessions: SessionSummary[];
  loading: boolean;
  error: string;
  onRefresh: () => void;
  onRestoreRunner: (runnerId: string) => void;
}

export function ArchivedRunnersPage(props: ArchivedRunnersPageProps) {
  return (
    <main className="settings-workspace">
      <section className="settings-page">
        <header className="settings-page-header">
          <div>
            <div className="settings-eyebrow">Settings</div>
            <h1>Archived chats</h1>
          </div>
          <button className="settings-refresh" onClick={props.onRefresh} disabled={props.loading}>
            Refresh
          </button>
        </header>

        {props.error ? <div className="error-banner">{props.error}</div> : null}
        {props.loading ? <div className="settings-empty">Loading archived chats...</div> : null}
        {!props.loading && props.sessions.length === 0 ? (
          <div className="settings-empty">No archived chats.</div>
        ) : null}

        <div className="archived-runner-list">
          {props.sessions.map((session) => (
            <article className="archived-runner-row" key={session.runner_id}>
              <div className="archived-runner-main">
                <strong>
                  {projectRunnerLabel({
                    runnerId: session.runner_id,
                    firstUserRequestPreview: session.first_user_request_preview,
                    goalPreview: session.goal_objective_preview,
                  })}
                </strong>
                <span>
                  {session.agent_mode} · {session.permission_mode} · {session.updated_at}
                </span>
              </div>
              <button className="restore-runner-button" onClick={() => props.onRestoreRunner(session.runner_id)}>
                <RotateCcw size={15} />
                <span>Restore</span>
              </button>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
