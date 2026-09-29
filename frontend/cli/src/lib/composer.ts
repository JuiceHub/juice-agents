/**
 * Pure helpers for the Rich Composer.
 *
 * Text editing itself lives in `textEditing.ts`; this file owns submission
 * normalization and history traversal state.
 */

export interface ComposerController {
  history: string[];
  historyCursors: number[];
  historyIndex: number;
  draftBeforeHistory: string;
  draftCursorBeforeHistory: number;
  value: string;
  cursor: number;
}

export type ReturnAction = "submit" | "newline";

/**
 * Resolve the composer action for a Return keypress.
 *
 * Ink 5.0.x can incorrectly mark a plain carriage return as `shift=true`.
 * It also reports a combined Alt+Enter escape sequence as a bare `"\r"` input
 * without `key.return`. The user-facing contract only reserves Alt/Meta+Enter
 * for a newline, so deliberately ignore `shift` and `ctrl`, while recognizing
 * that stripped escape-sequence shape. This keeps plain Enter—and in particular
 * slash commands such as `/help`—on the submit path independently of Ink.
 */
export function resolveReturnAction(input: string, key: {
  return?: boolean;
  meta?: boolean;
  ctrl?: boolean;
  shift?: boolean;
}): ReturnAction {
  const strippedAltReturn = !key.return && input === "\r";
  return key.meta || strippedAltReturn ? "newline" : "submit";
}

export function createComposerController(
  history: string[] = [],
  value = "",
  cursor = value.length,
  historyCursors: number[] = history.map((item) => item.length)
): ComposerController {
  return {
    history,
    historyCursors,
    historyIndex: -1,
    draftBeforeHistory: value,
    draftCursorBeforeHistory: cursor,
    value,
    cursor,
  };
}

export function pushHistory(history: string[], submitted: string): string[] {
  const normalized = submitDraft(submitted);
  if (!normalized) {
    return history;
  }

  if (history[history.length - 1] === normalized) {
    return history;
  }

  return [...history, normalized];
}

export function pushHistoryCursor(
  history: string[],
  historyCursors: number[],
  submitted: string
): number[] {
  const normalized = submitDraft(submitted);
  if (!normalized) {
    return historyCursors;
  }

  if (history[history.length - 1] === normalized) {
    return historyCursors;
  }

  return [...historyCursors, normalized.length];
}

export function submitDraft(value: string): string | null {
  const trimmed = value.replace(/\s+$/u, "");
  if (!trimmed.trim()) {
    return null;
  }

  return trimmed;
}

export function syncEditedDraft(
  state: ComposerController,
  nextValue: string,
  nextCursor = nextValue.length
): ComposerController {
  return {
    ...state,
    historyIndex: -1,
    draftBeforeHistory: nextValue,
    draftCursorBeforeHistory: nextCursor,
    value: nextValue,
    cursor: nextCursor,
  };
}

export function cycleHistoryUp(
  state: ComposerController,
  currentDraft = state.value,
  currentCursor = state.cursor
): ComposerController {
  if (state.history.length === 0) {
    return { ...state, value: currentDraft, cursor: currentCursor };
  }

  if (state.historyIndex === -1) {
    const nextIndex = state.history.length - 1;
    return {
      ...state,
      historyIndex: nextIndex,
      draftBeforeHistory: currentDraft,
      draftCursorBeforeHistory: currentCursor,
      value: state.history[nextIndex],
      cursor: state.historyCursors[nextIndex] ?? state.history[nextIndex].length,
    };
  }

  const nextIndex = Math.max(0, state.historyIndex - 1);
  return {
    ...state,
    historyIndex: nextIndex,
    value: state.history[nextIndex],
    cursor: state.historyCursors[nextIndex] ?? state.history[nextIndex].length,
  };
}

export function cycleHistoryDown(state: ComposerController): ComposerController {
  if (state.historyIndex === -1) {
    return state;
  }

  const nextIndex = state.historyIndex + 1;
  if (nextIndex >= state.history.length) {
    return {
      ...state,
      historyIndex: -1,
      value: state.draftBeforeHistory,
      cursor: state.draftCursorBeforeHistory,
    };
  }

  return {
    ...state,
    historyIndex: nextIndex,
    value: state.history[nextIndex],
    cursor: state.historyCursors[nextIndex] ?? state.history[nextIndex].length,
  };
}
