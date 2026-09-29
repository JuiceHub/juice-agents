/**
 * Editable runtime configuration panel for the CLI.
 */

import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import type {
  MemoryStatusResponse,
  RuntimeModelInfo,
  SessionStatus,
  SkillsConfigStatusResponse,
  WorkspaceRuntimeConfig,
} from "@juice-agents/shared/gateway/types";
import { getSupportedModelEfforts, normalizeEffortForModel, type ModelEffort } from "../lib/modelEffort.js";
import { TUI_THEME } from "../lib/theme.js";
import { pickWindowAroundSelected } from "../lib/overlayWindowing.js";

export type ConfigRowId =
  | "memory"
  | "dream"
  | "browser"
  | "image"
  | "skills"
  | "self_evolution"
  | "graphs"
  | "agent_mode"
  | "agent_type"
  | "model"
  | "model_effort";

export type ConfigChangeDirection = "next" | "previous";

export interface ConfigPanelDraft {
  memoryEnabled: boolean;
  dreamEnabled: boolean;
  browserEnabled: boolean;
  imageEnabled: boolean;
  skillsEnabled: boolean;
  selfEvolutionEnabled: boolean;
  graphsEnabled: boolean;
  disabledSkills: string[];
  skillsCount: number;
  permissionMode: string;
  agentMode: string;
  agentType: string;
  modelName: string;
  modelEffort: ModelEffort;
}

export interface ConfigPanelRow {
  id: ConfigRowId;
  label: string;
  value: string;
  description?: string;
  changed: boolean;
  selected: boolean;
}

export interface ConfigPanelModel {
  title: string;
  rows: ConfigPanelRow[];
  selectedRow: ConfigPanelRow | null;
  helpLine: string;
}

export interface ConfigApplyPlan {
  memoryChanges: Array<{ feature: "memory" | "dream"; enabled: boolean }>;
  browserChanges: Array<{ enabled: boolean }>;
  imageChanges: Array<{ enabled: boolean }>;
  skillsChange: { enabled: boolean; disabled: string[] } | null;
  selfEvolutionChange: { enabled: boolean } | null;
  graphsChange: { enabled: boolean } | null;
  runtimePatch: {
    agent_mode?: string;
    agent_type?: string;
    model_name?: string;
    model_effort?: ModelEffort;
  };
  switchPermissionMode: null;
  switchAgentMode: { agent_mode: string; agent_type: string } | null;
  switchModel: { model_name: string; model_effort: ModelEffort } | null;
}

interface ConfigPanelProps {
  initialDraft: ConfigPanelDraft;
  models: RuntimeModelInfo[];
  isActive?: boolean;
  onSave: (plan: ConfigApplyPlan, draft: ConfigPanelDraft) => Promise<void> | void;
  onCancel: () => void;
  /** 整个 panel（含 chrome）允许占用的最大行数 */
  maxRows?: number;
}

// 执行模式取值域；每个值由已注册的 ModeProfile 定义。
const AGENT_MODE_OPTIONS = ["agent", "plan", "team", "group"];
const AGENT_TYPE_OPTIONS = ["react", "codeact"];
const STATIC_ROW_ORDER: ConfigRowId[] = ["memory", "dream", "browser", "image", "skills", "self_evolution", "graphs"];

function boolLabel(value: boolean): string {
  return value ? "on" : "off";
}

function cycleValue<T>(values: T[], current: T, direction: ConfigChangeDirection): T {
  if (values.length === 0) {
    return current;
  }
  const currentIndex = Math.max(0, values.indexOf(current));
  const delta = direction === "next" ? 1 : -1;
  const nextIndex = (currentIndex + delta + values.length) % values.length;
  return values[nextIndex] ?? current;
}

function getModelNames(models: RuntimeModelInfo[], fallback: string): string[] {
  const names = models.map((model) => model.model_name).filter(Boolean);
  return names.length > 0 ? names : [fallback].filter(Boolean);
}

// agent_type 与 agent_mode 正交：任何执行模式都可选 react/codeact。
function getAgentTypeOptions(_agentMode: string): string[] {
  return AGENT_TYPE_OPTIONS;
}

function normalizeDisabledSkills(values: string[]): string[] {
  return [...new Set(values.map((item) => item.trim().toLowerCase()).filter(Boolean))].sort();
}

function getConfigRowOrder(draft: ConfigPanelDraft): ConfigRowId[] {
  return [...STATIC_ROW_ORDER, "agent_mode", "agent_type", "model", "model_effort"];
}

function normalizeAgentType(agentMode: string, agentType: string): string {
  const options = getAgentTypeOptions(agentMode);
  return options.includes(agentType) ? agentType : options[0] || "react";
}

