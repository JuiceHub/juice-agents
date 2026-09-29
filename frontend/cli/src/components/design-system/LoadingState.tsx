/**
 * LoadingState —— Spinner + 主消息 + 可选副标题。
 *
 * 用于占据 1-2 行的 "正在工作" 状态展示，是 CurrentWorkStatus 的视觉骨架。
 * 设计参考 claude-code/src/components/design-system/LoadingState.tsx，差异:
 * - 主消息支持 shimmer 扫光（默认开启），让长时间等待时不至于看起来像卡死
 * - subtitle 走 dim 灰色，承担辅助信息（例如当前 stage、token 数等）
 */

import React from "react";
import { Box, Text } from "ink";

import { TUI_THEME } from "../../lib/theme.js";
import { ShimmerText, Spinner } from "./Spinner.js";

interface LoadingStateProps {
  /** 主标题，显示在 spinner 后；建议是动作性短句（"Thinking..."、"Reading file..."）。 */
  message: string;
  /** 副标题，显示在 message 下一行；通常是次要状态（"2/5 done"）。 */
  subtitle?: string;
  /** 是否加粗 message。 */
  bold?: boolean;
  /** 是否对 message 启用 shimmer 扫光。默认 true；纯静态等待场景可关闭。 */
  shimmer?: boolean;
  /** Spinner 颜色覆盖；默认走品牌金。 */
  spinnerColor?: string;
}

export function LoadingState({
  message,
  subtitle,
  bold = false,
  shimmer = true,
  spinnerColor,
}: LoadingStateProps) {
  return (
    <Box flexDirection="column">
      <Box flexDirection="row">
        <Spinner color={spinnerColor} />
        <Text> </Text>
        {shimmer ? (
          <ShimmerText
            text={message}
            baseColor={TUI_THEME.surface.text}
            highlightColor={TUI_THEME.shimmer.assistant}
            bold={bold}
          />
        ) : (
          <Text color={TUI_THEME.surface.text} bold={bold}>
            {message}
          </Text>
        )}
      </Box>
      {subtitle ? (
        <Text color={TUI_THEME.surface.muted} dimColor>
          {"  "}
          {subtitle}
        </Text>
      ) : null}
    </Box>
  );
}
