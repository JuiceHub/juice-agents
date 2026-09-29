/**
 * useScrollKeys —— 把键盘输入翻译成滚动意图。
 *
 * 仅在 isActive 时监听（与 composer/overlay 输入互斥，由调用方协调）。
 * v1 只做键盘滚动；mouse wheel（SGR mouse tracking）留待 v2。
 *
 * 键位（对齐 less / vim / claude-code 习惯）：
 *   j / ↓         下滚 1 行
 *   k / ↑         上滚 1 行
 *   Ctrl+D / PgDn 下滚半屏
 *   Ctrl+U / PgUp 上滚半屏
 *   g             跳到顶
 *   G             跳到底
 *
 * 按键 → 意图 的映射抽成纯函数 resolveScrollAction，便于单测（不依赖 Ink 渲染）。
 */

import { useInput, type Key } from "ink";

/** 滚动意图。delta 为相对行数；jump 为绝对跳转。 */
export type ScrollAction =
  | { type: "by"; delta: number }
  | { type: "top" }
  | { type: "bottom" }
  | null;

/**
 * 把一次按键解析成滚动意图（纯函数）。无匹配返回 null。
 *
 * navOnly=true（v1 默认）：只响应 PgUp/PgDn——这两个键 composer 不消费，可与文本
 * 输入共存（Ink useInput 广播给所有监听器，j/k/方向键会被 composer 同时当文本处理
 * 而冲突）。navOnly=false：额外启用 j/k/方向键/g/G/Ctrl+U/D（用于 composer 让出的
 * transcript-only 模式，v2）。
 *
 * @param input - ink useInput 的 input 字符串
 * @param key - ink useInput 的 key 标志
 * @param halfPage - 半屏行数（≥1）
 * @param navOnly - 仅启用与文本输入无冲突的键（PgUp/PgDn）
 */
export function resolveScrollAction(
  input: string,
  key: Pick<Key, "downArrow" | "upArrow" | "pageDown" | "pageUp" | "ctrl">,
  halfPage: number,
  navOnly = true
): ScrollAction {
  const half = Math.max(1, Math.floor(halfPage));
  // 与文本输入无冲突的键：始终启用。
  if (key.pageDown) return { type: "by", delta: half };
  if (key.pageUp) return { type: "by", delta: -half };
  if (navOnly) return null;
  // 以下键会与 composer 文本输入冲突，仅在 composer 让出时（navOnly=false）启用。
  if (input === "j" || key.downArrow) return { type: "by", delta: 1 };
  if (input === "k" || key.upArrow) return { type: "by", delta: -1 };
  if (key.ctrl && input === "d") return { type: "by", delta: half };
  if (key.ctrl && input === "u") return { type: "by", delta: -half };
  if (input === "g") return { type: "top" };
  if (input === "G") return { type: "bottom" };
  return null;
}

export interface ScrollKeyHandlers {
  /** 相对滚动 dy 行（正 = 向下/看更新内容，负 = 向上/看更旧内容）。 */
  onScrollBy: (dy: number) => void;
  /** 跳到底部（贴底 sticky）。 */
  onJumpBottom: () => void;
  /** 跳到顶部。 */
  onJumpTop: () => void;
}

export interface UseScrollKeysOptions {
  /** 仅在 true 时拦截按键。 */
  isActive: boolean;
  /** 半屏行数（PgUp/PgDn / Ctrl+U/D 用）；通常 = floor(viewportHeight/2)。 */
  halfPage: number;
  /** 仅启用与文本输入无冲突的键（PgUp/PgDn）；默认 true。 */
  navOnly?: boolean;
}

/**
 * 注册滚动键位监听（副作用 hook）。
 */
export function useScrollKeys(
  handlers: ScrollKeyHandlers,
  options: UseScrollKeysOptions
): void {
  const { isActive, halfPage, navOnly = true } = options;

  useInput(
    (input, key) => {
      const action = resolveScrollAction(input, key, halfPage, navOnly);
      if (!action) return;
      if (action.type === "by") handlers.onScrollBy(action.delta);
      else if (action.type === "top") handlers.onJumpTop();
      else if (action.type === "bottom") handlers.onJumpBottom();
    },
    { isActive }
  );
}
