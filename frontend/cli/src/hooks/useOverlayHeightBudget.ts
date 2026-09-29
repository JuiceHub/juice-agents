/**
 * Overlay 高度预算计算：统一分配动态区的 overlay / transcript 空间。
 *
 * 保证动态区总高度 ≤ terminalRows - guard。alt-screen 架构下 guard 还防止
 * outputHeight ≥ rows 触发 Ink 的 clearTerminal 灾难分支（原版 Ink 在 ≥ 时
 * 触发，claude-code 的 ink fork 改写了该分支，原版未改——见 ink-ext/AlternateScreen）。
 */

export const TERMINAL_SCROLLBACK_GUARD_ROWS = 1;
export const COMPOSER_ROWS = 4;
export const STATUS_ROWS = 1;
/** overlay 打开时允许 transcript 区完全让位。 */
export const MIN_TRANSCRIPT_ROWS = 0;
export const MIN_OVERLAY_ROWS = 5; // 极小终端至少留 5 行给 overlay (title + 1 项 + help)
export const DEFAULT_TRANSCRIPT_ROWS = 12;

export interface OverlayBudget {
  /** overlay 包括 chrome 的最大行数 */
  overlayMaxRows: number;
  /** transcript 区（VirtualScrollList）的 viewportHeight */
  transcriptMaxRows: number;
  /** overlay 打开时 composer 是否可见 */
  composerVisible: boolean;
}

export interface OverlayBudgetParams {
  /** 终端总行数（undefined 时使用保守默认） */
  terminalRows?: number;
  /** 是否有 overlay 打开 */
  hasOverlay: boolean;
  /** 是否有 composer（inputLayer.composerActive） */
  hasComposer: boolean;
  /** queuedMessages 等额外占用的行数 */
  reservedExtraRows: number;
}

/**
 * 计算动态区预算分配。
 *
 * 约束：overlayMaxRows + transcriptMaxRows + statusRows + queueRows
 *       + (composerVisible ? composerRows : 0) + guard ≤ terminalRows
 */
export function computeOverlayBudget(
  params: OverlayBudgetParams
): OverlayBudget {
  const { terminalRows, hasOverlay, hasComposer, reservedExtraRows } = params;

  // 无终端信息时返回保守默认（不裁剪）
  if (!terminalRows || terminalRows <= 0) {
    return {
      overlayMaxRows: hasOverlay ? 999 : 0,
      transcriptMaxRows: DEFAULT_TRANSCRIPT_ROWS,
      composerVisible: hasComposer,
    };
  }

  // overlay 打开时 composer 隐藏，不占用动态区高度
  const composerVisible = hasOverlay ? false : hasComposer;
  const reserved = STATUS_ROWS + reservedExtraRows + TERMINAL_SCROLLBACK_GUARD_ROWS;

  // 可用于 overlay + transcript 的总行数
  const usable = Math.max(
    0,
    terminalRows - reserved - (composerVisible ? COMPOSER_ROWS : 0)
  );

  // 无 overlay：transcript 占满可用空间
  if (!hasOverlay) {
    return {
      overlayMaxRows: 0,
      transcriptMaxRows: usable,
      composerVisible,
    };
  }

  // 有 overlay：优先把空间分给 overlay，transcript 让位
  const overlayMax = Math.max(MIN_OVERLAY_ROWS, usable - MIN_TRANSCRIPT_ROWS);
  const transcriptMax = Math.max(0, usable - overlayMax);

  return {
    overlayMaxRows: overlayMax,
    transcriptMaxRows: transcriptMax,
    composerVisible, // false（overlay 打开时）
  };
}
