/**
 * 工具调用块的 Codex 风格紧凑渲染。
 *
 * 设计目标:
 * - header 单行: `▸ Ran <tool>(arg=val)` 或失败时 `✗ Failed <tool>`
 * - 参数体: 多值参数（如 shell 的 command、python 的 code、edit 的 new_string）
 *   可能含多行字符串，按 MAX_TOOL_PREVIEW_LINES 折叠为前 N 行 + `… +K more lines`
 *
 * 这两个职责是纯函数，被 MessageBlockView（Static 与 LiveTail 共用）以及
 * 单元测试消费，避免在组件中散落字符串拼接逻辑。
 */

import type { ToolCallBlock } from "@juice-agents/shared/presenter/stream";

/** 工具调用输出在最终态展示的最大行数；超出折叠为灰色 `+K more lines`。 */
export const MAX_TOOL_PREVIEW_LINES = 6;

/** 单参数 inline 展示时的最大字符数；过长自动截断尾部。 */
export const MAX_INLINE_PARAM_CHARS = 60;

export interface ToolHeader {
  /** ▸ 或 ✗ 前缀符号。 */
  glyph: string;
  /** 主标题 `Ran <tool>` / `Failed <tool>`。 */
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
 * - 失败: `✗ Failed <tool>(arg=val)`
 * - 其他: `▸ Ran <tool>(arg=val)`
 * - inline 参数仅展示首个非空参数（与现有 stream presenter 的 formatParameterSummary 对齐），
 *   且超过 MAX_INLINE_PARAM_CHARS 直接截断为 `…`，避免 header 撑爆终端宽度。
 */
export function formatToolHeader(toolCall: ToolCallBlock): ToolHeader {
  const name = (toolCall.name || "tool").trim() || "tool";
  const failed = toolCall.status === "error";
  const glyph = failed ? "✗" : "▸";
  const label = `${failed ? "Failed" : "Ran"} ${name}`;

  // 取首个非空参数做 inline 展示，第一行 + 截断。
  const entries = Object.entries(toolCall.parameters || {})
    .filter(([, value]) => value && value.trim().length > 0);
  if (entries.length === 0) {
    return { glyph, label, inlineParams: "", failed };
  }
  const [firstKey, firstValue] = entries[0];
  const firstLine = firstValue.split("\n")[0] ?? firstValue;
  const truncated =
    firstLine.length > MAX_INLINE_PARAM_CHARS
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
 * 把多值参数（典型如 shell.command / python.code / edit.new_string）折叠到前 N 行。
 *
 * 入参 `text` 是已经 join 好的多行字符串；空字符串返回空 preview，调用方据此跳过 ⎿ 块。
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

/**
 * 选取需要折叠展示的"主参数"。
 *
 * 工具调用展示的主体是首个值含换行或较长的参数（command/code/new_string 等）。
 * 如果没有这种参数，返回 null，上层只显示 inline header，不渲染 ⎿ 折叠块。
 */
export function pickPrimaryToolParameter(
  toolCall: ToolCallBlock
): { key: string; value: string } | null {
  const entries = Object.entries(toolCall.parameters || {});
  for (const [key, value] of entries) {
    if (!value) continue;
    if (value.includes("\n") || value.length > MAX_INLINE_PARAM_CHARS) {
      return { key, value };
    }
  }
  return null;
}
