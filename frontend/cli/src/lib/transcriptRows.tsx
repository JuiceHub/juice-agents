/**
 * transcriptRows —— 把 MessageBlock 摊平成「预 wrap 的视觉行」并提供行渲染组件。
 *
 * 性能优化：使用行级缓存避免重复计算已渲染的消息块
 *
 * 为什么需要这一层
 * ----------------
 * 虚拟滚动要求精确控制渲染的视觉行数（== viewportHeight），否则 React 树高度
 * 超过终端 rows 会触发 Ink 的 clearTerminal 灾难分支（实证）。而原版 Ink 既
 * 无法裁剪「部分行」（overflow:hidden 会间隔取行），也会剥离 <Text> 内嵌的
 * ANSI 色码（无法把预渲染的 ANSI 字符串塞进 Text）。
 *
 * 因此唯一可行的方案是：在 React 层把每个 MessageBlock 摊平成「一个视觉行 =
 * 一个 RowVM」的扁平序列，按终端列宽预先 wrap（保证每个 RowVM 恰好占 1 视觉
 * 行），再用 Ink 原生 <Text color> 逐行渲染。VirtualScrollList 持有这个扁平
 * 序列，按 [scrollTop, scrollTop+viewportHeight) 精确 slice。
 *
 * 视觉对齐
 * --------
 * 着色 / 折叠规则与 MessageBlockView 完全一致（复用 messageAppearance、
 * toolOutputFormatter、splitByDisplayWidth），确保行级渲染与组件级渲染零差异。
 * 这一层取代了旧的 messageRenderer.ts（ANSI 字符串直写）——不再需要绕过 Ink。
 */

import React from "react";
import { Text } from "ink";
import stringWidth from "string-width";
import type { MessageBlock, IntroPanelModel } from "./presenter.js";
import { formatDisplayForTerminal } from "./displayFormatter.js";
import { getMessageAppearance } from "./messageAppearance.js";
import {
  formatToolHeader,
  formatToolOutputPreview,
  pickPrimaryToolParameter,
  MAX_TOOL_PREVIEW_LINES,
} from "./toolOutputFormatter.js";
import { splitByDisplayWidth } from "./transcriptLayout.js";
import { TUI_COPY, TUI_THEME } from "./theme.js";
import {
  getIntroBannerDisplayLines,
  getIntroBannerLineColors,
  INTRO_BANNER_RESERVED_COLUMNS,
  INTRO_BANNER_SECTION_GAP,
} from "../components/IntroPanel.js";
import { getGlobalTranscriptCache } from "./transcriptRowCache.js";

/** App 左右各 1 列 padding（与 <Box paddingX={1}> 对齐）。 */
const PAD_X = 1;
const REGULAR_PREFIX_WIDTH = 2;

/**
 * 单个视觉行的视图模型。每个 RowVM 渲染为恰好 1 个终端行。
 * 行内只允许「前缀 + 正文」两段着色（够覆盖 dot/glyph + body），
 * 避免引入复杂的多段 span 结构。
 */
export interface RowVM {
  /** 稳定 key：`${blockKey}:${rowIndexWithinBlock}`，供 React 列表与 slice 复用。 */
  key: string;
  /** 行首前缀（● / ✻ / ⎿ / ▸ / 续行缩进 / IntroPanel 的 │），可为空。 */
  prefix: string;
  /** 前缀颜色（hex）。 */
  prefixColor: string;
  /** 正文文本（已按列宽切片，不含前缀）。 */
  text: string;
  /** 正文颜色（hex）。 */
  textColor: string;
  /** 是否 dim（thinking / system / 工具折叠输出走 dim）。 */
  dim: boolean;
  /** 是否 bold（IntroPanel banner 行）。 */
  bold?: boolean;
}

/**
 * 把单个 block 摊平为 RowVM[]（带缓存优化）。
 *
 * @param block - 消息块
 * @param columns - 终端列数
 * @param blockKey - 该 block 的稳定 key 前缀（通常是数组下标或 block.id）
 */
