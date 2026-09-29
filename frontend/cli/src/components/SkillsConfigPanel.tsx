/**
 * Editable skills enablement panel for the CLI.
 */

import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import type { SkillConfigInfo, SkillsConfigStatusResponse } from "@juice-agents/shared/gateway/types";
import { TUI_THEME } from "../lib/theme.js";
import { pickWindowAroundSelected } from "../lib/overlayWindowing.js";

export type SkillsConfigRowId = `skill:${string}`;

export interface SkillsConfigDraft {
  skillsEnabled: boolean;
  disabledSkills: string[];
  skills: SkillConfigInfo[];
}

export interface SkillsConfigApplyPlan {
  enabled: boolean;
  disabled: string[];
}

export interface SkillsConfigRow {
  id: SkillsConfigRowId;
  label: string;
  value: string;
  description?: string;
  changed: boolean;
  selected: boolean;
}

interface SkillsConfigPanelProps {
  initialDraft: SkillsConfigDraft;
  isActive?: boolean;
  onSave: (plan: SkillsConfigApplyPlan, draft: SkillsConfigDraft) => Promise<void> | void;
  onCancel: () => void;
  maxRows?: number;
}

function boolLabel(value: boolean): string {
  return value ? "on" : "off";
}

function normalizeDisabledSkills(values: string[]): string[] {
  return [...new Set(values.map((item) => item.trim().toLowerCase()).filter(Boolean))].sort();
}

function skillKey(skill: Pick<SkillConfigInfo, "name" | "qualified_name">): string {
  return String(skill.qualified_name || skill.name || "").trim().toLowerCase();
}

function skillAliases(skill: Pick<SkillConfigInfo, "name" | "qualified_name">): string[] {
  return [skill.name, skill.qualified_name]
    .map((item) => String(item || "").trim().toLowerCase())
    .filter(Boolean);
}

function hasDisabledAlias(
  disabledSkills: string[],
  skill: Pick<SkillConfigInfo, "name" | "qualified_name">,
): boolean {
  const disabled = new Set(disabledSkills);
  return skillAliases(skill).some((alias) => disabled.has(alias));
}

function skillLabel(skill: Pick<SkillConfigInfo, "name" | "qualified_name">): string {
  return String(skill.name || skill.qualified_name || "").trim();
}

export function createSkillsConfigDraft(status: SkillsConfigStatusResponse): SkillsConfigDraft {
  const disabled = new Set(normalizeDisabledSkills(status.disabled || []));
  for (const skill of status.skills || []) {
    if (!skill.enabled) {
      const key = skillKey(skill);
      if (key) disabled.add(key);
    }
  }
  return {
    skillsEnabled: status.enabled !== false,
    disabledSkills: normalizeDisabledSkills([...disabled]),
    skills: [...(status.skills || [])].sort((left, right) => skillLabel(left).localeCompare(skillLabel(right))),
  };
}

export function changeSkillsConfigDraft(draft: SkillsConfigDraft, rowId: SkillsConfigRowId): SkillsConfigDraft {
  const key = rowId.slice("skill:".length).toLowerCase();
  const skill = draft.skills.find((item) => skillAliases(item).includes(key));
  const disabled = new Set(draft.disabledSkills);
  if (skill && hasDisabledAlias(draft.disabledSkills, skill)) {
    for (const alias of skillAliases(skill)) {
      disabled.delete(alias);
    }
  } else if (disabled.has(key)) {
    for (const alias of skill ? skillAliases(skill) : [key]) {
      disabled.delete(alias);
    }
  } else {
    disabled.add(key);
  }
  return { ...draft, disabledSkills: normalizeDisabledSkills([...disabled]) };
}

export function buildSkillsConfigApplyPlan(draft: SkillsConfigDraft): SkillsConfigApplyPlan {
  return {
    enabled: draft.skillsEnabled,
    disabled: normalizeDisabledSkills(draft.disabledSkills),
  };
}

export function buildSkillsConfigRows(params: {
  draft: SkillsConfigDraft;
  initialDraft: SkillsConfigDraft;
  selectedIndex: number;
}): SkillsConfigRow[] {
  const rowIds = params.draft.skills
    .map((skill) => `skill:${skillKey(skill)}` as const)
    .filter((id) => id !== "skill:");
  const selectedIndex = Math.max(0, Math.min(params.selectedIndex, rowIds.length - 1));
  return rowIds.map((id, index) => {
    const key = id.slice("skill:".length);
    const skill = params.draft.skills.find((item) => skillKey(item) === key);
    const disabled = skill ? hasDisabledAlias(params.draft.disabledSkills, skill) : params.draft.disabledSkills.includes(key);
    const initialDisabled = skill
      ? hasDisabledAlias(params.initialDraft.disabledSkills, skill)
      : params.initialDraft.disabledSkills.includes(key);
    return {
      id,
      label: skill ? skillLabel(skill) : key,
      value: boolLabel(!disabled),
      description: skill ? [skill.category, skill.source].filter(Boolean).join(" · ") || skill.qualified_name : undefined,
      changed: disabled !== initialDisabled,
      selected: index === selectedIndex,
    };
  });
}

