/**
 * Multiline composer with history navigation and completion-aware keybindings.
 */

import React, { useCallback, useEffect, useRef } from "react";
import { Box, Text, useInput } from "ink";
import {
  type ComposerController,
  createComposerController,
  cycleHistoryDown,
  cycleHistoryUp,
  pushHistory,
  pushHistoryCursor,
  resolveReturnAction,
  submitDraft,
  syncEditedDraft,
} from "../lib/composer.js";
import {
  deleteBackwardAtCursor,
  insertTextAtCursor,
  moveCursorDown,
  moveCursorLeft,
  moveCursorRight,
  moveCursorUp,
} from "../lib/textEditing.js";
import { TUI_COPY, TUI_THEME } from "../lib/theme.js";
import { getInputFrameModel } from "./InputBox.js";
import { TextInput } from "./TextInput.js";

export interface RichComposerProps {
  value: string;
  onChange: (value: string) => void;
  cursor: number;
  onCursorChange: (cursor: number) => void;
  controller: ComposerController;
  onControllerChange: (controller: ComposerController) => void;
  onSubmit: (text: string) => void;
  onTab: (cursor: number) => { input: string; cursor: number } | null;
  onCycleMode: () => void;
  onEscape: () => void;
  onExit: () => void;
  onToggleActors: () => void;
  onToggleTasks: () => void;
  onCandidateUp: () => void;
  onCandidateDown: () => void;
  disabled: boolean;
  candidatesVisible: boolean;
  isActive: boolean;
  ghostText?: string | null;
  inlineHint?: string | null;
}

export interface RichComposerModel {
  lines: string[];
  placeholder: string;
  lineCount: number;
  showCursor: boolean;
}

export function getRichComposerModel(params: {
  value: string;
  disabled: boolean;
}): RichComposerModel {
  const lines = params.value.length > 0 ? params.value.split("\n") : [];
  return {
    lines,
    placeholder: TUI_COPY.placeholder,
    lineCount: Math.max(1, lines.length),
    showCursor: !params.disabled,
  };
}

export function isActorsHotkey(input: string, key: { ctrl?: boolean }): boolean {
  // Ctrl+S: 打开 actor-selector（即旧 /tasks 行为，迁到 /actors）
  return input === "\x13" || (input.toLowerCase() === "s" && Boolean(key.ctrl));
}

export function isTasksHotkey(input: string, key: { ctrl?: boolean }): boolean {
  // Ctrl+T: 打开新的 async tasks 面板（/tasks）
  return input === "\x14" || (input.toLowerCase() === "t" && Boolean(key.ctrl));
}

export function isModeCycleKey(
  input: string,
  key: { tab?: boolean; shift?: boolean }
): boolean {
  return (Boolean(key.tab) && Boolean(key.shift)) || input === "\x1b[Z";
}

export function resolveDeleteEdit(
  value: string,
  cursor: number,
  key: { backspace?: boolean; delete?: boolean }
) {
  if (key.backspace || key.delete) {
    return deleteBackwardAtCursor(value, cursor);
  }

  return null;
}