export function createConfigPanelDraft(params: {
  session: SessionStatus;
  memory: MemoryStatusResponse;
  models: RuntimeModelInfo[];
  skillsConfig?: SkillsConfigStatusResponse;
  selfEvolutionEnabled?: boolean;
  graphsEnabled?: boolean;
  workspaceConfig?: WorkspaceRuntimeConfig;
}): ConfigPanelDraft {
  const runtime = params.workspaceConfig?.runtime || {};
  const modelNames = getModelNames(params.models, params.session.model_name || runtime.model_name || "");
  const selectedModelName = params.session.model_name || runtime.model_name || modelNames[0] || "";
  const selectedModel = params.models.find((model) => model.model_name === selectedModelName);
  const permissionMode = params.session.permission_mode || "default";
  const agentMode = params.session.agent_mode || runtime.agent_mode || "agent";
  const disabledSkills = new Set(normalizeDisabledSkills(params.skillsConfig?.disabled || []));
  for (const skill of params.skillsConfig?.skills || []) {
    if (!skill.enabled) {
      const key = String(skill.qualified_name || skill.name || "").trim().toLowerCase();
      if (key) disabledSkills.add(key);
    }
  }
  return {
    memoryEnabled: Boolean(params.memory.enabled),
    dreamEnabled: Boolean(params.memory.dream_enabled),
    browserEnabled: params.workspaceConfig?.browser?.enabled !== false,
    imageEnabled: params.workspaceConfig?.image?.enabled === true,
    skillsEnabled: params.skillsConfig?.enabled !== false,
    selfEvolutionEnabled: params.selfEvolutionEnabled ?? (params.workspaceConfig?.self_evolution?.enabled !== false),
    graphsEnabled: params.graphsEnabled ?? (params.workspaceConfig?.graphs?.enabled !== false),
    disabledSkills: normalizeDisabledSkills([...disabledSkills]),
    skillsCount: params.skillsConfig?.skills?.length || 0,
    permissionMode,
    agentMode,
    agentType: normalizeAgentType(agentMode, params.session.agent_type || runtime.agent_type || "react"),
    modelName: selectedModelName,
    modelEffort: normalizeEffortForModel(params.session.model_effort || runtime.model_effort, selectedModel),
  };
}

export function changeConfigDraft(
  draft: ConfigPanelDraft,
  rowId: ConfigRowId,
  direction: ConfigChangeDirection,
  models: RuntimeModelInfo[],
): ConfigPanelDraft {
  if (rowId === "memory") {
    return { ...draft, memoryEnabled: !draft.memoryEnabled };
  }
  if (rowId === "dream") {
    return { ...draft, dreamEnabled: !draft.dreamEnabled };
  }
  if (rowId === "browser") {
    return { ...draft, browserEnabled: !draft.browserEnabled };
  }
  if (rowId === "image") {
    return { ...draft, imageEnabled: !draft.imageEnabled };
  }
  if (rowId === "skills") {
    return { ...draft, skillsEnabled: !draft.skillsEnabled };
  }
  if (rowId === "self_evolution") {
    return { ...draft, selfEvolutionEnabled: !draft.selfEvolutionEnabled };
  }
  if (rowId === "graphs") {
    return { ...draft, graphsEnabled: !draft.graphsEnabled };
  }
  if (rowId === "agent_mode") {
    const agentMode = cycleValue(AGENT_MODE_OPTIONS, draft.agentMode, direction);
    return {
      ...draft,
      agentMode,
      agentType: normalizeAgentType(agentMode, draft.agentType),
    };
  }
  if (rowId === "agent_type") {
    const options = getAgentTypeOptions(draft.agentMode);
    return {
      ...draft,
      agentType: cycleValue(options, draft.agentType, direction),
    };
  }
  if (rowId === "model") {
    const modelName = cycleValue(getModelNames(models, draft.modelName), draft.modelName, direction);
    const selectedModel = models.find((model) => model.model_name === modelName);
    return {
      ...draft,
      modelName,
      modelEffort: normalizeEffortForModel(draft.modelEffort, selectedModel),
    };
  }
  const selectedModel = models.find((model) => model.model_name === draft.modelName);
  return {
    ...draft,
    modelEffort: cycleValue(getSupportedModelEfforts(selectedModel), draft.modelEffort, direction),
  };
}

