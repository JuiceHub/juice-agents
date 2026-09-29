/**
 * useMouseWheel —— 订阅 mouseAwareStdin 的 wheel 事件并触发滚动。
 *
 * Wheel 事件从 entry.tsx 注入的 `mouseAwareStdin.emitter` 流出（通过 React
 * Context 传递；初始版本简化为读 process 全局符号挂载点）。
 *
 * 滚动幅度 v1 固定 3 行/格——与多数终端的滚轮行为一致；后续可改为可配置。
 */

import { useEffect } from "react";
import type { EventEmitter } from "node:events";
import type { WheelEvent } from "../lib/mouseAwareStdin.js";

/** 单次滚轮触发的行数。 */
export const WHEEL_LINES_PER_TICK = 3;

export interface UseMouseWheelHandlers {
  /** 相对滚动 delta（正=向下/看新内容，负=向上/看历史）。 */
  onScrollBy: (delta: number) => void;
}

export interface UseMouseWheelOptions {
  /** 仅在 isActive=true 时响应；与其他模态互斥（如 overlay 打开时让位）。 */
  isActive: boolean;
  /** mouseAwareStdin 暴露的事件源（来自 entry.tsx 注入）。 */
  emitter: EventEmitter | null;
  /** 单次滚轮的行数；缺省 WHEEL_LINES_PER_TICK。 */
  linesPerTick?: number;
}

/**
 * 注册 wheel 事件订阅。emitter 为 null 时（非 TTY、未启用 mouse 等）退化为 no-op。
 */
export function useMouseWheel(
  handlers: UseMouseWheelHandlers,
  options: UseMouseWheelOptions
): void {
  const { isActive, emitter, linesPerTick = WHEEL_LINES_PER_TICK } = options;
  useEffect(() => {
    if (!isActive || !emitter) return;
    const onWheel = (event: WheelEvent) => {
      // 滚轮向上 → 看更早内容（scrollTop 减小）→ delta 为负
      // 滚轮向下 → 看更新内容（scrollTop 增大）→ delta 为正
      const delta = event.direction === "up" ? -linesPerTick : linesPerTick;
      handlers.onScrollBy(delta);
    };
    emitter.on("wheel", onWheel);
    return () => {
      emitter.off("wheel", onWheel);
    };
  }, [isActive, emitter, linesPerTick, handlers]);
}
