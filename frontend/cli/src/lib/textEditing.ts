/**
 * Cursor-aware text editing helpers for the CLI composer.
 *
 * Cursor values are UTF-16 offsets because JavaScript strings and Ink text
 * rendering consume that representation. Movement snaps to grapheme-like
 * boundaries so emoji and multi-code-unit characters are not split while
 * editing.
 */

export interface EditResult {
  value: string;
  cursor: number;
}

export interface CursorLineColumn {
  line: number;
  column: number;
}

type SegmentRecord = { index: number; segment: string };

const segmentCache = new Map<string, number[]>();
const MAX_SEGMENT_CACHE_SIZE = 200;

function rememberSegments(value: string, stops: number[]): number[] {
  segmentCache.set(value, stops);
  if (segmentCache.size > MAX_SEGMENT_CACHE_SIZE) {
    const oldest = segmentCache.keys().next().value;
    if (oldest !== undefined) {
      segmentCache.delete(oldest);
    }
  }
  return stops;
}

export function getGraphemeStops(value: string): number[] {
  const cached = segmentCache.get(value);
  if (cached) {
    return cached;
  }

  const stops = new Set<number>([0, value.length]);
  // Prefer Intl.Segmenter when the current Node runtime exposes it; the
  // cast keeps the project on its existing ES2022 TypeScript lib target.
  const Segmenter = (Intl as any).Segmenter;

  if (typeof Segmenter === "function") {
    const segmenter = new Segmenter(undefined, { granularity: "grapheme" });
    for (const part of segmenter.segment(value) as Iterable<SegmentRecord>) {
      stops.add(part.index);
      stops.add(part.index + part.segment.length);
    }
  } else {
    let index = 0;
    for (const char of Array.from(value)) {
      stops.add(index);
      index += char.length;
      stops.add(index);
    }
  }

  return rememberSegments(value, [...stops].sort((a, b) => a - b));
}

export function clampCursor(value: string, cursor: number): number {
  const bounded = Math.max(0, Math.min(cursor, value.length));
  let previous = 0;
  for (const stop of getGraphemeStops(value)) {
    if (stop === bounded) {
      return stop;
    }
    if (stop > bounded) {
      return previous;
    }
    previous = stop;
  }
  return value.length;
}

export function moveCursorLeft(value: string, cursor: number): number {
  const current = clampCursor(value, cursor);
  let previous = 0;
  for (const stop of getGraphemeStops(value)) {
    if (stop >= current) {
      return previous;
    }
    previous = stop;
  }
  return previous;
}

export function moveCursorRight(value: string, cursor: number): number {
  const current = clampCursor(value, cursor);
  for (const stop of getGraphemeStops(value)) {
    if (stop > current) {
      return stop;
    }
  }
  return value.length;
}

export function insertTextAtCursor(
  value: string,
  cursor: number,
  text: string
): EditResult {
  const current = clampCursor(value, cursor);
  return {
    value: value.slice(0, current) + text + value.slice(current),
    cursor: current + text.length,
  };
}

export function deleteBackwardAtCursor(value: string, cursor: number): EditResult {
  const current = clampCursor(value, cursor);
  if (current <= 0) {
    return { value, cursor: current };
  }

  const previous = moveCursorLeft(value, current);
  return {
    value: value.slice(0, previous) + value.slice(current),
    cursor: previous,
  };
}

export function deleteForwardAtCursor(value: string, cursor: number): EditResult {
  const current = clampCursor(value, cursor);
  if (current >= value.length) {
    return { value, cursor: current };
  }

  const next = moveCursorRight(value, current);
  return {
    value: value.slice(0, current) + value.slice(next),
    cursor: current,
  };
}

function lineStartAt(value: string, cursor: number): number {
  return value.lastIndexOf("\n", Math.max(0, cursor - 1)) + 1;
}

function lineEndAt(value: string, cursor: number): number {
  const end = value.indexOf("\n", cursor);
  return end === -1 ? value.length : end;
}

export function getCursorLineColumn(
  value: string,
  cursor: number
): CursorLineColumn {
  const current = clampCursor(value, cursor);
  const linesBefore = value.slice(0, current).split("\n");
  return {
    line: linesBefore.length - 1,
    column: current - lineStartAt(value, current),
  };
}

function snapToLineColumn(value: string, lineStart: number, column: number): number {
  const lineEnd = lineEndAt(value, lineStart);
  return clampCursor(value, Math.min(lineStart + column, lineEnd));
}

export function moveCursorUp(value: string, cursor: number): number | null {
  const current = clampCursor(value, cursor);
  const currentStart = lineStartAt(value, current);
  if (currentStart === 0) {
    return null;
  }

  const previousEnd = currentStart - 1;
  const previousStart = lineStartAt(value, previousEnd);
  return snapToLineColumn(value, previousStart, current - currentStart);
}

export function moveCursorDown(value: string, cursor: number): number | null {
  const current = clampCursor(value, cursor);
  const currentEnd = lineEndAt(value, current);
  if (currentEnd >= value.length) {
    return null;
  }

  const nextStart = currentEnd + 1;
  return snapToLineColumn(value, nextStart, current - lineStartAt(value, current));
}
