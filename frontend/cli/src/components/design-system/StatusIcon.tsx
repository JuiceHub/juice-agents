/**
 * StatusIcon —— 语义化状态图标。
 *
 * 设计参考: claude-code/src/components/design-system/StatusIcon.tsx。
 * 我们沿用相同的 6 个状态分类，但颜色取自 Juice 的 TUI_THEME.status，与项目的
 * 土金色调保持一致。`figures` 库为我们处理跨平台符号回退（Windows 控制台等）。
 */

import figures from "figures";
import React from "react";
import { Text } from "ink";

import { TUI_THEME } from "../../lib/theme.js";

export type StatusKind =
  | "success"
  | "error"
  | "warning"
  | "info"
  | "pending"
  | "running";

interface StatusConfig {
  icon: string;
  color: string | undefined;
  /** 是否走 dim 灰显（pending 用，避免抢眼）。 */
  dim: boolean;
}

const STATUS_CONFIG: Record<StatusKind, StatusConfig> = {
  success: { icon: figures.tick, color: TUI_THEME.status.success, dim: false },
  error: { icon: figures.cross, color: TUI_THEME.status.error, dim: false },
  warning: { icon: figures.warning, color: TUI_THEME.status.warning, dim: false },
  info: { icon: figures.info, color: TUI_THEME.status.info, dim: false },
  pending: { icon: figures.circle, color: TUI_THEME.status.pending, dim: true },
  running: { icon: "…", color: TUI_THEME.status.running, dim: false },
};

interface StatusIconProps {
  status: StatusKind;
  /** 是否在图标后追加一个空格，方便和后续文本拼接。 */
  withSpace?: boolean;
}

export function StatusIcon({ status, withSpace = false }: StatusIconProps) {
  const config = STATUS_CONFIG[status];
  return (
    <Text color={config.color} dimColor={config.dim}>
      {config.icon}
      {withSpace ? " " : ""}
    </Text>
  );
}

/** 暴露纯字符（不带 React 包装），便于 layout 计算宽度。 */
export function getStatusIconChar(status: StatusKind): string {
  return STATUS_CONFIG[status].icon;
}
