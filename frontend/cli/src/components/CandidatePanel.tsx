/**
 * Lightweight completion overlay rendered beneath the composer.
 */

import React from "react";
import { Box, Text } from "ink";
import type { CandidateItem } from "../hooks/useCompletion.js";
import { TUI_COPY, TUI_THEME } from "../lib/theme.js";

interface CandidatePanelProps {
  candidates: CandidateItem[];
  selectedIndex: number;
  visible: boolean;
}

export const MAX_VISIBLE_COMPLETION_CANDIDATES = 5;

export interface CandidatePanelModel {
  visible: boolean;
  hint: string;
  layout: "overlay";
  startIndex: number;
  total: number;
  hasHiddenItems: boolean;
  items: Array<CandidateItem & { isSelected: boolean }>;
}

export function getCandidateWindowStart(params: {
  total: number;
  selectedIndex: number;
  maxVisible?: number;
}): number {
  const maxVisible = Math.max(1, params.maxVisible ?? MAX_VISIBLE_COMPLETION_CANDIDATES);
  const total = Math.max(0, params.total);
  if (total <= maxVisible) {
    return 0;
  }

  const selectedIndex = Math.max(0, Math.min(params.selectedIndex, total - 1));
  const lastStart = total - maxVisible;
  return Math.min(Math.max(0, selectedIndex - maxVisible + 1), lastStart);
}

export function getCandidatePanelModel({
  candidates,
  selectedIndex,
  visible,
}: CandidatePanelProps): CandidatePanelModel {
  if (!visible || candidates.length === 0) {
    return {
      visible: false,
      hint: TUI_COPY.completionHint,
      layout: "overlay",
      startIndex: 0,
      total: candidates.length,
      hasHiddenItems: false,
      items: [],
    };
  }

  const startIndex = getCandidateWindowStart({
    total: candidates.length,
    selectedIndex,
  });
  const visibleItems = candidates.slice(
    startIndex,
    startIndex + MAX_VISIBLE_COMPLETION_CANDIDATES
  );

  return {
    visible: true,
    hint: TUI_COPY.completionHint,
    layout: "overlay",
    startIndex,
    total: candidates.length,
    hasHiddenItems: candidates.length > MAX_VISIBLE_COMPLETION_CANDIDATES,
    items: visibleItems.map((item, index) => ({
      ...item,
      isSelected: startIndex + index === selectedIndex,
    })),
  };
}

export function CompletionOverlay({
  candidates,
  selectedIndex,
  visible,
}: CandidatePanelProps) {
  const model = getCandidatePanelModel({ candidates, selectedIndex, visible });

  if (!model.visible) {
    return null;
  }

  return (
    <Box flexDirection="column" marginTop={1}>
      {model.items.map((item) => (
        <Box key={item.label}>
          <Text
            color={
              item.isSelected
                ? TUI_THEME.completion.marker
                : TUI_THEME.surface.muted
            }
          >
            {item.isSelected ? "› " : "· "}
          </Text>
          <Text
            bold={item.isSelected}
            color={item.isSelected ? TUI_THEME.completion.selected : TUI_THEME.completion.text}
          >
            {item.label.padEnd(18)}
          </Text>
          <Text color={TUI_THEME.completion.hint}> {item.description}</Text>
        </Box>
      ))}
      {model.hasHiddenItems ? (
        <Text color={TUI_THEME.completion.hint}>
          {model.startIndex + 1}-{model.startIndex + model.items.length}/{model.total}
        </Text>
      ) : null}
      <Text color={TUI_THEME.completion.hint}>{model.hint}</Text>
    </Box>
  );
}

export const CandidatePanel = CompletionOverlay;
