/**
 * Cursor-aware Ink text input used by the Rich Composer.
 *
 * The component intentionally renders a synthetic block cursor instead of
 * using a native terminal cursor. That keeps the implementation compatible
 * with upstream Ink while the editing behavior stays close to Hermes-style
 * composer inputs.
 */

import React, { useEffect, useState } from "react";
import { Box, Text } from "ink";
import { clampCursor, getCursorLineColumn } from "../lib/textEditing.js";

export interface TextInputLineModel {
  beforeCursor: string;
  afterCursor: string;
  text: string;
  showCursor: boolean;
}

export interface TextInputModel {
  lines: TextInputLineModel[];
  placeholder: string;
  showPlaceholder: boolean;
  cursor: number;
}

export interface TextInputProps {
  value: string;
  cursor?: number;
  disabled: boolean;
  placeholder: string;
  textColor: string;
  placeholderColor: string;
  cursorColor: string;
  ghostText?: string | null;
  ghostColor?: string;
  inlineHint?: string | null;
  hintColor?: string;
}

export function getTextInputModel(params: {
  value: string;
  cursor: number;
  disabled: boolean;
  placeholder: string;
}): TextInputModel {
  const cursor = clampCursor(params.value, params.cursor);
  const showPlaceholder = params.value.length === 0;

  if (showPlaceholder) {
    return {
      lines: [
        {
          beforeCursor: "",
          afterCursor: "",
          text: "",
          showCursor: !params.disabled,
        },
      ],
      placeholder: params.placeholder,
      showPlaceholder,
      cursor,
    };
  }

  const active = getCursorLineColumn(params.value, cursor);
  const lines = params.value.split("\n");
  let offset = 0;

  return {
    lines: lines.map((line, index) => {
      const start = offset;
      offset += line.length + 1;
      const column = index === active.line ? cursor - start : line.length;
      return {
        beforeCursor: line.slice(0, Math.max(0, column)),
        afterCursor: line.slice(Math.max(0, column)),
        text: line,
        showCursor: !params.disabled && index === active.line,
      };
    }),
    placeholder: params.placeholder,
    showPlaceholder,
    cursor,
  };
}

export function TextInput({
  value,
  cursor,
  disabled,
  placeholder,
  textColor,
  placeholderColor,
  cursorColor,
  ghostText,
  ghostColor,
  inlineHint,
  hintColor,
}: TextInputProps) {
  const [localCursor, setLocalCursor] = useState(() => value.length);

  useEffect(() => {
    setLocalCursor(value.length);
  }, [value]);

  const activeCursor = cursor ?? localCursor;
  const model = getTextInputModel({
    value,
    cursor: activeCursor,
    disabled,
    placeholder,
  });

  if (model.showPlaceholder) {
    return (
      <Text color={placeholderColor} dimColor>
        {model.placeholder}
        {model.lines[0]?.showCursor ? <Text color={cursorColor}>█</Text> : null}
      </Text>
    );
  }

  return (
    <Box flexDirection="column" flexGrow={1}>
      {model.lines.map((line, index) => (
        <Text color={textColor} key={`${index}-${line.text}`}>
          {index === 0 ? "" : "  "}
          {line.beforeCursor || (line.showCursor ? "" : line.text ? "" : " ")}
          {line.showCursor ? <Text color={cursorColor}>█</Text> : null}
          {line.afterCursor}
          {line.showCursor && ghostText ? (
            <Text color={ghostColor}>{ghostText}</Text>
          ) : null}
          {line.showCursor && !ghostText && inlineHint ? (
            <Text color={hintColor || ghostColor} dimColor>{inlineHint}</Text>
          ) : null}
        </Text>
      ))}
    </Box>
  );
}
