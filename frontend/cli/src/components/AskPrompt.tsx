/**
 * Interactive prompt used by the ask tool.
 */

import React, { useState, useRef } from "react";
import { Box, Text, useInput } from "ink";
import type { AskResponse } from "@juice-agents/shared/gateway/types";
import { TUI_THEME } from "../lib/theme.js";
import { pickWindowAroundSelected } from "../lib/overlayWindowing.js";

export interface AskOptionItem {
  label: string;
  value: string;
  description?: string;
}

export interface AskPromptItemModel {
  cursor: "›" | " ";
  mark: "●" | "○" | "☑" | "☐";
  option: AskOptionItem;
  isCustom: boolean;
}

export interface AskPromptModel {
  question: string;
  items: AskPromptItemModel[];
  helpLine: string;
  customLine: string;
}

export const ASK_CUSTOM_OPTION_VALUE = "__ask_custom__";

function isCustomIndex(index: number, options: AskOptionItem[], allowCustom: boolean | undefined): boolean {
  return Boolean(allowCustom) && index === options.length;
}

function getAskItemCount(options: AskOptionItem[], allowCustom: boolean | undefined): number {
  return options.length + (allowCustom ? 1 : 0);
}

function clampAskIndex(index: number, options: AskOptionItem[], allowCustom: boolean | undefined): number {
  return Math.max(0, Math.min(Math.max(0, getAskItemCount(options, allowCustom) - 1), index));
}

export function applyAskCustomTextInput(params: {
  options: AskOptionItem[];
  checkedValues: Set<string>;
  customValue: string;
  input: string;
  multiple: boolean;
}): { selectedIndex: number; checkedValues: Set<string>; customValue: string } {
  const checkedValues = new Set(params.checkedValues);
  if (params.multiple) {
    checkedValues.add(ASK_CUSTOM_OPTION_VALUE);
  }
  return {
    selectedIndex: params.options.length,
    checkedValues,
    customValue: params.customValue + params.input,
  };
}