export function flattenBlockToRows(
  block: MessageBlock,
  columns: number,
  blockKey: string
): RowVM[] {
  const cache = getGlobalTranscriptCache();

  // 使用缓存：仅在内容或终端宽度变化时重新计算
  return cache.getCachedRows(block, columns, blockKey, () => {
    // 缓存未命中，执行实际计算
    const innerWidth = Math.max(1, (columns || 80) - PAD_X * 2);
    if (block.kind === "tool" && block.toolCall) {
      return flattenToolBlock(block, innerWidth, blockKey);
    }
    return flattenRegularBlock(block, innerWidth, blockKey);
  });
}

/**
 * 摊平常规块（user / assistant / final / thinking / system / error）。
 * 首行首段带 prefix（● / ✻ / ⎿ / ✗），续行缩进 2 列对齐。
 */
function flattenRegularBlock(
  block: MessageBlock,
  width: number,
  blockKey: string
): RowVM[] {
  const a = getMessageAppearance(block);
  const headerSym = a.useDot ? "●" : a.prefix;
  const headerColor = a.useDot ? a.dotColor : a.accentColor;
  const muted = block.kind === "thinking" || block.kind === "system";
  const bodyColor = muted ? TUI_THEME.message.muted : a.bodyColor;
  // The visible message anchor consumes two terminal cells (`● ` / `⎿ `).
  // The formatted display receives this body width up front so table rows do
  // not get hard-wrapped by the terminal after RowVM rendering.
  const wrapWidth = Math.max(1, width - REGULAR_PREFIX_WIDTH);
  const hasStructuredDisplay = Boolean(block.display);
  const body =
    (block.display ? formatDisplayForTerminal(block.display, wrapWidth) : block.text || "")
      .replace(/\n+$/, "") || " ";

  const rows: RowVM[] = [];
  body.split("\n").forEach((rawLine, lineIdx) => {
    if (hasStructuredDisplay) {
      const segs = splitByDisplayWidth(rawLine || " ", wrapWidth);
      segs.forEach((seg, segIdx) => {
        const isHead = lineIdx === 0 && segIdx === 0;
        rows.push({
          key: `${blockKey}:${rows.length}`,
          prefix: isHead ? `${headerSym} ` : "  ",
          prefixColor: isHead ? headerColor : bodyColor,
          text: seg,
          textColor: bodyColor,
          dim: muted,
        });
      });
      return;
    }

    const isFirstInputLine = lineIdx === 0;
    const headPrefix = isFirstInputLine ? `${headerSym} ` : "  ";
    const continuationPrefix = "  ";
    const line = splitLeadingIndent(rawLine || " ");
    const firstPrefix = `${headPrefix}${line.indent}`;
    const restPrefix = `${continuationPrefix}${line.indent}`;
    const lineWrapWidth = Math.max(1, width - displayWidth(restPrefix));
    const segs = wrapRegularTextLine(line.text, lineWrapWidth);
    segs.forEach((seg, segIdx) => {
      const isHead = isFirstInputLine && segIdx === 0;
      rows.push({
        key: `${blockKey}:${rows.length}`,
        prefix: isHead ? firstPrefix : restPrefix,
        prefixColor: isHead ? headerColor : bodyColor,
        text: seg,
        textColor: bodyColor,
        dim: muted,
      });
    });
  });
  return rows;
}

function splitLeadingIndent(rawLine: string): { indent: string; text: string } {
  if (rawLine.trim().length === 0) return { indent: "", text: " " };
  const match = rawLine.match(/^[ \t]*/);
  const indent = match?.[0] || "";
  return { indent, text: rawLine.slice(indent.length) || " " };
}

/**
 * 普通段落优先按词边界换行；没有空白分隔或单词本身超宽时再退回显示宽度切片。
 * 这样英文结果不会被拆成 `evalu`/`ation`，而中文等连续文本仍能稳定限宽。
 */