export function buildConfigApplyPlan(
  initialDraft: ConfigPanelDraft,
  draft: ConfigPanelDraft,
): ConfigApplyPlan {
  const memoryChanges: ConfigApplyPlan["memoryChanges"] = [];
  const browserChanges: ConfigApplyPlan["browserChanges"] = [];
  const imageChanges: ConfigApplyPlan["imageChanges"] = [];
  if (initialDraft.memoryEnabled !== draft.memoryEnabled) {
    memoryChanges.push({ feature: "memory", enabled: draft.memoryEnabled });
  }
  if (initialDraft.dreamEnabled !== draft.dreamEnabled) {
    memoryChanges.push({ feature: "dream", enabled: draft.dreamEnabled });
  }
  if (initialDraft.browserEnabled !== draft.browserEnabled) {
    browserChanges.push({ enabled: draft.browserEnabled });
  }
  if (initialDraft.imageEnabled !== draft.imageEnabled) {
    imageChanges.push({ enabled: draft.imageEnabled });
  }
  const initialDisabled = normalizeDisabledSkills(initialDraft.disabledSkills);
  const nextDisabled = normalizeDisabledSkills(draft.disabledSkills);
  const skillsChange =
    initialDraft.skillsEnabled !== draft.skillsEnabled ||
    initialDisabled.join("\n") !== nextDisabled.join("\n")
      ? { enabled: draft.skillsEnabled, disabled: nextDisabled }
      : null;
  const selfEvolutionChange = initialDraft.selfEvolutionEnabled !== draft.selfEvolutionEnabled
    ? { enabled: draft.selfEvolutionEnabled }
    : null;
  const graphsChange = initialDraft.graphsEnabled !== draft.graphsEnabled
    ? { enabled: draft.graphsEnabled }
    : null;

  const runtimePatch: ConfigApplyPlan["runtimePatch"] = {};
  if (initialDraft.agentMode !== draft.agentMode) {
    runtimePatch.agent_mode = draft.agentMode;
  }
  if (initialDraft.agentType !== draft.agentType) {
    runtimePatch.agent_type = draft.agentType;
  }
  if (initialDraft.modelName !== draft.modelName) {
    runtimePatch.model_name = draft.modelName;
  }
  if (initialDraft.modelEffort !== draft.modelEffort) {
    runtimePatch.model_effort = draft.modelEffort;
  }

  // Changing agent_type only updates the workspace default. If the mode is
  // changed in the same save, keep the live switch on its existing protocol;
  // the new default applies to later default-Agent creation.
  const switchAgentMode =
    initialDraft.agentMode !== draft.agentMode
      ? { agent_mode: draft.agentMode, agent_type: initialDraft.agentType }
      : null;
  const switchModel =
    initialDraft.modelName !== draft.modelName || initialDraft.modelEffort !== draft.modelEffort
      ? { model_name: draft.modelName, model_effort: draft.modelEffort }
      : null;

  return {
    memoryChanges,
    browserChanges,
    imageChanges,
    skillsChange,
    selfEvolutionChange,
    graphsChange,
    runtimePatch,
    switchPermissionMode: null,
    switchAgentMode,
    switchModel,
  };
}

