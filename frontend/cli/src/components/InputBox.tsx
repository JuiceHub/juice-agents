/**
 * Composer frame helpers shared by the Rich Composer.
 *
 * The interactive implementation now lives in `RichComposer.tsx`, while this
 * file continues to host the pure model used by tests.
 */

import { TUI_COPY, TUI_THEME } from "../lib/theme.js";

export interface InputFrameModel {
  prompt: string;
  placeholder: string;
  tone: "ready" | "busy" | "active";
  promptColor: string;
  textColor: string;
  placeholderColor: string;
  cursorColor: string;
}

export function getInputFrameModel(params: {
  value: string;
  disabled: boolean;
  candidatesVisible: boolean;
}): InputFrameModel {
  if (params.disabled) {
    return {
      prompt: "·",
      placeholder: TUI_COPY.placeholder,
      tone: "busy",
      promptColor: TUI_THEME.surface.muted,
      textColor: TUI_THEME.composer.text,
      placeholderColor: TUI_THEME.composer.placeholder,
      cursorColor: TUI_THEME.composer.cursor,
    };
  }

  if (params.candidatesVisible) {
    return {
      prompt: ">",
      placeholder: TUI_COPY.placeholder,
      tone: "active",
      promptColor: TUI_THEME.brand.glow,
      textColor: TUI_THEME.composer.text,
      placeholderColor: TUI_THEME.composer.placeholder,
      cursorColor: TUI_THEME.composer.cursor,
    };
  }

  return {
    prompt: ">",
    placeholder: TUI_COPY.placeholder,
    tone: "ready",
    promptColor: TUI_THEME.composer.prompt,
    textColor: TUI_THEME.composer.text,
    placeholderColor: TUI_THEME.composer.placeholder,
    cursorColor: TUI_THEME.composer.cursor,
  };
}
