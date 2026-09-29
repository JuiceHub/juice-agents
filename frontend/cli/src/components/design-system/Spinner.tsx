/**
 * Spinner —— 单行旋转字符 + 可选 shimmer 扫光。
 *
 * 设计取自 claude-code/src/components/Spinner.tsx 的核心思路，但做了大幅精简:
 * - 去掉 teammate spinner tree、token budget、stalled detection 等高级功能
 * - 只保留两件事: ① 旋转字符 ② shimmer 扫光（在文字上从左向右移动一道亮色）
 *
 * 静默策略: 当 process.env.JUICE_NO_ANIMATION="1" 时，整个 spinner 退化为静态 ●
 * 字符，便于在 CI/日志/截屏场景下保持终端输出干净，且兼容 reduced-motion 用户偏好。
 */

import React, { useEffect, useState } from "react";
import { Text } from "ink";

import { TUI_THEME } from "../../lib/theme.js";

// 经典的 dots 动画帧（Braille pattern），与 claude-code、ora 等流行实现一致
const SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
const SPINNER_INTERVAL_MS = 80;
const SHIMMER_INTERVAL_MS = 120;

function isReducedMotion(): boolean {
  return process.env.JUICE_NO_ANIMATION === "1";
}

interface SpinnerProps {
  /** 旋转字符颜色，默认走品牌金色。 */
  color?: string;
}

/**
 * 纯旋转字符。供 LoadingState/CurrentWorkStatus 等组件嵌入。
 */
export function Spinner({ color = TUI_THEME.brand.wordmark }: SpinnerProps) {
  const reducedMotion = isReducedMotion();
  const [frameIndex, setFrameIndex] = useState(0);

  useEffect(() => {
    if (reducedMotion) return;
    const timer = setInterval(() => {
      setFrameIndex((prev) => (prev + 1) % SPINNER_FRAMES.length);
    }, SPINNER_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [reducedMotion]);

  if (reducedMotion) {
    return <Text color={color}>●</Text>;
  }
  return <Text color={color}>{SPINNER_FRAMES[frameIndex]}</Text>;
}

interface ShimmerTextProps {
  /** 要显示的文本；shimmer 会在字符上扫过。 */
  text: string;
  /** 文本基础颜色。 */
  baseColor?: string;
  /** 扫光命中字符时使用的高光色。 */
  highlightColor?: string;
  /** 是否加粗。 */
  bold?: boolean;
}

/**
 * 在一段文本上做"扫光"动画。算法: 维护一个 cursor 索引，按字符宽度切片，
 * 命中区间染成 highlightColor，其余字符保持 baseColor。reduced-motion 模式下
 * 直接返回静态文本，无任何 setInterval。
 */
export function ShimmerText({
  text,
  baseColor = TUI_THEME.surface.text,
  highlightColor = TUI_THEME.shimmer.assistant,
  bold = false,
}: ShimmerTextProps) {
  const reducedMotion = isReducedMotion();
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    if (reducedMotion) return;
    // shimmer 周期: 文字长度 + 一段后摆，这样高亮会"完整地穿过去再消失"，循环更自然
    const period = Math.max(8, text.length + 6);
    const timer = setInterval(() => {
      setCursor((prev) => (prev + 1) % period);
    }, SHIMMER_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [reducedMotion, text.length]);

  if (reducedMotion || text.length === 0) {
    return (
      <Text color={baseColor} bold={bold}>
        {text}
      </Text>
    );
  }

  // 高亮窗口宽度: 3 个字符，让光斑明显但不喧宾夺主
  const highlightWidth = 3;
  const before = text.slice(0, Math.max(0, cursor));
  const middle = text.slice(
    Math.max(0, cursor),
    Math.max(0, cursor + highlightWidth)
  );
  const after = text.slice(Math.max(0, cursor + highlightWidth));

  return (
    <Text bold={bold}>
      <Text color={baseColor}>{before}</Text>
      <Text color={highlightColor} bold>
        {middle}
      </Text>
      <Text color={baseColor}>{after}</Text>
    </Text>
  );
}