function wrapRegularTextLine(text: string, maxWidth: number): string[] {
  const width = Math.max(1, maxWidth);
  const normalized = text.replace(/[ \t]+/g, " ").trim();
  if (!normalized) return [" "];
  if (!/\s/.test(normalized)) return splitByDisplayWidth(normalized, width);

  const lines: string[] = [];
  let line = "";
  let lineWidth = 0;

  for (const word of normalized.split(" ")) {
    const wordWidth = displayWidth(word);
    if (wordWidth > width) {
      if (line) {
        lines.push(line);
        line = "";
        lineWidth = 0;
      }
      const chunks = splitByDisplayWidth(word, width);
      lines.push(...chunks.slice(0, -1));
      line = chunks[chunks.length - 1] || "";
      lineWidth = displayWidth(line);
      continue;
    }

    if (!line) {
      line = word;
      lineWidth = wordWidth;
      continue;
    }

    if (lineWidth + 1 + wordWidth <= width) {
      line = `${line} ${word}`;
      lineWidth += 1 + wordWidth;
    } else {
      lines.push(line);
      line = word;
      lineWidth = wordWidth;
    }
  }

  if (line || lines.length === 0) lines.push(line || " ");
  return lines;
}

function displayWidth(value: string): number {
  return stringWidth(String(value || ""));
}

/**
 * 摊平工具块：header 行（▸ Ran tool(arg=val) / ✗ Failed） + ⎿ 折叠输出（前 6 行 + `+K more lines`）。
 */
function flattenToolBlock(
  block: MessageBlock,
  width: number,
  blockKey: string
): RowVM[] {
  if (!block.toolCall) return [];
  const header = formatToolHeader(block.toolCall);
  const primary = pickPrimaryToolParameter(block.toolCall);
  const preview = primary
    ? formatToolOutputPreview(primary.value, MAX_TOOL_PREVIEW_LINES)
    : { lines: [], truncatedCount: 0 };

  const headerColor = header.failed
    ? TUI_THEME.message.errorAccent
    : TUI_THEME.message.responseAccent;
  const isRunning = block.toolCall.status === "running";
  const headerSuffix = isRunning ? " (running)" : "";
  const headerLine = header.inlineParams
    ? `${header.label} ${header.inlineParams}${headerSuffix}`
    : `${header.label}${headerSuffix}`;

  const rows: RowVM[] = [];
  const mutedColor = TUI_THEME.message.muted;

  // header 行：glyph 上色 + 标题。header 不 wrap（已由 inlineParams 截断）。
  rows.push({
    key: `${blockKey}:0`,
    prefix: `${header.glyph} `,
    prefixColor: headerColor,
    text: headerLine,
    textColor: headerColor,
    dim: false,
  });

  // 折叠输出：⎿ + 前 6 行；首行 ⎿ 前缀，续行 4 空格缩进。
  preview.lines.forEach((line, idx) => {
    rows.push({
      key: `${blockKey}:out:${idx}`,
      prefix: idx === 0 ? "  ⎿ " : "    ",
      prefixColor: mutedColor,
      text: line || " ",
      textColor: mutedColor,
      dim: true,
    });
  });
  if (preview.truncatedCount > 0) {
    rows.push({
      key: `${blockKey}:more`,
      prefix: "    ",
      prefixColor: TUI_THEME.surface.muted,
      text: `… +${preview.truncatedCount} more lines`,
      textColor: TUI_THEME.surface.muted,
      dim: true,
    });
  }
  return rows;
}

/**
 * 把整批 blocks 摊平为扁平 RowVM[]，块间插入 1 行 spacer（对齐 <Box marginTop={1}>）。
 * 首块前不加 spacer。
 *
 * @param blocks - 消息块数组
 * @param columns - 终端列数
 * @param keyOf - 可选：自定义每个 block 的 key 前缀（默认用下标）
 */
