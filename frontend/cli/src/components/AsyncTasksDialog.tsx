/**
 * AsyncTasksDialog: 列出当前 active 异步后台任务，并支持 Enter 进入详情。
 *
 * 两层视图：
 *   list   —— 紧凑单行展示每个任务（kind / id / status / duration / owner / desc）
 *   detail —— 展示状态头、metadata 完整 dump、stdout/stderr 末尾、错误/关闭原因
 *
 * 不打断 composer 输入态？打开此面板时由 app.tsx 把 composer 标记为 inactive，
 * Esc 关闭面板后再恢复输入。
 */

import React from "react";
import { Box, Text, useInput } from "ink";
import type { AsyncTaskState, AsyncTaskOutput } from "@juice-agents/shared/gateway/types";
import { TUI_THEME } from "../lib/theme.js";
import { pickWindowAroundSelected } from "../lib/overlayWindowing.js";

const KIND_DISPLAY: Record<string, string> = {
  local_bash: "bash",
  local_agent: "agent",
  local_graph: "graph",
  teammate: "team",
  memory_dream: "dream",
};

const STATUS_COLOR: Record<string, string> = {
  pending: TUI_THEME.brand.meta,
  running: TUI_THEME.state.success,
  completed: TUI_THEME.surface.muted,
  failed: TUI_THEME.state.danger,
  killed: TUI_THEME.state.danger,
};

/** 按起始时间倒序：最近开始的最上面，pending（无 started_at）排末尾。 */
export function sortAsyncTasks(tasks: AsyncTaskState[]): AsyncTaskState[] {
  return [...tasks].sort((a, b) => {
    const aStarted = typeof a.started_at === "number" ? a.started_at : 0;
    const bStarted = typeof b.started_at === "number" ? b.started_at : 0;
    if (aStarted !== bStarted) {
      return bStarted - aStarted;
    }
    return (b.created_at || 0) - (a.created_at || 0);
  });
}

