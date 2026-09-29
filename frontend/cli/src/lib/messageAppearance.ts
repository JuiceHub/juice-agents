import type { MessageBlock } from "./presenter.js";
import { TUI_THEME } from "./theme.js";

// 消息块的视觉风格描述。新版引入 dotColor + useDot 两个字段:
// - useDot=true 时，TranscriptRow 会用一个 ● 圆点作为视觉锚点（对齐 claude-code）
// - useDot=false 时（如 thinking、tool result），仍使用 prefix 字符串（✻ / ⎿ 等）
// 这样既保留了 claude-code 的克制风格，也兼容现有的工具结果缩进体系。
export interface MessageAppearance {
  variant: "assistant" | "user" | "thinking" | "tool" | "tool-out" | "response";
  /** 旧字段：当 useDot 为 false 时使用的 ASCII/Unicode 前缀（含尾随空格逻辑由调用方处理）。 */
  prefix: string;
  /** 兼容字段：旧版 TranscriptRow 用来给 prefix 上色，与 dotColor 等价但语义偏窄。 */
  accentColor: string;
  /** 主体文字颜色。 */
  bodyColor: string;
  /** 是否在 prefix 位置渲染一个彩色 ● 圆点。 */
  useDot: boolean;
  /** 圆点颜色；仅在 useDot=true 时有效，与 bodyColor 解耦以便圆点比正文更醒目。 */
  dotColor: string;
  /** 是否需要"标签头"（保留给未来扩展，目前一律 false 以贴合 claude-code 风格）。 */
  showLabel: boolean;
}

export function getMessageAppearance(block: MessageBlock): MessageAppearance {
  // assistant 与 final（流末完整回答）共用金色 ● 锚点
  if (block.kind === "final" || block.kind === "assistant") {
    return {
      variant: "assistant",
      prefix: "●",
      accentColor: TUI_THEME.message.assistantAccent,
      bodyColor: TUI_THEME.message.body,
      useDot: true,
      dotColor: TUI_THEME.message.assistantAccent,
      showLabel: false,
    };
  }

  // user 提交：青色 ● 锚点
  if (block.kind === "user") {
    return {
      variant: "user",
      prefix: "●",
      accentColor: TUI_THEME.message.userAccent,
      bodyColor: TUI_THEME.message.body,
      useDot: true,
      dotColor: TUI_THEME.message.userAccent,
      showLabel: false,
    };
  }

  // thinking：保留 ✻ 符号 + dim 灰，与 claude-code 中 thinking 的视觉一致
  if (block.kind === "thinking") {
    return {
      variant: "thinking",
      prefix: "✻",
      accentColor: TUI_THEME.message.thinkingAccent,
      bodyColor: TUI_THEME.message.muted,
      useDot: false,
      dotColor: TUI_THEME.message.thinkingAccent,
      showLabel: false,
    };
  }

  // tool 调用 header：金色 ● 圆点（与 assistant 同色，因为是 assistant 的发起动作）
  if (block.kind === "tool") {
    return {
      variant: "tool",
      prefix: "●",
      accentColor: TUI_THEME.message.responseAccent,
      bodyColor: TUI_THEME.message.body,
      useDot: true,
      dotColor: TUI_THEME.message.responseAccent,
      showLabel: false,
    };
  }

  // 错误块：红色 ✗
  if (block.kind === "error") {
    return {
      variant: "response",
      prefix: "✗",
      accentColor: TUI_THEME.message.errorAccent,
      bodyColor: TUI_THEME.message.errorAccent,
      useDot: false,
      dotColor: TUI_THEME.message.errorAccent,
      showLabel: false,
    };
  }

  // system / 默认：⎿ 折角，作为附加输出（对齐 claude-code 的 tool result 风格）
  return {
    variant: "response",
    prefix: "⎿",
    accentColor: TUI_THEME.message.responseAccent,
    bodyColor: TUI_THEME.message.muted,
    useDot: false,
    dotColor: TUI_THEME.message.responseAccent,
    showLabel: false,
  };
}
