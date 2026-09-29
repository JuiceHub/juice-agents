import stringWidth from "string-width";

import type { MessageDisplay } from "@juice-agents/shared/presenter/stream";
import { formatDisplayAsText } from "./presenter.js";
import { splitByDisplayWidth } from "./transcriptLayout.js";

const TABLE_GAP = "  ";
const MIN_COLUMN_WIDTH = 3;
const PREFERRED_MIN_COLUMN_WIDTH = 8;
const COMPACT_COLUMN_WIDTH = 12;

/**
 * Format structured display payloads for the terminal width that is actually
 * available inside a transcript row. Shared presenter fallback stays stable
 * for copy/search; this CLI layer owns terminal-specific wrapping.
 */
export function formatDisplayForTerminal(display: MessageDisplay, maxWidth: number): string {
  if (display.type !== "table") {
    return formatDisplayAsText(display);
  }
  return formatTableForTerminal(display.columns, display.rows, maxWidth);
}

export function formatTableForTerminal(
  columns: { key: string; label: string }[],
  rows: Record<string, string>[],
  maxWidth: number,
): string {
  if (rows.length === 0 || columns.length === 0) return "";

  const width = Math.max(1, Math.floor(maxWidth || 80));
  const naturalWidths = columns.map((column) =>
    Math.max(
      displayWidth(column.label),
      ...rows.map((row) => displayWidth(cellText(row[column.key]))),
    ),
  );
  const widths = allocateColumnWidths(columns, naturalWidths, width);
  const lines: string[] = [];

  lines.push(renderTableVisualRow(columns.map((column) => column.label), widths));
  lines.push(
    renderTableVisualRow(
      columns.map((column, index) =>
        "-".repeat(Math.min(widths[index], Math.max(MIN_COLUMN_WIDTH, displayWidth(column.label)))),
      ),
      widths,
    ),
  );

  for (const row of rows) {
    const wrappedCells = columns.map((column, index) =>
      wrapCell(cellText(row[column.key]), widths[index]),
    );
    const rowHeight = Math.max(...wrappedCells.map((cell) => cell.length));
    for (let lineIndex = 0; lineIndex < rowHeight; lineIndex += 1) {
      lines.push(
        renderTableVisualRow(
          wrappedCells.map((cell) => cell[lineIndex] || ""),
          widths,
        ),
      );
    }
  }

  return lines.join("\n");
}

function allocateColumnWidths(
  columns: { label: string }[],
  naturalWidths: number[],
  maxWidth: number,
): number[] {
  const gapWidth = TABLE_GAP.length * Math.max(0, columns.length - 1);
  const available = Math.max(columns.length, maxWidth - gapWidth);
  const labelWidths = columns.map((column) => displayWidth(column.label));
  const minWidths = labelWidths.map((labelWidth, index) => {
    const naturalWidth = naturalWidths[index];
    if (naturalWidth <= COMPACT_COLUMN_WIDTH) {
      return Math.max(MIN_COLUMN_WIDTH, naturalWidth);
    }
    return Math.max(MIN_COLUMN_WIDTH, labelWidth, PREFERRED_MIN_COLUMN_WIDTH);
  });

  if (sum(naturalWidths) <= available) {
    return naturalWidths;
  }

  if (sum(minWidths) > available) {
    return shrinkToFit(minWidths, available);
  }

  const widths = [...minWidths];
  let remaining = available - sum(widths);

  // Allocate extra cells to the columns that lost the most natural width. This
  // keeps short identity columns compact while giving TOOLS/DESCRIPTION room.
  while (remaining > 0) {
    let target = -1;
    let targetNeed = 0;
    for (let index = 0; index < widths.length; index += 1) {
      const need = naturalWidths[index] - widths[index];
      if (need > targetNeed) {
        target = index;
        targetNeed = need;
      }
    }
    if (target === -1) break;
    widths[target] += 1;
    remaining -= 1;
  }

  return widths;
}

function shrinkToFit(widths: number[], available: number): number[] {
  const out = widths.map((width) => Math.max(1, width));
  while (sum(out) > available) {
    let target = 0;
    for (let index = 1; index < out.length; index += 1) {
      if (out[index] > out[target]) target = index;
    }
    if (out[target] <= 1) break;
    out[target] -= 1;
  }
  return out;
}

function renderTableVisualRow(cells: string[], widths: number[]): string {
  return cells
    .map((cell, index) => padEndDisplay(cell, widths[index]))
    .join(TABLE_GAP)
    .trimEnd();
}

function wrapCell(value: string, width: number): string[] {
  const normalized = cellText(value);
  const safeWidth = Math.max(1, width);
  if (!normalized) return [" "];
  if (!/\s/.test(normalized)) {
    return splitByDisplayWidth(normalized, safeWidth);
  }

  const lines: string[] = [];
  let line = "";
  let lineWidth = 0;

  for (const word of normalized.split(" ")) {
    const wordWidth = displayWidth(word);
    if (wordWidth > safeWidth) {
      if (line) {
        lines.push(line);
        line = "";
        lineWidth = 0;
      }
      const chunks = splitByDisplayWidth(word, safeWidth);
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

    if (lineWidth + 1 + wordWidth <= safeWidth) {
      line = `${line} ${word}`;
      lineWidth += 1 + wordWidth;
    } else {
      lines.push(line);
      line = word;
      lineWidth = wordWidth;
    }
  }

  if (line || lines.length === 0) {
    lines.push(line || " ");
  }
  return lines;
}

function padEndDisplay(value: string, width: number): string {
  const current = displayWidth(value);
  if (current >= width) return value;
  return `${value}${" ".repeat(width - current)}`;
}

function cellText(value: unknown): string {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function displayWidth(value: string): number {
  return stringWidth(String(value || ""));
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}
