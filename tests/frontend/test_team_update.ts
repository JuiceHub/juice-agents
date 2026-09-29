import test from "node:test";
import assert from "node:assert/strict";
import { presentStreamEvent } from "../../frontend/shared/src/presenter/stream.ts";
import type { RunnerStreamEvent } from "../../frontend/shared/src/gateway/types.ts";

test("Team stream snapshot shows task, member, and finish progress", () => {
  const event: RunnerStreamEvent = {
    kind: "team_update",
    permission_mode: "default",
    agent_mode: "team",
    runner_id: "run-1",
    actor_name: "root",
    actor_role: "root",
    step_num: null,
    action_step: null,
    round_id: "round-1",
    team_event: {
      team_name: "alpha",
      actor: "root",
      update: { type: "task_completed", task_id: "task-1" },
      snapshot: {
        tasks: [
          {
            task_id: "task-1", title: "Review", description: "", status: "completed",
            eligible_members: ["writer", "reviewer"], claimed_by: "reviewer", dependencies: [],
          },
          {
            task_id: "task-2", title: "Publish", description: "", status: "in_progress",
            eligible_members: ["writer"], claimed_by: "writer", dependencies: ["task-1"], error: "",
          },
        ],
        members: [
          { name: "writer", status: "working" },
          { name: "reviewer", status: "idle" },
        ],
        finished: false,
      },
    },
  };

  assert.deepEqual(presentStreamEvent(event), [{
    kind: "system",
    text: "root: task completed · Review · completed by reviewer · Tasks 1/2 complete · Members writer: working, reviewer: idle",
  }]);
  assert.match(presentStreamEvent({
    ...event,
    team_event: { ...event.team_event, update: { type: "team_finished" }, snapshot: { ...event.team_event?.snapshot, finished: true } },
  })[0]?.text || "", /Team finished/);
});

test("failed claimed task remains visible for root reassignment", () => {
  const event: RunnerStreamEvent = {
    kind: "team_update", permission_mode: "default", agent_mode: "team", runner_id: "run-1",
    actor_name: "root", actor_role: "root", step_num: null, action_step: null, round_id: "round-1",
    team_event: {
      actor: "root", update: { type: "task_failed", task_id: "task-1" },
      snapshot: {
        tasks: [{
          task_id: "task-1", title: "Review", description: "", status: "in_progress",
          eligible_members: ["reviewer"], claimed_by: "reviewer", dependencies: [], error: "timeout",
        }],
      },
    },
  };

  assert.deepEqual(presentStreamEvent(event), [{
    kind: "system",
    text: "root: task failed · Review · in_progress by reviewer · timeout · Tasks 0/1 complete",
  }]);
});
