/**
 * `/config` 面板的异步加载状态机。
 *
 * 配置快照需要等待若干只读 RPC。用户在等待期间按 Esc 时，前端不能等待
 * RPC 返回才关闭，更不能让晚到的结果再次把已关闭的面板打开。generation
 * 是一次打开尝试的单调令牌：只有仍处于 loading 且令牌相同的结果能提交。
 */

export type ConfigPanelLoadPhase = "closed" | "loading" | "ready";

export interface ConfigPanelLoadState<T> {
  phase: ConfigPanelLoadPhase;
  generation: number;
  data: T | null;
}

/** 创建一个未打开面板的初始状态。 */
export function createConfigPanelLoadState<T>(): ConfigPanelLoadState<T> {
  return { phase: "closed", generation: 0, data: null };
}

/** 同步显示 loading overlay；generation 由调用方的稳定 ref 分配。 */
export function openConfigPanelLoad<T>(generation: number): ConfigPanelLoadState<T> {
  return { phase: "loading", generation, data: null };
}

/**
 * 立即关闭 loading 或 ready overlay。
 *
 * 调用方会先递增 generation，因此任何已在途的 RPC 都无法匹配这个状态。
 */
export function closeConfigPanelLoad<T>(generation: number): ConfigPanelLoadState<T> {
  return { phase: "closed", generation, data: null };
}

/**
 * 仅接受当前 loading 请求的结果；取消、失败关闭或下一次打开后的旧结果原样丢弃。
 */
export function resolveConfigPanelLoad<T>(
  state: ConfigPanelLoadState<T>,
  generation: number,
  data: T,
): ConfigPanelLoadState<T> {
  if (state.phase !== "loading" || state.generation !== generation) {
    return state;
  }
  return { phase: "ready", generation, data };
}
