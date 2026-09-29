/**
 * Generic interactive selector component for the CLI.
 */

import React from "react";
import { Box, Text, useInput } from "ink";
import type { SelectorItem } from "../hooks/useInteractiveSelector.js";
import { cycleModelEffort as cycleEffort, type ModelEffort } from "../lib/modelEffort.js";
import { TUI_THEME } from "../lib/theme.js";
import { pickWindowAroundSelected } from "../lib/overlayWindowing.js";

export type SelectorEffort = ModelEffort;

const CHROME_ROWS = 5; // marginTop + title + marginBottom + help + marginBottom
const EFFORT_ROWS = 2; // marginTop + effort line

export interface SelectorWindow {
  visibleItems: SelectorItem[];
  topHidden: number;
  bottomHidden: number;
  startIndex: number;
}

/**
 * 计算可见项窗口（按 selectedIndex 居中 windowing）。
 */
export function pickVisibleItems(params: {
  items: SelectorItem[];
  selectedIndex: number;
  maxRows: number;
  hasEffort: boolean;
}): SelectorWindow {
  const { items, selectedIndex, maxRows, hasEffort } = params;
  const chromeTotal = CHROME_ROWS + (hasEffort ? EFFORT_ROWS : 0);

  // 无限制或空列表
  if (maxRows <= 0 || items.length === 0) {
    return {
      visibleItems: items,
      topHidden: 0,
      bottomHidden: 0,
      startIndex: 0,
    };
  }

  // 预留 2 行给指示行（… ↑ / … ↓）
  const indicatorReserve = 2;
  const budgetRows = Math.max(1, maxRows - chromeTotal - indicatorReserve);

  const window = pickWindowAroundSelected({
    items,
    rowsPerItem: (item) => (item.description ? 2 : 1),
    selectedIndex,
    budgetRows,
  });

  return {
    visibleItems: items.slice(window.startIndex, window.endIndex + 1),
    topHidden: window.topHidden,
    bottomHidden: window.bottomHidden,
    startIndex: window.startIndex,
  };
}

export interface InteractiveSelectorModel {
  title: string;
  items: SelectorItem[];
  selectedIndex: number;
  selectedItem: SelectorItem | null;
  effortLine: string | null;
  helpLine: string;
}

export const SELECTOR_EFFORT_LABEL_COLOR = TUI_THEME.surface.text;
export const SELECTOR_EFFORT_VALUE_COLOR = TUI_THEME.brand.wordmark;

export function cycleModelEffort(
  current: SelectorEffort | string | undefined,
  direction: "left" | "right",
  source?: { supported_efforts?: readonly string[] } | null
): SelectorEffort {
  return cycleEffort(current, direction, source);
}

export function buildInteractiveSelectorModel(params: {
  title: string;
  items: SelectorItem[];
  selectedIndex: number;
  effort?: SelectorEffort;
}): InteractiveSelectorModel {
  return {
    title: params.title,
    items: params.items,
    selectedIndex: params.selectedIndex,
    selectedItem: params.items[params.selectedIndex] || null,
    effortLine:
      params.effort === undefined
        ? null
        : `Effort: ${params.effort}  ← → to adjust`,
    helpLine:
      params.effort === undefined
        ? "↑↓ 选择 · Enter 确认 · Esc 取消"
        : "↑↓ 选择 · ←→ 调 effort · Enter 确认 · Esc 取消",
  };
}

interface InteractiveSelectorProps {
  title: string;
  items: SelectorItem[];
  selectedIndex: number;
  isActive?: boolean;
  onSelect: () => void;
  onCancel: () => void;
  onUp: () => void;
  onDown: () => void;
  effort?: SelectorEffort;
  onLeft?: () => void;
  onRight?: () => void;
  /** 整个 selector（含 chrome）允许占用的最大行数；超出按 windowing 截断 */
  maxRows?: number;
}

export function InteractiveSelector({
  title,
  items,
  selectedIndex,
  isActive = true,
  onSelect,
  onCancel,
  onUp,
  onDown,
  effort,
  onLeft,
  onRight,
  maxRows,
}: InteractiveSelectorProps) {
  useInput(
    (input, key) => {
      if (key.upArrow || input === "k") {
        onUp();
      } else if (key.downArrow || input === "j") {
        onDown();
      } else if (key.leftArrow && onLeft) {
        onLeft();
      } else if (key.rightArrow && onRight) {
        onRight();
      } else if (key.return) {
        onSelect();
      } else if (key.escape) {
        onCancel();
      }
    },
    { isActive }
  );

  const model = buildInteractiveSelectorModel({
    title,
    items,
    selectedIndex,
    effort,
  });

  // windowing：当 maxRows 有限时，仅渲染部分项
  const window = pickVisibleItems({
    items,
    selectedIndex,
    maxRows: maxRows ?? 999,
    hasEffort: effort !== undefined,
  });

  return (
    <Box flexDirection="column" marginTop={1} marginBottom={1}>
      <Box marginBottom={1}>
        <Text color={TUI_THEME.brand.meta}>
          {model.title}
        </Text>
      </Box>

      {window.topHidden > 0 && (
        <Text color={TUI_THEME.surface.muted} dimColor>
          {`… ↑ ${window.topHidden} more`}
        </Text>
      )}

      {window.visibleItems.map((item, vi) => {
        const index = window.startIndex + vi;
        const isSelected = index === selectedIndex;
        const isActive = item.active;

        return (
          <Box key={item.value}>
            <Text color={TUI_THEME.surface.subtle}>
              {isSelected ? "›" : " "}
            </Text>
            <Text> </Text>
            <Box flexDirection="column" flexGrow={1}>
              <Text
                color={isSelected ? TUI_THEME.brand.wordmark : TUI_THEME.surface.text}
              >
                {isActive ? "● " : "  "}
                {item.label}
              </Text>
              {item.description && (
                <Text color={TUI_THEME.surface.muted} dimColor>
                  {"  " + item.description}
                </Text>
              )}
            </Box>
          </Box>
        );
      })}

      {window.bottomHidden > 0 && (
        <Text color={TUI_THEME.surface.muted} dimColor>
          {`… ↓ ${window.bottomHidden} more`}
        </Text>
      )}

      {model.effortLine ? (
        <Box marginTop={1}>
          <Text color={SELECTOR_EFFORT_LABEL_COLOR}>
            Effort:{" "}
          </Text>
          <Text color={SELECTOR_EFFORT_VALUE_COLOR}>
            {effort}
          </Text>
          <Text color={SELECTOR_EFFORT_LABEL_COLOR}>
            {"  ← → to adjust"}
          </Text>
        </Box>
      ) : null}

      <Box marginTop={1}>
        <Text color={TUI_THEME.surface.muted} dimColor>
          {model.helpLine}
        </Text>
      </Box>
    </Box>
  );
}