export function flattenBlocksToRows(
  blocks: MessageBlock[],
  columns: number,
  keyOf?: (block: MessageBlock, index: number) => string
): RowVM[] {
  const out: RowVM[] = [];
  blocks.forEach((block, index) => {
    const blockKey = keyOf ? keyOf(block, index) : `b${index}`;
    if (index > 0) {
      out.push({
        key: `${blockKey}:spacer`,
        prefix: "",
        prefixColor: TUI_THEME.message.body,
        text: "",
        textColor: TUI_THEME.message.body,
        dim: false,
      });
    }
    out.push(...flattenBlockToRows(block, columns, blockKey));
  });
  return out;
}

/**
 * 把 IntroPanel 摊平为 RowVM[]，视觉与 introRenderer/IntroPanel 组件一致：
 *   ` │ <banner>`（渐变色 + bold）
 *   ` │ <meta>`（首行 brand.meta，其余 surface.muted）
 *   ` │ `（marginTop=1 空行）
 *   ` │ <tagline>`
 *   ``（marginBottom=1 底部空行）
 *
 * guide `│` 作为 prefix（brand.guide 色），内容作为 text。取代 introRenderer.ts。
 */
export function flattenIntroToRows(
  panel: IntroPanelModel,
  columns: number,
  keyPrefix = "intro"
): RowVM[] {
  const wordmark = panel.wordmark || "JUICE AGENTS";
  const hasBannerLines = panel.bannerLines.length > 0;
  const bannerLines = hasBannerLines
    ? getIntroBannerDisplayLines({
        lines: panel.bannerLines,
        terminalColumns: columns,
        reservedColumns: INTRO_BANNER_RESERVED_COLUMNS,
        sectionGap: INTRO_BANNER_SECTION_GAP,
      })
    : [wordmark];
  const bannerLineColors = getIntroBannerLineColors({
    lines: bannerLines,
    gradient: TUI_COPY.welcomeBannerGradient,
    fallbackColor: TUI_THEME.brand.wordmark,
    hasBannerLines,
  });

  const guide = TUI_THEME.brand.guide;
  const rows: RowVM[] = [];

  bannerLines.forEach((line, idx) => {
    rows.push({
      key: `${keyPrefix}:banner:${idx}`,
      prefix: "│ ",
      prefixColor: guide,
      text: line || " ",
      textColor: bannerLineColors[idx],
      dim: false,
      bold: true,
    });
  });
  panel.metaLines.forEach((line, idx) => {
    rows.push({
      key: `${keyPrefix}:meta:${idx}`,
      prefix: "│ ",
      prefixColor: guide,
      text: line || " ",
      textColor: idx === 0 ? TUI_THEME.brand.meta : TUI_THEME.surface.muted,
      dim: false,
    });
  });
  // marginTop=1 空行（保留 guide）
  rows.push({
    key: `${keyPrefix}:gap`,
    prefix: "│ ",
    prefixColor: guide,
    text: " ",
    textColor: guide,
    dim: false,
  });
  rows.push({
    key: `${keyPrefix}:tagline`,
    prefix: "│ ",
    prefixColor: guide,
    text: panel.tagline,
    textColor: TUI_THEME.surface.text,
    dim: false,
  });
  // marginBottom=1 底部空行（无 guide）
  rows.push({
    key: `${keyPrefix}:bottom`,
    prefix: "",
    prefixColor: guide,
    text: "",
    textColor: guide,
    dim: false,
  });
  return rows;
}

/**
 * 渲染单个 RowVM 为一个 Ink 行。左 padding 与 <Box paddingX={1}> 对齐。
 * 空行（spacer / 空文本）渲染为单个空格，避免 Ink 折叠掉空 <Text>。
 */
export function TranscriptRow({ row }: { row: RowVM }): React.ReactElement {
  const padding = " ".repeat(PAD_X);
  if (!row.prefix && !row.text) {
    // spacer 行
    return <Text> </Text>;
  }
  return (
    <Text>
      {padding}
      {row.prefix ? (
        <Text color={row.prefixColor} dimColor={row.dim} bold={row.bold}>
          {row.prefix}
        </Text>
      ) : null}
      <Text color={row.textColor} dimColor={row.dim} bold={row.bold}>
        {row.text || " "}
      </Text>
    </Text>
  );
}
