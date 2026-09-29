import type { ActorSessionsReport, SessionSummary } from "@juice-agents/shared/gateway/types";
import type { MessageBlock } from "@juice-agents/shared/presenter/stream";
import { buildSubactorNavItems, projectRunnerLabel } from "./layout.js";

export type WorkspaceSearchResult =
  | {
      kind: "session";
      id: string;
      title: string;
      detail: string;
      runnerId: string;
    }
  | {
      kind: "actor";
      id: string;
      title: string;
      detail: string;
      actorName: string;
    }
  | {
      kind: "message";
      id: string;
      title: string;
      detail: string;
      text: string;
    };

export function buildWorkspaceSearchResults(params: {
  query: string;
  sessions: SessionSummary[];
  actorSessionsReport: ActorSessionsReport | null;
  messages: MessageBlock[];
}): WorkspaceSearchResult[] {
  const query = params.query.trim().toLowerCase();
  const results: WorkspaceSearchResult[] = [];

  for (const session of params.sessions) {
    const title = projectRunnerLabel({
      runnerId: session.runner_id,
      firstUserRequestPreview: session.first_user_request_preview,
      goalPreview: session.goal_objective_preview,
    });
    const detail = [session.agent_mode, session.permission_mode, session.goal_status, session.updated_at].filter(Boolean).join(" · ");
    results.push({
      kind: "session",
      id: `session-${session.runner_id}`,
      title,
      detail,
      runnerId: session.runner_id,
    });
  }

  for (const actor of buildSubactorNavItems(params.actorSessionsReport, null)) {
    results.push({
      kind: "actor",
      id: `actor-${actor.actorId}`,
      title: actor.label,
      detail: actor.description,
      actorName: actor.actorName,
    });
  }

  params.messages.forEach((message, index) => {
    const text = message.text.trim();
    if (!text) return;
    results.push({
      kind: "message",
      id: `message-${message.id || index}`,
      title: `${message.kind} message`,
      detail: text.slice(0, 180),
      text,
    });
  });

  if (!query) return results.slice(0, 80);
  return results
    .filter((result) => [result.kind, result.title, result.detail].join("\n").toLowerCase().includes(query))
    .slice(0, 80);
}
