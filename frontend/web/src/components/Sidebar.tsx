import React from "react";
import { Archive, ArrowLeft, Bot, Folder, GitBranch, Search, Settings, Sparkles, SquarePen } from "lucide-react";
import type {
  ActorSessionsReport,
  SessionSummary,
} from "@juice-agents/shared/gateway/types";
import { LEFT_NAV_ACTIONS, buildSubactorNavItems, projectRunnerLabel } from "../lib/layout.js";
import type { AppView, MainView } from "../App.js";

interface SidebarProps {
  baseDir: string;
  sessions: SessionSummary[];
  activeRunnerId: string;
  activeActorName: string | null;
  actorSessionsReport: ActorSessionsReport | null;
  activeView: AppView;
  onNewRunner: () => void;
  onSelectView: (view: MainView) => void;
  onResumeRunner: (runnerId: string) => void;
  onSelectSubactor: (actorName: string) => void;
  onArchiveRunner: (runnerId: string) => void;
  onOpenSettings: () => void;
  onBackToApp: () => void;
}

const actionIcons = [SquarePen, Search, Sparkles];

export function Sidebar(props: SidebarProps) {
  const projectName = props.baseDir.split("/").filter(Boolean).at(-1) || props.baseDir;
  const settingsMode = props.activeView === "settings";
  const subactors =
    props.actorSessionsReport?.runner_id === props.activeRunnerId
      ? buildSubactorNavItems(props.actorSessionsReport, props.activeActorName)
      : [];

  if (settingsMode) {
    return (
      <aside className="sidebar settings-sidebar">
        <nav className="sidebar-actions">
          <button className="sidebar-action" onClick={props.onBackToApp} title="Back to app">
            <ArrowLeft size={16} />
            <span>返回应用</span>
          </button>
        </nav>
        <section className="sidebar-section">
          <button className="sidebar-action active" title="Archived chats">
            <Archive size={16} />
            <span>Archived chats</span>
          </button>
        </section>
      </aside>
    );
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-main">
        <nav className="sidebar-actions">
          {LEFT_NAV_ACTIONS.map((label, index) => {
            const Icon = actionIcons[index] || Bot;
            const view = label === "Search" ? "search" : label === "Skills" ? "skills" : "thread";
            return (
              <button
                key={label}
                className={`sidebar-action ${props.activeView === view && label !== "New chat" ? "active" : ""}`}
                onClick={() => {
                  if (label === "New chat") {
                    props.onNewRunner();
                  } else {
                    props.onSelectView(view);
                  }
                }}
                title={label}
              >
                <Icon size={16} />
                <span>{label}</span>
              </button>
            );
          })}
        </nav>

        <section className="sidebar-section">
          <div className="sidebar-heading">Projects</div>
          <div className="project-title">
            <Folder size={15} />
            <span>{projectName}</span>
          </div>
          <div className="runner-list">
            {props.sessions.length === 0 ? (
              <div className="empty-row">No runner threads yet</div>
            ) : (
              props.sessions.map((session) => {
                const isActiveRunner = session.runner_id === props.activeRunnerId;
                return (
                  <div key={session.runner_id} className="runner-group">
                    <div className={`runner-row-wrap ${isActiveRunner && !props.activeActorName ? "active" : ""}`}>
                      <button
                        className="runner-row"
                        onClick={() => {
                          props.onSelectView("thread");
                          props.onResumeRunner(session.runner_id);
                        }}
                      >
                        <span className="runner-title">
                          {projectRunnerLabel({
                            runnerId: session.runner_id,
                            firstUserRequestPreview: session.first_user_request_preview,
                            goalPreview: session.goal_objective_preview,
                          })}
                        </span>
                        <span className="runner-meta">{session.agent_mode}</span>
                      </button>
                      <button
                        className="archive-runner-button"
                        title="Archive runner"
                        onClick={(event) => {
                          event.stopPropagation();
                          props.onArchiveRunner(session.runner_id);
                        }}
                      >
                        <Archive size={13} />
                      </button>
                    </div>
                    {isActiveRunner && subactors.length > 0 ? (
                      <div className="subactor-list">
                        {subactors.map((actor) => (
                          <button
                            key={actor.actorId}
                            className={`subactor-row ${actor.active ? "active" : ""}`}
                            onClick={() => {
                              props.onSelectView("thread");
                              props.onSelectSubactor(actor.actorName);
                            }}
                            title={actor.description}
                          >
                            <GitBranch size={13} />
                            <span className="runner-title">{actor.label}</span>
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>
        </section>
      </div>

      <button className="sidebar-action sidebar-settings-button" onClick={props.onOpenSettings} title="Settings">
        <Settings size={16} />
        <span>Settings</span>
      </button>
    </aside>
  );
}
