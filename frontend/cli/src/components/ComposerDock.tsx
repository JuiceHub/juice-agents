import React from "react";
import { Box, Text } from "ink";
import type { CandidateItem } from "../hooks/useCompletion.js";
import { getCandidatePanelModel, CompletionOverlay } from "./CandidatePanel.js";
import { RichComposer } from "./RichComposer.js";
import type { ComposerController } from "../lib/composer.js";
import { TUI_COPY, TUI_THEME } from "../lib/theme.js";

export interface ComposerDockModel {
  ghostText: string | null;
  inlineHint: string | null;
  overlayVisible: boolean;
  overlayItems: Array<CandidateItem & { isSelected: boolean }>;
  modeLine: string;
  transientHint: string | null;
}

export function buildComposerDockModel(params: {
  permissionMode: string;
  pendingMode?: string | null;
  agentMode: string;
  value: string;
  disabled: boolean;
  loading: boolean;
  candidates: CandidateItem[];
  selectedIndex: number;
  completionVisible: boolean;
  ghostText: string | null;
  inlineHint: string | null;
  actorViewHint?: string | null;
}): ComposerDockModel {
  const overlay = getCandidatePanelModel({
    candidates: params.candidates,
    selectedIndex: params.selectedIndex,
    visible: params.completionVisible,
  });

  const permissionMode = (params.permissionMode || "default").trim() || "default";
  const pendingMode = (params.pendingMode || "").trim();
  const agentMode = (params.agentMode || "agent").trim() || "agent";

  // 在 append-only 模型下不再有「未读消息」概念——所有完成的消息已经追加到
  // 终端 scrollback，鼠标向上即可回看。loading 与 actorViewHint 仍走 transientHint。
  let transientHint: string | null = null;
  if (params.loading) {
    transientHint = TUI_COPY.booting;
  } else if (params.actorViewHint) {
    transientHint = params.actorViewHint;
  }

  return {
    ghostText: params.ghostText,
    inlineHint: params.inlineHint,
    overlayVisible: overlay.visible,
    overlayItems: overlay.items,
    // 两个维度并列展示，agent mode 在前、permission mode 在后。
    // pendingMode 只属于 permission 维度，因此只在后半段出现。
    modeLine:
      pendingMode && pendingMode !== permissionMode
        ? `${agentMode} · ${permissionMode} → ${pendingMode} pending`
        : `${agentMode} · ${permissionMode}`,
    transientHint,
  };
}

interface ComposerDockProps {
  permissionMode: string;
  pendingMode?: string | null;
  agentMode: string;
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
  isInputActive: boolean;
  loading: boolean;
  candidates: CandidateItem[];
  selectedIndex: number;
  completionVisible: boolean;
  ghostText: string | null;
  inlineHint: string | null;
  actorViewHint?: string | null;
  /** active 异步后台任务数；>0 才显示 mode 行右侧的徽标。 */
  asyncRunningCount: number;
}

export function ComposerDock(props: ComposerDockProps) {
  const model = buildComposerDockModel({
    permissionMode: props.permissionMode,
    pendingMode: props.pendingMode,
    agentMode: props.agentMode,
    value: props.value,
    disabled: props.disabled,
    loading: props.loading,
    candidates: props.candidates,
    selectedIndex: props.selectedIndex,
    completionVisible: props.completionVisible,
    ghostText: props.ghostText,
    inlineHint: props.inlineHint,
    actorViewHint: props.actorViewHint,
  });

  return (
    <Box flexDirection="column">
      <RichComposer
        value={props.value}
        onChange={props.onChange}
        cursor={props.cursor}
        onCursorChange={props.onCursorChange}
        controller={props.controller}
        onControllerChange={props.onControllerChange}
        onSubmit={props.onSubmit}
        onTab={props.onTab}
        onCycleMode={props.onCycleMode}
        onEscape={props.onEscape}
        onExit={props.onExit}
        onToggleActors={props.onToggleActors}
        onToggleTasks={props.onToggleTasks}
        onCandidateUp={props.onCandidateUp}
        onCandidateDown={props.onCandidateDown}
        disabled={props.disabled}
        isActive={props.isInputActive}
        candidatesVisible={props.completionVisible}
        ghostText={model.ghostText}
        inlineHint={model.inlineHint}
      />

      <Box marginTop={1}>
        <Text color={TUI_THEME.state.warning}>
          ● {model.modeLine}
        </Text>
        {props.asyncRunningCount > 0 ? (
          <Text color={TUI_THEME.brand.glow}>
            {" "}· {props.asyncRunningCount} bg task{props.asyncRunningCount === 1 ? "" : "s"}
          </Text>
        ) : null}
      </Box>

      {model.overlayVisible ? (
        <CompletionOverlay
          candidates={props.candidates}
          selectedIndex={props.selectedIndex}
          visible={props.completionVisible}
        />
      ) : null}

      {model.transientHint ? (
        <Text color={TUI_THEME.surface.muted}>
          {model.transientHint}
        </Text>
      ) : null}
    </Box>
  );
}
