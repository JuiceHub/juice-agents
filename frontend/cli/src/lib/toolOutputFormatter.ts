/**
 * 工具调用生命周期的紧凑渲染。
 *
 * 设计目标:
 * - header 根据统一的生命周期状态显示 Running / Completed / Failed 等标签。
 * - 参数只在标题中显示首个非空值的一行摘要，不展开代码或长文本正文。
 *
 * 状态来自已有执行字段；返回结果单独预览，不从文本推测失败。
 */

import {
  normalizeToolStatus,
  toolStatusLabel,
  type ToolCallBlock,
} from "@juice-agents/shared/presenter/stream";

/** 工具结果预览的最大终端视觉行数；不会限制明确失败和独立错误详情。 */
export const MAX_TOOL_PREVIEW_LINES = 6;

/** 单参数 inline 展示时的最大字符数；过长自动截断尾部。 */
export const MAX_INLINE_PARAM_CHARS = 60;

export interface ToolHeader {
  /** 与生命周期状态对应的前缀符号。 */
  glyph: string;
  /** 主标题，如 `Running <tool>` / `Completed <tool>`。 */
  label: string;
  /** 括号内紧凑的参数展示，可能为空字符串。 */
  inlineParams: string;
  /** 是否为失败状态（用于上层选择红色）。 */
  failed: boolean;
}

export interface ToolPreview {
  /** 折叠后保留展示的内容行（已按行拆分，无尾换行）。 */
  lines: string[];
  /** 被截掉的行数；0 时上层不显示 `+K more lines`。 */
  truncatedCount: number;
}

/**
 * 生成工具调用 header。
 *
 * 规则:
 * - 状态标签与其他展示层共用规范化函数，避免 failed/denied 被当成成功。
 * - inline 参数仅展示首个非空参数（与现有 stream presenter 的 formatParameterSummary 对齐），
 *   且超过 MAX_INLINE_PARAM_CHARS 直接截断为 `…`，避免 header 撑爆终端宽度。
 */
export function formatToolHeader(toolCall: ToolCallBlock): ToolHeader {
  const name = (toolCall.name || "tool").trim() || "tool";
  const status = normalizeToolStatus(toolCall.status);
  const failed = status === "failed" || status === "denied";
  const glyph = failed ? "✗"
    : status === "completed" ? "✓"
    : status === "background" ? "↗"
    : status === "cancelled" ? "⊘"
    : "▸";
  const label = `${toolStatusLabel(status)} ${name}`;

  // 只显示首个非空参数的首行。长文本及后续行用同一个省略号标记，
  // 避免用户把摘要误当成完整参数；原参数仍保留在工具记录中。
  const entries = Object.entries(toolCall.parameters || {})
    .filter(([, value]) => value && value.trim().length > 0);
  if (entries.length === 0) {
    return { glyph, label, inlineParams: "", failed };
  }
  const [firstKey, firstValue] = entries[0];
  const firstLine = firstValue.split(/\r?\n/, 1)[0] ?? firstValue;
  const truncated =
    firstLine.length > MAX_INLINE_PARAM_CHARS || firstLine !== firstValue
      ? `${firstLine.slice(0, Math.max(0, MAX_INLINE_PARAM_CHARS - 1))}…`
      : firstLine;
  return {
    glyph,
    label,
    inlineParams: `(${firstKey}: ${truncated})`,
    failed,
  };
}

/**
 * 把工具返回结果折叠到前 N 行。
 *
 * 入参 `text` 已按终端内容宽度折行；空字符串返回空 preview。
 * 必须先折行再应用限制，避免一条很长的 HTML/字典结果占据多屏。
 * 末尾的空行会被丢弃，避免尾部空白让 `+K more lines` 显示在错误位置。
 */
export function formatToolOutputPreview(
  text: string,
  maxLines: number = MAX_TOOL_PREVIEW_LINES
): ToolPreview {
  const limit = Math.max(1, Math.floor(maxLines));
  if (!text) {
    return { lines: [], truncatedCount: 0 };
  }
  // 去尾部空行避免最后一行是空字符串导致 +K 看起来错位。
  const all = text.replace(/\s+$/g, "").split("\n");
  if (all.length <= limit) {
    return { lines: all, truncatedCount: 0 };
  }
  return {
    lines: all.slice(0, limit),
    truncatedCount: all.length - limit,
  };
}
