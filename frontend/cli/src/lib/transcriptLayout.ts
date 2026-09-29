import stringWidth from "string-width";

import type { MessageBlock } from "./presenter.js";
import { formatDisplayAsText } from "./presenter.js";
import { getMessageAppearance } from "./messageAppearance.js";

export type TranscriptLineStyle = "body" | "muted";

export interface TranscriptContentRow {
  type: "content";
  rowIndex: number;
  messageIndex: number;
  block: MessageBlock;
  prefix: string;
  text: string;
  style: TranscriptLineStyle;
}

export interface TranscriptSpacerRow {
  type: "spacer";
  rowIndex: number;
  messageIndex: number;
}

export type TranscriptLayoutRow =
  | TranscriptContentRow
  | TranscriptSpacerRow;

export interface TranscriptLayout {
  rows: TranscriptLayoutRow[];
  totalRows: number;
  messageCount: number;
}

const graphemeSegmenter = new Intl.Segmenter(undefined, {
  granularity: "grapheme",
});

/**
 * 按显示宽度（含 Unicode 双宽字符）把文本切分成多个视觉行。
 *
 * 在 append-only 模型下，<Static> 内由 Ink 自带 wrap 逻辑处理换行，但 LiveTail
 * 仍需要先估算 block 的视觉行数，以便整体限制动态区高度。
 */
export function splitByDisplayWidth(text: string, maxWidth: number): string[] {
  const width = Math.max(1, maxWidth);
  if (text.length === 0) return [" "];

  const chunks: string[] = [];
  let chunk = "";
  let chunkWidth = 0;

  for (const { segment } of graphemeSegmenter.segment(text)) {
    const segmentWidth = Math.max(0, stringWidth(segment));
    if (chunk && chunkWidth + segmentWidth > width) {
      chunks.push(chunk);
      chunk = "";
      chunkWidth = 0;
    }
    chunk += segment;
    chunkWidth += segmentWidth;
  }

  if (chunk || chunks.length === 0) chunks.push(chunk || " ");
  return chunks;
}

function appendWrappedLine(params: {
  rows: TranscriptLayoutRow[];
  block: MessageBlock;
  messageIndex: number;
  prefix: string;
  text: string;
  width: number;
  style: TranscriptLineStyle;
}): void {
  const prefixWidth = stringWidth(params.prefix);
  const continuationPrefix = " ".repeat(prefixWidth);
  const chunks = splitByDisplayWidth(
    params.text,
    Math.max(1, params.width - prefixWidth)
  );

  chunks.forEach((text, index) => {
    params.rows.push({
      type: "content",
      rowIndex: params.rows.length,
      messageIndex: params.messageIndex,
      block: params.block,
      prefix: index === 0 ? params.prefix : continuationPrefix,
      text,
      style: params.style,
    });
  });
}

function blockBody(block: MessageBlock): string {
  if (!block.display) return block.text;
  return formatDisplayAsText(block.display) || block.text;
}

/**
 * 把 message blocks 拆分为带前缀的视觉行序列，主要给 LiveTail 估算总行数用。
 *
 * 该函数曾经也是 VirtualTranscript 的渲染源；append-only 重构后渲染走
 * MessageBlockView，layoutTranscriptMessages 只保留行数估算职责。
 */
export function layoutTranscriptMessages(
  messages: MessageBlock[],
  terminalColumns: number | undefined
): TranscriptLayout {
  // App has one column of horizontal padding on each side.
  const width = Math.max(1, (terminalColumns || 80) - 2);
  const rows: TranscriptLayoutRow[] = [];

  messages.forEach((block, messageIndex) => {
    rows.push({ type: "spacer", rowIndex: rows.length, messageIndex });
    const appearance = getMessageAppearance(block);

    if (block.kind === "tool" && block.toolCall) {
      const title = `${block.title || block.text}${
        block.toolCall.status === "error" ? " · failed" : ""
      }`;
      appendWrappedLine({
        rows,
        block,
        messageIndex,
        prefix: `${appearance.prefix} `,
        text: title,
        width,
        style: "body",
      });
      for (const [key, value] of Object.entries(
        block.toolCall.parameters || {}
      )) {
        appendWrappedLine({
          rows,
          block,
          messageIndex,
          prefix: "  ",
          text: `${key}: ${value || "(empty)"}`,
          width,
          style: "muted",
        });
      }
      return;
    }

    const lines = blockBody(block).split("\n");
    lines.forEach((line, lineIndex) => {
      const prefix =
        lineIndex === 0 && appearance.prefix
          ? `${appearance.prefix} `
          : appearance.prefix
            ? "  "
            : "";
      appendWrappedLine({
        rows,
        block,
        messageIndex,
        prefix,
        text: line || " ",
        width,
        style: "body",
      });
    });
  });

  return {
    rows,
    totalRows: rows.length,
    messageCount: messages.length,
  };
}
