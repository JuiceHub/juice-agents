/**
 * Presenter helpers for the TypeScript + Ink CLI.
 *
 * These helpers keep display concerns out of the components so layout changes
 * stay easy to test and reason about.
 */

import type {
  ActionStep,
  ActorSessionSnapshot,
  RunnerStreamEvent,
  SessionStatus,
} from "@juice-agents/shared/gateway/types";
import {
  INTERRUPT_MESSAGE,
  presentActorSession as presentSharedActorSession,
  presentStreamEvent as presentSharedStreamEvent,
  presentStreamStep as presentSharedStreamStep,
  type MessageBlock as SharedMessageBlock,
} from "@juice-agents/shared/presenter/stream";

export { INTERRUPT_MESSAGE };
import { TUI_COPY } from "./theme.js";

export {
  formatDisplayAsText,
  messageBlockToText,
  presentAvailableAgents,
  presentTeamManifest,
  presentTeams,
  presentCommandHelp,
  presentCronCreated,
  presentCronDelete,
  presentCronStatus,
  presentCronTasks,
  presentDreamReceipt,
  presentModeResources,
  presentGoal,
  presentMemorySearch,
  presentMemoryStatus,
  presentMemoryView,
  presentModels,
  presentAgentTypeConfigSaved,
  presentPendingStatus,
  presentPlugins,
  presentSessions,
  presentSkillView,
  presentSkills,
  presentStatus,
  presentWorktree,
  presentWorktrees,
} from "@juice-agents/shared/presenter/command";

export interface MessageBlock extends SharedMessageBlock {
  tone?: "hero" | "primary" | "normal" | "muted" | "danger";
}

export interface IntroPanelModel {
  wordmark: string;
  bannerLines: string[];
  tagline: string;
  metaLines: string[];
}

export function presentStreamStep(step: ActionStep): MessageBlock[] {
  return presentSharedStreamStep(step as any) as MessageBlock[];
}

export function presentStreamEvent(event: RunnerStreamEvent): MessageBlock[] {
  return presentSharedStreamEvent(event as any) as MessageBlock[];
}

export function presentActorSession(actor: ActorSessionSnapshot | null | undefined): MessageBlock[] {
  return presentSharedActorSession(actor as any) as MessageBlock[];
}

export function formatModeLabel(status: SessionStatus): string {
  // agent/plan 的 protocol 标签对用户最有辨识度；Team/Group 仍沿用同一默认值。
  const agentMode = status.agent_mode || "agent";
  const label = `${agentMode}/${status.permission_mode}`;
  return agentMode === "agent" || agentMode === "plan"
    ? `${label}/${status.agent_type}`
    : label;
}

export function buildIntroPanel(status: SessionStatus): IntroPanelModel {
  const modelName = status.model_name || TUI_COPY.unknownModel;
  const modelEffort = status.model_effort || "disabled";
  const metaLines = [status.base_dir, `${modelName} · ${modelEffort}`];

  // accept 会跳过所有写操作审批。它现在可以跨会话持久化，因此必须显式提示，
  // 避免用户以为自己仍在 default 却实际不再收到任何确认。
  if (status.permission_mode === "accept") {
    metaLines.push(TUI_COPY.acceptModeNotice);
  }

  return {
    wordmark: TUI_COPY.welcomeWordmark,
    bannerLines: [...TUI_COPY.welcomeBannerLines],
    tagline: TUI_COPY.welcomeTagline,
    metaLines,
  };
}

// 决定 IntroPanel 是否应当渲染。
// runtimeReady 在 init() 完成（成功/失败均算）后置为 true，否则首屏会用空 pendingRuntime
// 渲染出 "unknown-model · disabled" 的占位字样，而不是真实的 workspace 配置。
export function shouldShowIntroPanel(params: {
  introPanel: IntroPanelModel | null | undefined;
  activeActorName: string | null;
  runtimeReady: boolean;
}): boolean {
  return Boolean(params.introPanel) && params.activeActorName === null && params.runtimeReady;
}