export function buildConfigPanelModel(params: {
  draft: ConfigPanelDraft;
  initialDraft: ConfigPanelDraft;
  models: RuntimeModelInfo[];
  selectedIndex: number;
}): ConfigPanelModel {
  const modelByName = new Map(params.models.map((model) => [model.model_name, model]));
  const rowOrder = getConfigRowOrder(params.draft);
  const selectedIndex = Math.max(0, Math.min(params.selectedIndex, rowOrder.length - 1));
  const rowsById = new Map<ConfigRowId, ConfigPanelRow>();
  const baseRows: ConfigPanelRow[] = [
    {
      id: "memory",
      label: "Workspace memory",
      value: boolLabel(params.draft.memoryEnabled),
      changed: params.draft.memoryEnabled !== params.initialDraft.memoryEnabled,
      selected: selectedIndex === 0,
    },
    {
      id: "dream",
      label: "Auto dream",
      value: boolLabel(params.draft.dreamEnabled),
      changed: params.draft.dreamEnabled !== params.initialDraft.dreamEnabled,
      selected: selectedIndex === 1,
    },
    {
      id: "browser",
      label: "Browser tools",
      value: boolLabel(params.draft.browserEnabled),
      changed: params.draft.browserEnabled !== params.initialDraft.browserEnabled,
      selected: selectedIndex === 2,
    },
    {
      id: "image",
      label: "Image tools",
      value: boolLabel(params.draft.imageEnabled),
      description: "controls image generation/editing; add_image stays available",
      changed: params.draft.imageEnabled !== params.initialDraft.imageEnabled,
      selected: selectedIndex === 3,
    },
    {
      id: "skills",
      label: "Skills",
      value: boolLabel(params.draft.skillsEnabled),
      description: params.draft.skillsCount > 0 ? `${params.draft.skillsCount} configured; use /skills for per-skill control` : "no installed skills found",
      changed: params.draft.skillsEnabled !== params.initialDraft.skillsEnabled,
      selected: false,
    },
    {
      id: "self_evolution",
      label: "Self evolution",
      value: boolLabel(params.draft.selfEvolutionEnabled),
      description: "controls Agent, Tool, Skill, and Graph management tools",
      changed: params.draft.selfEvolutionEnabled !== params.initialDraft.selfEvolutionEnabled,
      selected: false,
    },
    {
      id: "graphs",
      label: "Graphs",
      value: boolLabel(params.draft.graphsEnabled),
      description: "controls automatic graph tools; $graph:<name> remains explicit",
      changed: params.draft.graphsEnabled !== params.initialDraft.graphsEnabled,
      selected: false,
    },
    {
      id: "agent_mode",
      label: "Agent mode",
      value: params.draft.agentMode,
      description: "agent | plan | team | group",
      changed: params.draft.agentMode !== params.initialDraft.agentMode,
      selected: false,
    },
    {
      id: "agent_type",
      label: "Agent type",
      value: params.draft.agentType,
      description: "applies to built-in defaults; explicit role configs win",
      changed: params.draft.agentType !== params.initialDraft.agentType,
      selected: false,
    },
    {
      id: "model",
      label: "Model",
      value: params.draft.modelName,
      description: modelByName.get(params.draft.modelName)?.provider_model_name,
      changed: params.draft.modelName !== params.initialDraft.modelName,
      selected: false,
    },
    {
      id: "model_effort",
      label: "Model effort",
      value: params.draft.modelEffort,
      description: `choices: ${getSupportedModelEfforts(modelByName.get(params.draft.modelName)).join(", ")}`,
      changed: params.draft.modelEffort !== params.initialDraft.modelEffort,
      selected: false,
    },
  ];
  for (const row of baseRows) {
    rowsById.set(row.id, row);
  }
  const rows = rowOrder
    .map((id, index) => {
      const row = rowsById.get(id);
      return row ? { ...row, selected: index === selectedIndex } : null;
    })
    .filter((row): row is ConfigPanelRow => row !== null);

  return {
    title: "Config",
    rows,
    selectedRow: rows[selectedIndex] || null,
    helpLine: "Up/Down select · Space/Left/Right change · Enter save · Esc cancel",
  };
}

export function ConfigPanel({
  initialDraft,
  models,
  isActive = true,
  onSave,
  onCancel,
  maxRows,
}: ConfigPanelProps) {
  const [draft, setDraft] = useState(initialDraft);
  const [selectedIndex, setSelectedIndex] = useState(0);

  const model = buildConfigPanelModel({
    draft,
    initialDraft,
    models,
    selectedIndex,
  });

  const changeSelected = (direction: ConfigChangeDirection) => {
    const row = model.selectedRow;
    if (!row) {
      return;
    }
    setDraft((current) => changeConfigDraft(current, row.id, direction, models));
  };
  const rowCount = getConfigRowOrder(draft).length;

  useInput(
    (input, key) => {
      if (key.upArrow || input === "k") {
        setSelectedIndex((prev) => Math.max(0, prev - 1));
      } else if (key.downArrow || input === "j") {
        setSelectedIndex((prev) => Math.min(rowCount - 1, prev + 1));
      } else if (key.leftArrow) {
        changeSelected("previous");
      } else if (key.rightArrow || input === " ") {
        changeSelected("next");
      } else if (key.return) {
        void onSave(buildConfigApplyPlan(initialDraft, draft), draft);
      } else if (key.escape) {
        onCancel();
      }
    },
    { isActive },
  );

  // windowing: 限制可见行数
  const CONFIG_CHROME_ROWS = 5; // marginTop + title + marginBottom + help + marginBottom
  const itemBudget = Math.max(3, (maxRows ?? 999) - CONFIG_CHROME_ROWS);
  const window = pickWindowAroundSelected({
    items: model.rows,
    rowsPerItem: (r) => (r.description ? 2 : 1),
    selectedIndex,
    budgetRows: itemBudget,
  });
  const visibleRows = model.rows.slice(window.startIndex, window.endIndex + 1);

  return (
    <Box flexDirection="column" marginTop={1} marginBottom={1}>
      <Box marginBottom={1}>
        <Text color={TUI_THEME.brand.meta}>{model.title}</Text>
      </Box>

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
              {row.label.padEnd(22)}
            </Text>
            <Text color={row.changed ? TUI_THEME.state.warning : TUI_THEME.surface.text}>
              {row.value}
            </Text>
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
          {model.helpLine}
        </Text>
      </Box>
    </Box>
  );
}
