/**
 * VirtualScrollList —— 行级虚拟滚动列表（原版 Ink 上的纯 React 实现）。
 *
 * 核心约束（实证驱动的设计）
 * --------------------------
 * 原版 Ink 无法裁剪「部分视觉行」：overflow:hidden 在固定高度下会间隔取行，
 * marginTop 负值也会错乱。因此不能整项渲染再裁剪，必须保证 React 树输出的
 * 视觉行数恰好 == viewportHeight（再多一行就触发 Ink 的 clearTerminal 灾难）。
 *
 * 方案：调用方把内容摊平成「1 视觉行 = 1 个 RowVM」的扁平序列（见
 * transcriptRows.flattenBlocksToRows），本组件按 [scrollTop, scrollTop+
 * viewportHeight) 精确 slice 出恰好 viewportHeight 行渲染。行级粒度让滚动、
 * sticky、窗口计算都退化成简单的整数运算，无浮点、无估算偏差。
 *
 * sticky 语义
 * -----------
 * - stickyBottom 初始为 true：内容增长时自动贴底（跟随最新输出）。
 * - 用户上滚（scrollBy 离开底部）→ 解除 sticky，停在原处看历史。
 * - 用户 G / 滚回底部 → 重新 sticky。
 */

import React, { useState, useMemo, useEffect, useRef, useContext } from "react";
import { Box } from "ink";
import { useScrollKeys } from "./useScrollKeys.js";
import { useMouseWheel } from "./useMouseWheel.js";
import { MouseEmitterContext } from "./MouseEmitterContext.js";

/**
 * 计算可见窗口的纯函数（可单测）。
 *
 * @param totalRows - 全部视觉行数
 * @param viewportHeight - 视口行数
 * @param scrollTop - 当前滚动位置（顶部行下标）
 * @returns { start, end, clampedScrollTop } —— slice [start,end) 恰好 ≤ viewportHeight 行
 */
export function computeWindow(
  totalRows: number,
  viewportHeight: number,
  scrollTop: number
): { start: number; end: number; clampedScrollTop: number } {
  const vh = Math.max(0, Math.floor(viewportHeight));
  if (totalRows <= 0 || vh <= 0) {
    return { start: 0, end: 0, clampedScrollTop: 0 };
  }
  const maxScroll = Math.max(0, totalRows - vh);
  const clamped = Math.max(0, Math.min(scrollTop, maxScroll));
  const start = clamped;
  const end = Math.min(totalRows, start + vh);
  return { start, end, clampedScrollTop: clamped };
}

/**
 * 计算下一个滚动位置 + sticky 状态（纯函数，可单测）。
 *
 * @returns { scrollTop, sticky }
 */
export function applyScrollDelta(params: {
  totalRows: number;
  viewportHeight: number;
  current: number;
  delta: number;
}): { scrollTop: number; sticky: boolean } {
  const { totalRows, viewportHeight, current, delta } = params;
  const maxScroll = Math.max(0, totalRows - Math.max(0, viewportHeight));
  const next = Math.max(0, Math.min(current + delta, maxScroll));
  // 到达（或超过）底部即视为贴底。
  return { scrollTop: next, sticky: next >= maxScroll };
}

interface VirtualScrollListProps<T> {
  /** 扁平的「1 项 = 1 视觉行」序列。 */
  rows: T[];
  /** 视口高度（视觉行数）。 */
  viewportHeight: number;
  /** 渲染单行。 */
  renderRow: (row: T, index: number) => React.ReactNode;
  /** 取每行稳定 key。 */
  rowKey: (row: T, index: number) => string;
  /** 是否启用滚动键位（与 composer/overlay 互斥）。 */
  scrollActive: boolean;
  /** 初始是否贴底；缺省 true。 */
  stickyBottom?: boolean;
}

/**
 * 行级虚拟滚动列表组件。
 */
export function VirtualScrollList<T>({
  rows,
  viewportHeight,
  renderRow,
  rowKey,
  scrollActive,
  stickyBottom = true,
}: VirtualScrollListProps<T>): React.ReactElement {
  const [scrollTop, setScrollTop] = useState(0);
  const [sticky, setSticky] = useState(stickyBottom);
  const totalRows = rows.length;

  // sticky 时内容增长自动贴底。用 ref 读最新值避免把 scrollTop 塞进依赖。
  const stickyRef = useRef(sticky);
  stickyRef.current = sticky;
  useEffect(() => {
    if (stickyRef.current) {
      const maxScroll = Math.max(0, totalRows - Math.max(0, viewportHeight));
      setScrollTop(maxScroll);
    }
  }, [totalRows, viewportHeight]);

  // scrollBy 是稳定的函数引用（setScrollTop 是稳定的），用 ref 避免重复注册
  const scrollByRef = useRef((dy: number) => {
    setScrollTop((cur) => {
      const r = applyScrollDelta({ totalRows, viewportHeight, current: cur, delta: dy });
      setSticky(r.sticky);
      return r.scrollTop;
    });
  });
  // 每次 totalRows/viewportHeight 变化时更新闭包
  scrollByRef.current = (dy: number) => {
    setScrollTop((cur) => {
      const r = applyScrollDelta({ totalRows, viewportHeight, current: cur, delta: dy });
      setSticky(r.sticky);
      return r.scrollTop;
    });
  };

  const handleScrollBy = useRef({ onScrollBy: (dy: number) => scrollByRef.current(dy) }).current;

  useScrollKeys(
    {
      onScrollBy: (dy) => scrollByRef.current(dy),
      onJumpBottom: () => {
        setSticky(true);
        setScrollTop(Math.max(0, totalRows - Math.max(0, viewportHeight)));
      },
      onJumpTop: () => {
        setSticky(false);
        setScrollTop(0);
      },
    },
    { isActive: scrollActive, halfPage: Math.floor(viewportHeight / 2) }
  );

  const emitter = useContext(MouseEmitterContext);
  useMouseWheel(handleScrollBy, { isActive: scrollActive, emitter });

  const window = useMemo(
    () => computeWindow(totalRows, viewportHeight, scrollTop),
    [totalRows, viewportHeight, scrollTop]
  );

  const visible = rows.slice(window.start, window.end);

  return (
    <Box flexDirection="column" height={Math.max(0, Math.floor(viewportHeight))}>
      {visible.map((row, i) => (
        <React.Fragment key={rowKey(row, window.start + i)}>
          {renderRow(row, window.start + i)}
        </React.Fragment>
      ))}
    </Box>
  );
}