export function RichComposer({
  value,
  onChange,
  cursor,
  onCursorChange,
  controller,
  onControllerChange,
  onSubmit,
  onTab,
  onCycleMode,
  onEscape,
  onExit,
  onToggleActors,
  onToggleTasks,
  onCandidateUp,
  onCandidateDown,
  disabled,
  candidatesVisible,
  isActive,
  ghostText,
  inlineHint,
}: RichComposerProps) {
  const controllerRef = useRef(controller);

  useEffect(() => {
    controllerRef.current = controller;
  }, [controller]);

  const commitController = useCallback((next: ComposerController) => {
    // Ink can deliver repeated keypresses faster than React commits a render.
    // Keep an immediate ref mirror so history traversal sees the latest index
    // for sequences like Up, Up or Down, Down within the same input burst.
    controllerRef.current = next;
    onControllerChange(next);
  }, [onControllerChange]);

  const applyFreeformChange = useCallback(
    (nextValue: string, nextCursor: number) => {
      commitController(
        syncEditedDraft(controllerRef.current, nextValue, nextCursor)
      );
      onChange(nextValue);
      onCursorChange(nextCursor);
    },
    [commitController, onChange, onCursorChange]
  );

  useInput(
    (ch: string, key: any) => {
      // Ctrl+C: clear input if non-empty, otherwise exit
      if (ch === "c" && key.ctrl) {
        if (value.length > 0) {
          applyFreeformChange("", 0);
        } else {
          onExit();
        }
        return;
      }

      if (isActorsHotkey(ch, key)) {
        onToggleActors();
        return;
      }

      if (isTasksHotkey(ch, key)) {
        onToggleTasks();
        return;
      }

      if (disabled) {
        return;
      }

      if (isModeCycleKey(ch, key)) {
        onCycleMode();
        return;
      }

      if (key.tab) {
        const accepted = onTab(cursor);
        if (accepted) {
          applyFreeformChange(accepted.input, accepted.cursor);
        }
        return;
      }

      if (key.escape) {
        onEscape();
        return;
      }

      if (key.upArrow) {
        if (candidatesVisible) {
          onCandidateUp();
          return;
        }

        if (controllerRef.current.historyIndex !== -1) {
          const next = cycleHistoryUp(controllerRef.current, value, cursor);
          commitController(next);
          onChange(next.value);
          onCursorChange(next.cursor);
          return;
        }

        const nextCursor = moveCursorUp(value, cursor);
        if (nextCursor !== null) {
          onCursorChange(nextCursor);
          return;
        }

        const next = cycleHistoryUp(controllerRef.current, value, cursor);
        commitController(next);
        onChange(next.value);
        onCursorChange(next.cursor);
        return;
      }

      if (key.downArrow) {
        if (candidatesVisible) {
          onCandidateDown();
          return;
        }

        if (controllerRef.current.historyIndex !== -1) {
          const next = cycleHistoryDown(controllerRef.current);
          commitController(next);
          onChange(next.value);
          onCursorChange(next.cursor);
          return;
        }

        const nextCursor = moveCursorDown(value, cursor);
        if (nextCursor !== null) {
          onCursorChange(nextCursor);
          return;
        }

        const next = cycleHistoryDown(controllerRef.current);
        commitController(next);
        onChange(next.value);
        onCursorChange(next.cursor);
        return;
      }

      // Ink exposes ordinary CR as `key.return`, but some terminals send LF;
      // a combined Alt+Enter arrives as a stripped bare CR without that flag.
      if (key.return || ch === "\r" || ch === "\n") {
        if (resolveReturnAction(ch, key) === "newline") {
          const next = insertTextAtCursor(value, cursor, "\n");
          applyFreeformChange(next.value, next.cursor);
          return;
        }

        const submitted = submitDraft(value);
        if (!submitted) {
          return;
        }

        const currentController = controllerRef.current;
        const nextHistoryCursors = pushHistoryCursor(
          currentController.history,
          currentController.historyCursors,
          submitted
        );
        commitController(
          createComposerController(
            pushHistory(currentController.history, submitted),
            "",
            0,
            nextHistoryCursors
          )
        );
        onChange("");
        onCursorChange(0);
        onSubmit(submitted);
        return;
      }

      if (key.leftArrow) {
        onCursorChange(moveCursorLeft(value, cursor));
        return;
      }

      if (key.rightArrow) {
        onCursorChange(moveCursorRight(value, cursor));
        return;
      }

      const deleteEdit = resolveDeleteEdit(value, cursor, key);
      if (deleteEdit) {
        const next = deleteEdit;
        applyFreeformChange(next.value, next.cursor);
        return;
      }

      if (ch === "\x15") {
        applyFreeformChange("", 0);
        return;
      }

      if (ch && !key.ctrl && !key.meta) {
        const next = insertTextAtCursor(value, cursor, ch);
        applyFreeformChange(next.value, next.cursor);
      }
    },
    { isActive }
  );

  const frame = getInputFrameModel({ value, disabled, candidatesVisible });
  const model = getRichComposerModel({ value, disabled });
  const showGhostText =
    Boolean(ghostText) && model.lines.length <= 1 && !value.includes("\n");
  const showInlineHint =
    Boolean(inlineHint) && model.lines.length <= 1 && !value.includes("\n") && !showGhostText;

  return (
    <Box flexDirection="column">
      <Box>
        <Box width={2}>
          <Text color={frame.promptColor} bold>
            {frame.prompt}
          </Text>
        </Box>
        <TextInput
          value={value}
          cursor={cursor}
          disabled={disabled}
          placeholder={model.placeholder}
          textColor={frame.textColor}
          placeholderColor={frame.placeholderColor}
          cursorColor={frame.cursorColor}
          ghostText={showGhostText ? ghostText : null}
          ghostColor={TUI_THEME.composer.ghost}
          inlineHint={showInlineHint ? inlineHint : null}
          hintColor={TUI_THEME.composer.ghost}
        />
      </Box>
    </Box>
  );
}
