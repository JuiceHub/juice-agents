import test from "node:test";
import assert from "node:assert/strict";
import { buildWorkspaceSearchResults } from "./search.js";
import type { ActiveAgentsReport, SessionSummary } from "@juice-agents/shared/gateway/types";

test("workspace search includes sessions, subactors, and visible messages", () => {
  const sessions: SessionSummary[] = [
    {
      runner_id: "run-1",
      permission_mode: "default",
      agent_mode: "agent",
      root_agent_name: "root",
      updated_at: "2026-05-18",
      root_dir: "/tmp/work",
      first_user_request_preview: "Fix browser runner title",
      goal_objective_preview: "Fix browser panel",
    },
  ];
  const report: ActiveAgentsReport = {
    runner_id: "run-1",
    permission_mode: "default",
    agent_mode: "agent",
    root_agent_name: "root",
    agents: [
      makeActor({ agent_name: "root", is_root: true }),
      makeActor({ agent_name: "explorer", agent_kind: "local_agent" }),
    ],
  };
  const results = buildWorkspaceSearchResults({
    query: "browser",
    sessions,
    actorSessionsReport: report,
    messages: [{ id: "m1", kind: "assistant", text: "Browser canvas loaded" }],
  });

  assert.equal(results.length, 2);
  assert.equal(results[0].kind, "session");
  assert.equal(results[1].kind, "message");
});

function makeActor(overrides: Record<string, unknown> = {}) {
  return {
    agent_id: String(overrides.agent_name || "actor"),
    agent_name: "actor",
    agent_kind: "agent",
    agent_role: "worker",
    status: "idle",
    is_root: false,
    current_async_task_id: "",
    metadata: {},
    session: {},
    steps: [],
    ...overrides,
  };
}