export function SkillsConfigPanel({
  initialDraft,
  isActive = true,
  onSave,
  onCancel,
  maxRows,
}: SkillsConfigPanelProps) {
  const [draft, setDraft] = useState(initialDraft);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const rows = buildSkillsConfigRows({ draft, initialDraft, selectedIndex });
  const selectedRow = rows[Math.max(0, Math.min(selectedIndex, rows.length - 1))] || null;
  const maxRowIndex = Math.max(0, rows.length - 1);

  useInput(
    (input, key) => {
      if (key.upArrow || input === "k") {
        setSelectedIndex((prev) => Math.max(0, prev - 1));
      } else if (key.downArrow || input === "j") {
        setSelectedIndex((prev) => Math.min(maxRowIndex, prev + 1));
      } else if (key.leftArrow || key.rightArrow || input === " ") {
        if (selectedRow) setDraft((current) => changeSkillsConfigDraft(current, selectedRow.id));
      } else if (key.return) {
        void onSave(buildSkillsConfigApplyPlan(draft), draft);
      } else if (key.escape) {
        onCancel();
      }
    },
    { isActive },
  );

  // windowing
  const SKILLS_CHROME_ROWS = 5; // marginTop + title + marginBottom + help + marginBottom
  const warningRows = !draft.skillsEnabled ? 2 : 0; // global off warning
  const emptyRows = rows.length === 0 ? 1 : 0;
  const itemBudget = Math.max(3, (maxRows ?? 999) - SKILLS_CHROME_ROWS - warningRows - emptyRows);
  const window = rows.length > 0 ? pickWindowAroundSelected({
    items: rows,
    rowsPerItem: (r) => (r.description ? 2 : 1),
    selectedIndex,
    budgetRows: itemBudget,
  }) : { startIndex: 0, endIndex: -1, topHidden: 0, bottomHidden: 0 };
  const visibleRows = rows.slice(window.startIndex, window.endIndex + 1);

  return (
    <Box flexDirection="column" marginTop={1} marginBottom={1}>
      <Box marginBottom={1}>
        <Text color={TUI_THEME.brand.meta}>Skills    Enable/Disable Skills</Text>
      </Box>
      {!draft.skillsEnabled ? (
        <Box marginBottom={1}>
          <Text color={TUI_THEME.surface.muted} dimColor>
            Global Skills is off in /config; individual choices are saved for when it is on.
          </Text>
        </Box>
      ) : null}
      {rows.length === 0 ? (
        <Box>
          <Text color={TUI_THEME.surface.muted} dimColor>
            No installed skills found.
          </Text>
        </Box>
      ) : null}

      {window.topHidden > 0 && (
        <Text color={TUI_THEME.surface.muted} dimColor>
          {`… ↑ ${window.topHidden} more`}
        </Text>
      )}

      {visibleRows.map((row) => (
        <Box key={row.id} flexDirection="column">
          <Box>
            <Text color={TUI_THEME.surface.subtle}>{row.selected ? "›" : " "}</Text>
            <Text> </Text>
            <Text color={row.selected ? TUI_THEME.brand.wordmark : TUI_THEME.surface.text}>
              {row.label.padEnd(26)}
            </Text>
            <Text color={row.changed ? TUI_THEME.state.warning : TUI_THEME.surface.text}>{row.value}</Text>
            {row.changed ? <Text color={TUI_THEME.state.warning}> *</Text> : null}
          </Box>
          {row.description ? (
            <Box marginLeft={4}>
              <Text color={TUI_THEME.surface.muted} dimColor>
                {row.description}
              </Text>
            </Box>
          ) : null}
        </Box>
      ))}

      {window.bottomHidden > 0 && (
        <Text color={TUI_THEME.surface.muted} dimColor>
          {`… ↓ ${window.bottomHidden} more`}
        </Text>
      )}

      <Box marginTop={1}>
        <Text color={TUI_THEME.surface.muted} dimColor>
          Up/Down select · Space/Left/Right toggle · Enter save · Esc cancel
        </Text>
      </Box>
    </Box>
  );
}
