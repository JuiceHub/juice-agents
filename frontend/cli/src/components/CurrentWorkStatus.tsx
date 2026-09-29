import React, { useEffect, useRef, useState } from "react";
import { Box, Text } from "ink";

import { LoadingState } from "./design-system/index.js";
import { TUI_COPY, TUI_THEME } from "../lib/theme.js";

export interface CurrentWorkStatusItem {
  label: string;
  value?: string;
}

export interface CurrentWorkStatusModel {
  visible: boolean;
  summary: string;
  items: CurrentWorkStatusItem[];
}

export interface LifecycleStatusEvent {
  event?: string;
  node?: string;
  completed?: number;
  total?: number;
  detail?: string;
}

const NODE_SUMMARIES: Record<string, string> = {
  preflight: "Preparing research...",
  write_research_brief: "Writing research brief...",
  plan_research_topics: "Planning research...",
  dispatch_research: "Starting researchers...",
  research_topic: "Researching sources...",
  compress_and_deduplicate: "Organizing evidence...",
  verify_and_vote_claims: "Verifying claims...",
  assess_gaps: "Assessing evidence gaps...",
  write_final_report: "Writing final report...",
  verify_citations: "Verifying citations...",
  persist_artifacts: "Saving research artifacts...",
};

// 等待时间提示阈值。借鉴 claude-code Spinner 的 30s/1800s 设计，但缩短上限到 5min:
// 用户在 5 分钟无响应时通常已经在考虑是否要中断，及时给一个 /clear 提示更有用。
const TIP_AFTER_MS = 30_000; // 30 秒后开始提示中断方式
const LONG_TIP_AFTER_MS = 5 * 60_000; // 5 分钟后切换为长任务提示
const ELAPSED_POLL_MS = 5_000; // 5 秒采样足够触发阈值切换，不会过度刷新

export function lifecycleStatusModel(
  event: LifecycleStatusEvent | null | undefined
): Pick<CurrentWorkStatusModel, "summary" | "items"> {
  if (!event) return { summary: "", items: [] };
  const node = String(event.node || "");
  let summary = NODE_SUMMARIES[node] || String(event.detail || "Working...");
  const completed = Number(event.completed);
  const total = Number(event.total);
  const items: CurrentWorkStatusItem[] = [];
  if (node === "research_topic" && Number.isFinite(total) && total > 0) {
    const boundedCompleted = Number.isFinite(completed)
      ? Math.max(0, Math.min(completed, total))
      : 0;
    summary = `Researching sources (${boundedCompleted}/${total})...`;
  }
  if (node && event.detail && !NODE_SUMMARIES[node]) {
    items.push({ label: "Stage", value: String(event.detail) });
  }
  return { summary, items };
}

export function buildCurrentWorkStatusModel(params: {
  streaming?: boolean;
  interactive?: boolean;
  summary?: string;
  items?: CurrentWorkStatusItem[];
  lifecycleEvent?: LifecycleStatusEvent | null;
}): CurrentWorkStatusModel {
  const lifecycle = lifecycleStatusModel(params.lifecycleEvent);
  const items = params.items || lifecycle.items;
  const isInternalModelWork = Boolean(params.streaming && !params.interactive);
  const summary =
    params.summary ||
    lifecycle.summary ||
    (isInternalModelWork ? TUI_COPY.currentWorkStatus : "");
  return {
    visible: Boolean(isInternalModelWork || summary || items.length > 0),
    summary,
    items,
  };
}

/** 根据已等待毫秒数选择 tip 文案；< 30s 时返回 null，避免在交互过快时分散注意。 */
export function pickElapsedTip(elapsedMs: number): string | null {
  if (elapsedMs >= LONG_TIP_AFTER_MS) {
    return "Use /clear to start fresh and free up context";
  }
  if (elapsedMs >= TIP_AFTER_MS) {
    return "Esc to interrupt, Ctrl+C to exit";
  }
  return null;
}

export function CurrentWorkStatus(props: {
  streaming?: boolean;
  interactive?: boolean;
  summary?: string;
  items?: CurrentWorkStatusItem[];
  lifecycleEvent?: LifecycleStatusEvent | null;
}) {
  const model = buildCurrentWorkStatusModel(props);

  // 跟踪当前一段 streaming 的起点，用于显示 elapsed-based tip。streaming 切回 false
  // 时清空，下一次 streaming 重新开始计时；避免跨段误用旧的开始时间。
  const startRef = useRef<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    if (!model.visible) {
      startRef.current = null;
      setElapsedMs(0);
      return;
    }
    if (startRef.current == null) {
      startRef.current = Date.now();
      setElapsedMs(0);
    }
    const timer = setInterval(() => {
      if (startRef.current != null) {
        setElapsedMs(Date.now() - startRef.current);
      }
    }, ELAPSED_POLL_MS);
    return () => clearInterval(timer);
  }, [model.visible]);

  if (!model.visible) {
    return null;
  }

  const tip = pickElapsedTip(elapsedMs);
  // 把 items[0] 作为 LoadingState 的 subtitle 展示；其余 items 跟在后面继续保持
  // 现有视觉（label/value 并排），避免破坏研究模式下的多 stage 反馈。
  const [primaryItem, ...restItems] = model.items;

  return (
    <Box flexDirection="column" marginTop={1}>
      {model.summary ? (
        <LoadingState
          message={model.summary}
          subtitle={primaryItem ? formatItem(primaryItem) : undefined}
          bold
        />
      ) : null}
      {restItems.map((item) => (
        <Text
          color={TUI_THEME.surface.muted}
          dimColor
          key={`${item.label}-${item.value || ""}`}
        >
          {"  "}
          {formatItem(item)}
        </Text>
      ))}
      {tip ? (
        <Text color={TUI_THEME.surface.muted} dimColor>
          {"  "}
          Tip: {tip}
        </Text>
      ) : null}
    </Box>
  );
}

function formatItem(item: CurrentWorkStatusItem): string {
  return item.value ? `${item.label}: ${item.value}` : item.label;
}
