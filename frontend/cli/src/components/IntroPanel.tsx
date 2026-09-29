/**
 * Transcript intro panel.
 */

import React from "react";
import { Box, Text } from "ink";
import type { IntroPanelModel } from "../lib/presenter.js";
import { TUI_COPY, TUI_THEME } from "../lib/theme.js";

interface IntroPanelProps {
  panel: IntroPanelModel;
  terminalColumns?: number;
}

interface IntroBannerLineColorParams {
  lines: string[];
  gradient: readonly string[];
  fallbackColor: string;
  hasBannerLines: boolean;
}

function getIntroBannerLineColors({
  lines,
  gradient,
  fallbackColor,
  hasBannerLines,
}: IntroBannerLineColorParams): string[] {
  let bannerColorIndex = 0;

  return lines.map((line) => {
    if (!hasBannerLines || line.trim() === "") {
      bannerColorIndex = 0;
      return fallbackColor;
    }

    const color = gradient[Math.min(bannerColorIndex, gradient.length - 1)];
    bannerColorIndex += 1;
    return color;
  });
}

interface IntroBannerDisplayLineParams {
  lines: string[];
  terminalColumns?: number;
  reservedColumns: number;
  sectionGap: string;
}

const INTRO_BANNER_RESERVED_COLUMNS = 4;
const INTRO_BANNER_SECTION_GAP = "  ";

function splitIntroBannerSections(lines: string[]): string[][] {
  const sections: string[][] = [];
  let currentSection: string[] = [];

  for (const line of lines) {
    if (line.trim() === "") {
      if (currentSection.length > 0) {
        sections.push(currentSection);
        currentSection = [];
      }
      continue;
    }

    currentSection.push(line);
  }

  if (currentSection.length > 0) {
    sections.push(currentSection);
  }

  return sections;
}

function getMaxLineWidth(lines: string[]): number {
  return lines.reduce((max, line) => Math.max(max, line.length), 0);
}

function combineIntroBannerSections(sections: string[][], sectionGap: string): string[] {
  const sectionWidths = sections.map(getMaxLineWidth);
  const maxSectionHeight = sections.reduce(
    (max, section) => Math.max(max, section.length),
    0
  );
  const combinedLines: string[] = [];

  // Keep each banner word in its own fixed-width column so rows stay aligned
  // when the JUICE and AGENTS art are displayed side by side.
  for (let row = 0; row < maxSectionHeight; row += 1) {
    const combinedLine = sections
      .map((section, sectionIndex) =>
        (section[row] || "").padEnd(sectionWidths[sectionIndex], " ")
      )
      .join(sectionGap)
      .trimEnd();
    combinedLines.push(combinedLine);
  }

  return combinedLines;
}

function getIntroBannerDisplayLines({
  lines,
  terminalColumns,
  reservedColumns,
  sectionGap,
}: IntroBannerDisplayLineParams): string[] {
  const sections = splitIntroBannerSections(lines);
  if (sections.length < 2 || !terminalColumns || terminalColumns <= 0) {
    return lines;
  }

  const combinedLines = combineIntroBannerSections(sections, sectionGap);
  const availableColumns = terminalColumns - reservedColumns;
  if (getMaxLineWidth(combinedLines) <= availableColumns) {
    return combinedLines;
  }

  return lines;
}

export function IntroPanel({ panel, terminalColumns }: IntroPanelProps) {
  const wordmark = panel.wordmark || "JUICE AGENTS";
  const hasBannerLines = panel.bannerLines.length > 0;
  const bannerLines = hasBannerLines
    ? getIntroBannerDisplayLines({
        lines: panel.bannerLines,
        terminalColumns,
        reservedColumns: INTRO_BANNER_RESERVED_COLUMNS,
        sectionGap: INTRO_BANNER_SECTION_GAP,
      })
    : [wordmark];
  const bannerLineColors = getIntroBannerLineColors({
    lines: bannerLines,
    gradient: TUI_COPY.welcomeBannerGradient,
    fallbackColor: TUI_THEME.brand.wordmark,
    hasBannerLines,
  });

  return (
    <Box flexDirection="row" marginBottom={1}>
      <Box marginRight={1}>
        <Text color={TUI_THEME.brand.guide}>│</Text>
      </Box>
      <Box flexDirection="column">
        {bannerLines.map((line, index) => {
          return (
            <Text bold color={bannerLineColors[index]} key={`${index}-${line || "space"}`}>
              {line || " "}
            </Text>
          );
        })}
        {panel.metaLines.map((line, index) => (
          <Text
            color={index === 0 ? TUI_THEME.brand.meta : TUI_THEME.surface.muted}
            key={`${index}-${line}`}
          >
            {line}
          </Text>
        ))}
        <Box marginTop={1}>
          <Text color={TUI_THEME.surface.text}>{panel.tagline}</Text>
        </Box>
      </Box>
    </Box>
  );
}

// Export utility functions for introRenderer
export {
  getIntroBannerDisplayLines,
  getIntroBannerLineColors,
  INTRO_BANNER_RESERVED_COLUMNS,
  INTRO_BANNER_SECTION_GAP,
};