export function toggleAskSelection(
  checkedValues: Set<string>,
  value: string
): Set<string> {
  const next = new Set(checkedValues);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

export function buildAskPromptModel(params: {
  question: string;
  options: AskOptionItem[];
  selectedIndex: number;
  checkedValues: Set<string>;
  multiple: boolean;
  customValue: string;
  allowCustom?: boolean;
}): AskPromptModel {
  const items = params.options.map((option, index) => {
    const active = params.multiple
      ? params.checkedValues.has(option.value)
      : false;
    return {
      cursor: index === params.selectedIndex ? "›" as const : " " as const,
      mark: params.multiple
        ? (active ? "☑" as const : "☐" as const)
        : "○" as const,
      option,
      isCustom: false,
    };
  });
  if (params.allowCustom) {
    const customActive = params.multiple
      ? params.checkedValues.has(ASK_CUSTOM_OPTION_VALUE)
      : false;
    items.push({
      cursor: params.options.length === params.selectedIndex ? "›" as const : " " as const,
      mark: params.multiple
        ? (customActive ? "☑" as const : "☐" as const)
        : "○" as const,
      option: {
        label: params.customValue ? `Other: ${params.customValue}` : "Other",
        value: ASK_CUSTOM_OPTION_VALUE,
        description: "type a custom response",
      },
      isCustom: true,
    });
  }

  return {
    question: params.question,
    items,
    helpLine: params.multiple
      ? "↑↓ 选择 · Space 勾选 · 输入文字选择 Other · Enter 确认 · Esc 取消"
      : "↑↓ 选择 · 输入文字选择 Other · Enter 确认 · Esc 取消",
    customLine: params.customValue,
  };
}

export function buildAskResponse(params: {
  requestId: string;
  status: AskResponse["status"];
  options: AskOptionItem[];
  selectedIndex: number;
  checkedValues: Set<string>;
  multiple: boolean;
  customValue: string;
  allowCustom?: boolean;
  question?: string;
}): AskResponse {
  if (params.status !== "answered") {
    return {
      status: params.status,
      request_id: params.requestId,
      question: params.question,
      selected: [],
      custom_response: "",
      error: params.status === "error" ? "ask prompt failed" : "",
    };
  }

  const custom = params.customValue.trim();
  const customSelected = params.allowCustom
    ? params.multiple
      ? params.checkedValues.has(ASK_CUSTOM_OPTION_VALUE)
      : isCustomIndex(params.selectedIndex, params.options, params.allowCustom)
    : false;
  const selected = params.multiple
    ? params.options.filter((option) => params.checkedValues.has(option.value))
    : customSelected
      ? []
      : params.options[params.selectedIndex]
        ? [params.options[params.selectedIndex]]
        : [];

  return {
    status: "answered",
    request_id: params.requestId,
    question: params.question,
    selected,
    custom_response: customSelected ? custom : "",
    error: "",
  };
}

interface AskPromptProps {
  requestId: string;
  question: string;
  options: AskOptionItem[];
  multiple: boolean;
  allowCustom: boolean;
  isActive?: boolean;
  onSubmit: (response: AskResponse) => void;
  maxRows?: number;
}

export function AskPrompt({
  requestId,
  question,
  options,
  multiple,
  allowCustom,
  isActive = true,
  onSubmit,
  maxRows,
}: AskPromptProps) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [checkedValues, setCheckedValues] = useState<Set<string>>(() => new Set());
  const [customValue, setCustomValue] = useState("");
  const submittedRef = useRef(false);

  useInput(
    (input, key) => {
      if (submittedRef.current) return;

      if (key.escape) {
        submittedRef.current = true;
        onSubmit(buildAskResponse({
          requestId,
          question,
          status: "cancelled",
          options,
          selectedIndex,
          checkedValues,
          multiple,
          customValue,
          allowCustom,
        }));
        return;
      }

      if (key.upArrow || (!allowCustom && input === "k")) {
        setSelectedIndex((prev) => Math.max(0, prev - 1));
        return;
      }

      if (key.downArrow || (!allowCustom && input === "j")) {
        setSelectedIndex((prev) => clampAskIndex(prev + 1, options, allowCustom));
        return;
      }

      if (multiple && input === " ") {
        if (isCustomIndex(selectedIndex, options, allowCustom)) {
          setCheckedValues((prev) => toggleAskSelection(prev, ASK_CUSTOM_OPTION_VALUE));
          return;
        }
        if (options[selectedIndex]) {
          setCheckedValues((prev) => toggleAskSelection(prev, options[selectedIndex].value));
          return;
        }
      }

      if (key.backspace || key.delete) {
        if (allowCustom && (isCustomIndex(selectedIndex, options, allowCustom) || customValue.length > 0)) {
          setSelectedIndex(options.length);
          setCustomValue((prev) => prev.slice(0, -1));
          return;
        }
        return;
      }

      if (key.return) {
        submittedRef.current = true;
        onSubmit(buildAskResponse({
          requestId,
          question,
          status: "answered",
          options,
          selectedIndex,
          checkedValues,
          multiple,
          customValue,
          allowCustom,
        }));
        return;
      }

      if (!allowCustom) {
        return;
      }

      if (input === "\x15") {
        setSelectedIndex(options.length);
        setCustomValue("");
        setCheckedValues((prev) => {
          const next = new Set(prev);
          next.delete(ASK_CUSTOM_OPTION_VALUE);
          return next;
        });
        return;
      }

      if (input && !key.ctrl && !key.meta) {
        const next = applyAskCustomTextInput({
          options,
          checkedValues,
          customValue,
          input,
          multiple,
        });
        setSelectedIndex(next.selectedIndex);
        setCustomValue(next.customValue);
        setCheckedValues(next.checkedValues);
      }
    },
    { isActive }
  );

  const model = buildAskPromptModel({
    question,
    options,
    selectedIndex,
    checkedValues,
    multiple,
    customValue,
    allowCustom,
  });

  // windowing
  const ASK_CHROME_ROWS = 4; // marginTop + question + marginTop + help
  const itemBudget = Math.max(3, (maxRows ?? 999) - ASK_CHROME_ROWS);
  const window = pickWindowAroundSelected({
    items: model.items,
    rowsPerItem: (item) => (item.option.description ? 2 : 1),
    selectedIndex,
    budgetRows: itemBudget,
  });
  const visibleItems = model.items.slice(window.startIndex, window.endIndex + 1);

  return (
    <Box flexDirection="column" marginTop={1} marginBottom={1}>
      <Text color={TUI_THEME.brand.meta}>{model.question}</Text>

      {window.topHidden > 0 && (
        <Box marginTop={1}>
          <Text color={TUI_THEME.surface.muted} dimColor>
            {`… ↑ ${window.topHidden} more`}
          </Text>
        </Box>
      )}

      {visibleItems.map((item) => (
        <Box key={item.option.value} marginTop={1}>
          <Text color={TUI_THEME.surface.subtle}>{item.cursor}</Text>
          <Text> </Text>
          <Text color={TUI_THEME.brand.wordmark}>{item.mark}</Text>
          <Text> </Text>
          <Box flexDirection="column" flexGrow={1}>
            {item.isCustom ? (
              <Text color={TUI_THEME.surface.text}>
                Other:{" "}
                <Text color={TUI_THEME.composer.text}>
                  {model.customLine}
                  {item.cursor === "›" ? (
                    <Text color={TUI_THEME.composer.cursor}>█</Text>
                  ) : null}
                </Text>
              </Text>
            ) : (
              <Text color={TUI_THEME.surface.text}>{item.option.label}</Text>
            )}
            {item.option.description ? (
              <Text color={TUI_THEME.surface.muted} dimColor>
                {"  " + item.option.description}
              </Text>
            ) : null}
          </Box>
        </Box>
      ))}

      {window.bottomHidden > 0 && (
        <Box marginTop={1}>
          <Text color={TUI_THEME.surface.muted} dimColor>
            {`… ↓ ${window.bottomHidden} more`}
          </Text>
        </Box>
      )}

      <Box marginTop={1}>
        <Text color={TUI_THEME.surface.muted} dimColor>
          {model.helpLine}
        </Text>
      </Box>
    </Box>
  );
}
