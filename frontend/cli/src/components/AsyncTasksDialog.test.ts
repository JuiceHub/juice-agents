import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAsyncTaskRow,
  computeTaskDuration,
  formatDuration,
  sortAsyncTasks,
} from "./AsyncTasksDialog.js";
import type { AsyncTaskState } from "@juice-agents/shared/gateway/types";

function makeTask(overrides: Partial<AsyncTaskState> = {}): AsyncTaskState {
  return {
    async_task_id: "abcdef0123",
    type: "local_bash",
    status: "running",
    owner_actor_id: "root",
    owner_actor_name: "main",
    description: "shell background command: echo hi",
    output_dir: "/tmp/x",
    created_at: 100,
    started_at: 110,
    finished_at: null,
    closed_reason: "",
    error: "",
    metadata: { command: "echo hi", cwd: "/tmp" },
    ...overrides,
  };
}

test("formatDuration scales seconds to s/m/h", () => {
  assert.equal(formatDuration(0), "0s");
  assert.equal(formatDuration(45), "45s");
  assert.equal(formatDuration(64), "1m04s");
  assert.equal(formatDuration(3601), "1h00m");
  assert.equal(formatDuration(-1), "--");
});

test("computeTaskDuration returns -- when not started", () => {
  const task = makeTask({ started_at: null });
  assert.equal(computeTaskDuration(task, 200), "--");
});

test("computeTaskDuration uses finished_at if available", () => {
  const task = makeTask({ started_at: 100, finished_at: 130 });
  assert.equal(computeTaskDuration(task, 999), "30s");
});

test("computeTaskDuration falls back to now when still running", () => {
  const task = makeTask({ started_at: 100, finished_at: null });
  assert.equal(computeTaskDuration(task, 115), "15s");
});

test("buildAsyncTaskRow truncates description and maps kind label", () => {
  const task = makeTask({ description: "x".repeat(80) });
  const row = buildAsyncTaskRow(task, 130);
  assert.equal(row.kind, "bash");
  assert.equal(row.duration, "20s");
  assert.equal(row.id.length <= 8, true);
  assert.ok(row.description.endsWith("…"));
});

test("sortAsyncTasks puts most-recently-started first", () => {
  const sorted = sortAsyncTasks([
    makeTask({ async_task_id: "old", started_at: 100 }),
    makeTask({ async_task_id: "new", started_at: 200 }),
    makeTask({ async_task_id: "pending", started_at: null, created_at: 50 }),
  ]);
  assert.deepEqual(
    sorted.map((t) => t.async_task_id),
    ["new", "old", "pending"]
  );
});