/** 把秒数转成 "12s" / "1m04s" 这样的紧凑文本。 */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "--";
  }
  const total = Math.floor(seconds);
  if (total < 60) {
    return `${total}s`;
  }
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m < 60) {
    return `${m}m${s.toString().padStart(2, "0")}s`;
  }
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h}h${mm.toString().padStart(2, "0")}m`;
}

export function computeTaskDuration(task: AsyncTaskState, now: number): string {
  const finished = typeof task.finished_at === "number" ? task.finished_at : null;
  const started = typeof task.started_at === "number" ? task.started_at : null;
  if (started === null) {
    return "--";
  }
  const end = finished ?? now;
  return formatDuration(Math.max(0, end - started));
}

function shortId(id: string): string {
  const trimmed = (id || "").trim();
  return trimmed.length > 8 ? trimmed.slice(-8) : trimmed;
}

function truncate(text: string, max: number): string {
  if (text.length <= max) {
    return text;
  }
  return text.slice(0, Math.max(0, max - 1)) + "…";
}

export interface AsyncTaskRowModel {
  kind: string;
  id: string;
  status: string;
  duration: string;
  owner: string;
  description: string;
}

export function buildAsyncTaskRow(task: AsyncTaskState, now: number): AsyncTaskRowModel {
  const kindRaw = (task.type || "").trim();
  return {
    kind: KIND_DISPLAY[kindRaw] || kindRaw || "task",
    id: shortId(task.async_task_id),
    status: (task.status || "").trim() || "unknown",
    duration: computeTaskDuration(task, now),
    owner: truncate((task.owner_actor_name || "").trim() || "-", 12),
    description: truncate((task.description || "").trim(), 60),
  };
}

interface AsyncTasksDialogProps {
  tasks: AsyncTaskState[];
  isActive: boolean;
  selectedIndex: number;
  mode: "list" | "detail";
  detailOutput: AsyncTaskOutput | null;
  detailLoading: boolean;
  detailError: string | null;
  /** detail 模式当前选中的任务对象；外层用 selectedIndex 解析后传进来。 */
  detailTask: AsyncTaskState | null;
  now: number;
  onUp: () => void;
  onDown: () => void;
  onEnter: () => void;
  onCancel: () => void;
  maxRows?: number;
}

export function AsyncTasksDialog(props: AsyncTasksDialogProps) {
  useInput(
    (input, key) => {
      if (key.upArrow || input === "k") {
        props.onUp();
      } else if (key.downArrow || input === "j") {
        props.onDown();
      } else if (key.return) {
        props.onEnter();
      } else if (key.escape) {
        props.onCancel();
      }
    },
    { isActive: props.isActive }
  );

  if (props.mode === "detail" && props.detailTask) {
    return renderDetail(props);
  }
  return renderList(props);
}

function renderList(props: AsyncTasksDialogProps) {
  const sorted = sortAsyncTasks(props.tasks);
  const safeIndex = Math.max(0, Math.min(props.selectedIndex, sorted.length - 1));

  // windowing for list mode
  const LIST_CHROME_ROWS = 5; // marginTop + title + marginBottom + help + marginBottom
  const emptyRows = sorted.length === 0 ? 1 : 0;
  const itemBudget = Math.max(3, (props.maxRows ?? 999) - LIST_CHROME_ROWS - emptyRows);
  const window = sorted.length > 0 ? pickWindowAroundSelected({
    items: sorted,
    rowsPerItem: () => 1, // 每个 task 一行
    selectedIndex: safeIndex,
    budgetRows: itemBudget,
  }) : { startIndex: 0, endIndex: -1, topHidden: 0, bottomHidden: 0 };
  const visibleTasks = sorted.slice(window.startIndex, window.endIndex + 1);

  return (
    <Box flexDirection="column" marginTop={1} marginBottom={1}>
      <Box marginBottom={1}>
        <Text color={TUI_THEME.brand.meta}>
          Async tasks ({sorted.length} active)
        </Text>
      </Box>

      {sorted.length === 0 ? (
        <Text color={TUI_THEME.surface.muted} dimColor>
          No active async tasks.
        </Text>
      ) : (
        <>
          {window.topHidden > 0 && (
            <Text color={TUI_THEME.surface.muted} dimColor>
              {`… ↑ ${window.topHidden} more`}
            </Text>
          )}

          {visibleTasks.map((task, vi) => {
            const index = window.startIndex + vi;
            const isSelected = index === safeIndex;
            const row = buildAsyncTaskRow(task, props.now);
            const statusColor = STATUS_COLOR[row.status] || TUI_THEME.surface.text;
            return (
              <Box key={task.async_task_id || `${index}`}>
                <Text color={TUI_THEME.surface.subtle}>{isSelected ? "›" : " "}</Text>
                <Text> </Text>
                <Text color={isSelected ? TUI_THEME.brand.wordmark : TUI_THEME.surface.text}>
                  ● {row.kind.padEnd(6)} {row.id.padEnd(9)}
                </Text>
                <Text color={statusColor}>
                  {row.status.padEnd(10)}
                </Text>
                <Text color={TUI_THEME.surface.muted}>
                  {row.duration.padEnd(8)}
                </Text>
                <Text color={TUI_THEME.surface.muted}>
                  {row.owner.padEnd(14)}
                </Text>
                <Text color={isSelected ? TUI_THEME.brand.wordmark : TUI_THEME.surface.muted}>
                  {row.description}
                </Text>
              </Box>
            );
          })}

          {window.bottomHidden > 0 && (
            <Text color={TUI_THEME.surface.muted} dimColor>
              {`… ↓ ${window.bottomHidden} more`}
            </Text>
          )}
        </>
      )}

      <Box marginTop={1}>
        <Text color={TUI_THEME.surface.muted} dimColor>
          ↑↓ 选择 · Enter 详情 · Esc 关闭
        </Text>
      </Box>
    </Box>
  );
}

function renderDetail(props: AsyncTasksDialogProps) {
  const task = props.detailTask!;
  const row = buildAsyncTaskRow(task, props.now);
  const statusColor = STATUS_COLOR[row.status] || TUI_THEME.surface.text;
  // metadata 完整 dump，按 key 排序保持稳定显示。
  const metadataEntries = Object.entries(task.metadata || {}).sort(([a], [b]) =>
    a.localeCompare(b)
  );
  const showError = ["failed", "killed"].includes(row.status) || Boolean(task.error);
  return (
    <Box flexDirection="column" marginTop={1} marginBottom={1}>
      <Box marginBottom={1}>
        <Text color={TUI_THEME.brand.meta}>
          {task.async_task_id} · {task.type} · {" "}
        </Text>
        <Text color={statusColor}>{row.status}</Text>
        <Text color={TUI_THEME.brand.meta}>
          {" "}· {row.duration} · owner={row.owner}
        </Text>
      </Box>

      <Text color={TUI_THEME.surface.muted}>— status —</Text>
      <Text color={TUI_THEME.surface.text}>
        kind={task.type}  status={row.status}  duration={row.duration}
      </Text>
      <Text color={TUI_THEME.surface.muted}>
        created={formatTimestamp(task.created_at)}
        {task.started_at ? `  started=${formatTimestamp(task.started_at)}` : ""}
        {task.finished_at ? `  finished=${formatTimestamp(task.finished_at)}` : ""}
      </Text>

      <Box marginTop={1} flexDirection="column">
        <Text color={TUI_THEME.surface.muted}>— metadata —</Text>
        {metadataEntries.length === 0 ? (
          <Text color={TUI_THEME.surface.muted} dimColor>
            (empty)
          </Text>
        ) : (
          metadataEntries.map(([key, value]) => (
            <Text key={key} color={TUI_THEME.surface.text}>
              {key.padEnd(16)}: {formatMetaValue(value)}
            </Text>
          ))
        )}
      </Box>

      <Box marginTop={1} flexDirection="column">
        <Text color={TUI_THEME.surface.muted}>— output —</Text>
        <Text color={TUI_THEME.surface.muted} dimColor>
          output_dir: {task.output_dir || "(unknown)"}
        </Text>
        {props.detailLoading ? (
          <Text color={TUI_THEME.surface.muted}>loading tail…</Text>
        ) : props.detailError ? (
          <Text color={TUI_THEME.state.danger}>error: {props.detailError}</Text>
        ) : props.detailOutput ? (
          <Box flexDirection="column">
            <Text color={TUI_THEME.surface.muted}>
              [stdout tail {props.detailOutput.max_lines}]
            </Text>
            {props.detailOutput.stdout_tail.trim() ? (
              <Text color={TUI_THEME.surface.text}>
                {props.detailOutput.stdout_tail}
              </Text>
            ) : (
              <Text color={TUI_THEME.surface.muted} dimColor>(empty)</Text>
            )}
            <Text color={TUI_THEME.surface.muted}>
              [stderr tail {props.detailOutput.max_lines}]
            </Text>
            {props.detailOutput.stderr_tail.trim() ? (
              <Text color={TUI_THEME.state.danger}>
                {props.detailOutput.stderr_tail}
              </Text>
            ) : (
              <Text color={TUI_THEME.surface.muted} dimColor>(empty)</Text>
            )}
          </Box>
        ) : (
          <Text color={TUI_THEME.surface.muted} dimColor>
            (no log files for this task kind)
          </Text>
        )}
      </Box>

      {showError ? (
        <Box marginTop={1} flexDirection="column">
          <Text color={TUI_THEME.surface.muted}>— error / closed_reason —</Text>
          {task.error ? (
            <Text color={TUI_THEME.state.danger}>error: {task.error}</Text>
          ) : null}
          {task.closed_reason ? (
            <Text color={TUI_THEME.state.danger}>closed_reason: {task.closed_reason}</Text>
          ) : null}
        </Box>
      ) : null}

      <Box marginTop={1}>
        <Text color={TUI_THEME.surface.muted} dimColor>
          Esc 返回列表
        </Text>
      </Box>
    </Box>
  );
}

function formatMetaValue(value: any): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatTimestamp(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return "-";
  }
  // 后端是 epoch 秒；转 ISO 字符串便于阅读。
  return new Date(value * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}
