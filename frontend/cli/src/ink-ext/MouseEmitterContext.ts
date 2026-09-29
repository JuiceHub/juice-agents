/**
 * MouseEmitterContext —— 把 mouseAwareStdin 的 wheel 事件源注入 React 树。
 *
 * entry.tsx 在创建 ink instance 时把 emitter 传给 App，App 用 Provider 暴露给
 * 子树；VirtualScrollList 通过 useContext 订阅。null 表示未启用 mouse（非 TTY
 * 或测试环境），useMouseWheel 会自动退化为 no-op。
 */

import { createContext } from "react";
import type { EventEmitter } from "node:events";

export const MouseEmitterContext = createContext<EventEmitter | null>(null);
