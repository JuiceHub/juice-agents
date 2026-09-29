/**
 * Divider —— 横向分隔线。
 *
 * 两种形态:
 * - 不带 label: 一整行 ─ 占满可用宽度（受父级 Box 控制）
 * - 带 label: ── label ── 居左/居中放置（默认居左以贴合终端阅读习惯）
 *
 * 颜色默认走 surface.muted，与 unread divider 视觉分离: unread divider 用 brand
 * meta 色突出"新消息"语义；普通 Divider 用低对比的灰色，仅做空间分隔。
 */

import React from "react";
import { Box, Text } from "ink";

import { TUI_THEME } from "../../lib/theme.js";

interface DividerProps {
  /** 可选标签；为空时渲染纯线条。 */
  label?: string;
  /** 线条颜色，默认 surface.muted。 */
  color?: string;
  /** 字符；默认 "─"，可改成 "·" 等做不同观感。 */
  char?: string;
  /** 标签前后的固定线长（label 模式下使用）。 */
  flankLength?: number;
}

export function Divider({
  label,
  color = TUI_THEME.surface.muted,
  char = "─",
  flankLength = 2,
}: DividerProps) {
  if (!label) {
    // 纯线条: Ink 不能直接 100% 宽度铺满（终端宽度未知时），所以给一个保守的 24 字符
    // 长度，配合父容器 Box 的 width 设置即可看起来像满宽分隔线。
    return (
      <Box>
        <Text color={color}>{char.repeat(24)}</Text>
      </Box>
    );
  }

  const flank = char.repeat(Math.max(1, flankLength));
  return (
    <Box>
      <Text color={color}>
        {flank} {label} {flank}
      </Text>
    </Box>
  );
}
