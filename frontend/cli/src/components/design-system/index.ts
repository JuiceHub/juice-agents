/**
 * design-system —— 视觉原语 barrel export。
 *
 * 所有 CLI 组件请优先从这里导入 StatusIcon/Spinner/LoadingState/Divider 等基础
 * 元素，避免散落的 ad-hoc 写法导致颜色/符号不一致。新原语先在这里实现并 export，
 * 上层组件再消费。
 */

export { StatusIcon, getStatusIconChar } from "./StatusIcon.js";
export type { StatusKind } from "./StatusIcon.js";

export { Spinner, ShimmerText } from "./Spinner.js";

export { LoadingState } from "./LoadingState.js";

export { Divider } from "./Divider.js";
