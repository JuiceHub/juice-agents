/**
 * AlternateScreen —— 让 Ink 应用运行在终端的备用屏幕缓冲区（DEC 1049）。
 *
 * 为什么用 alt-screen
 * -------------------
 * 旧架构用 useStdout().write 直写终端原生 scrollback，与 Ink 的 log-update 动态
 * 区两套机制并存，因 Ink 内部 lastOutput 缓存（32ms throttle）滞后而产生「内容
 * 重复 + 滚动条上滑」竞态（实证）。
 *
 * alt-screen 把整个 App 隔离到备用缓冲区：
 *   - 进入时发 DEC 1049（保存主屏 + 切到备用屏）并清屏 + home
 *   - 退出时发 DEC 1049l 恢复主屏，主屏内容完整无残留
 *
 * Ink 只剩 log-update 单一渲染管线，竞态从根上消失。历史回看改为应用内滚动
 * （见 VirtualScrollList + useScrollKeys）。与 claude-code / codex 同款。
 *
 * 关于高度约束（重要）
 * --------------------
 * 本组件**不**固定根 Box 高度为终端 rows。原因：原版 Ink 在
 * `outputHeight >= stdout.rows` 时会触发 clearTerminal 灾难分支（claude-code
 * 的 ink fork 改写了该分支，原版未改）。若根 Box 撑满 rows 行，outputHeight 恰
 * 等于 rows 即触发。因此高度约束交由调用方的 computeOverlayBudget 保证：动态区
 * 各区块固定高度之和 ≤ terminalRows - guard。alt-screen 额外提供「即使内容意外
 * 超出，clearTerminal 也只擦备用屏、不碰主屏 scrollback」的安全兜底。
 *
 * 清理保证
 * --------
 * useInsertionEffect 的 cleanup 在卸载时同步发 DEC 1049l；entry.tsx 另需在
 * process 退出信号兜底发同序列，避免异常退出残留备用屏。
 */

import React, { type PropsWithChildren, useInsertionEffect } from "react";
import { Box, useStdout } from "ink";
import {
  ENABLE_MOUSE_TRACKING,
  DISABLE_MOUSE_TRACKING,
} from "../lib/mouseAwareStdin.js";

/** 进入备用屏 + 清屏 + 光标 home。 */
export const ENTER_ALT_SCREEN = "\x1b[?1049h\x1b[2J\x1b[H";
/** 退出备用屏（恢复主屏）。 */
export const EXIT_ALT_SCREEN = "\x1b[?1049l";

interface AlternateScreenProps {
  /** 保留参数位以兼容测试注入；当前不参与布局（高度由 budget 控制）。 */
  rows?: number;
  /**
   * 是否启用 SGR mouse tracking（DEC 1000+1006），用于鼠标滚轮滚动 transcript。
   * 必须配合 entry.tsx 注入的 mouseAwareStdin 使用，否则字节会泄漏到 ink useInput。
   * 缺省 true。
   */
  enableMouseTracking?: boolean;
}

/**
 * 把 children 运行于 alt-screen。根容器不固定高度，由子树（budget）自行约束。
 */
export function AlternateScreen({
  children,
  enableMouseTracking = true,
}: PropsWithChildren<AlternateScreenProps>): React.ReactElement {
  const { stdout } = useStdout();

  // useInsertionEffect（非 useLayoutEffect）：在 mutation 阶段、Ink 的
  // resetAfterCommit→onRender 之前发 ENTER_ALT_SCREEN，确保第一帧就画在备用屏，
  // 不会有半帧泄漏到主屏。cleanup 同步发 EXIT_ALT_SCREEN 恢复主屏。
  // mouse tracking 也在此进出：与 alt-screen 严格配对，进入失败/异常退出都不会残留。
  useInsertionEffect(() => {
    stdout.write(ENTER_ALT_SCREEN);
    if (enableMouseTracking) stdout.write(ENABLE_MOUSE_TRACKING);
    return () => {
      if (enableMouseTracking) stdout.write(DISABLE_MOUSE_TRACKING);
      stdout.write(EXIT_ALT_SCREEN);
    };
  }, [stdout, enableMouseTracking]);

  return (
    <Box flexDirection="column" width="100%" flexShrink={0}>
      {children}
    </Box>
  );
}
