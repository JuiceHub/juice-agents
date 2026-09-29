/**
 * 通用 windowing 算法：给 overlay 组件提供 viewport 滚动能力。
 *
 * 当 overlay 项数超出终端可用高度时，按 selectedIndex 居中显示部分项，
 * 并在顶部/底部添加溢出指示行（… ↑ N more / … ↓ M more）。
 */

export interface WindowResult {
  /** 窗口起始在原数组的位置 */
  startIndex: number;
  /** 窗口结束位置（inclusive） */
  endIndex: number;
  /** 顶部截断的项数 */
  topHidden: number;
  /** 底部截断的项数 */
  bottomHidden: number;
}

export interface PickWindowParams<T> {
  /** 待显示的全部项 */
  items: T[];
  /** 每项占用的视觉行数（有 description 算 2，否则 1） */
  rowsPerItem: (item: T, index: number) => number;
  /** 当前选中项的索引 */
  selectedIndex: number;
  /** 可用于显示项的行数预算（已扣除 chrome + 指示行预留） */
  budgetRows: number;
}

/**
 * 按 selectedIndex 居中选择窗口，平衡上下扩展。
 *
 * 算法：
 * 1. 初始窗口 = [selectedIndex, selectedIndex]，占用 rowsPerItem(selected) 行
 * 2. 循环：向距离 selected "更近"的方向扩展（优先平衡上下），累加 used < budget
 * 3. 若上下仍有截断，二次收缩为指示行让出 1~2 行
 * 4. 边界：budget < 单项行数时至少包含 selected
 */
export function pickWindowAroundSelected<T>(
  params: PickWindowParams<T>
): WindowResult {
  const { items, rowsPerItem, selectedIndex, budgetRows } = params;
  const N = items.length;

  if (N === 0 || selectedIndex < 0 || selectedIndex >= N) {
    return { startIndex: 0, endIndex: -1, topHidden: 0, bottomHidden: 0 };
  }

  const budget = Math.max(1, Math.floor(budgetRows));

  // 初始：窗口只包含 selected
  let lo = selectedIndex;
  let hi = selectedIndex;
  let used = rowsPerItem(items[selectedIndex], selectedIndex);

  // 向上下扩展，优先平衡（离 selected 更远的方向先扩）
  while (used < budget && (lo > 0 || hi < N - 1)) {
    const canUp = lo > 0;
    const canDown = hi < N - 1;
    const distUp = selectedIndex - lo;
    const distDown = hi - selectedIndex;

    // 优先扩展"距离 selected 更近"的方向，保持窗口居中
    if (canDown && (!canUp || distDown <= distUp)) {
      const nextRows = rowsPerItem(items[hi + 1], hi + 1);
      if (used + nextRows > budget) break;
      used += nextRows;
      hi += 1;
    } else if (canUp) {
      const nextRows = rowsPerItem(items[lo - 1], lo - 1);
      if (used + nextRows > budget) break;
      used += nextRows;
      lo -= 1;
    } else {
      break;
    }
  }

  const topHidden = lo;
  const bottomHidden = N - 1 - hi;

  // 若有截断，为指示行预留空间（topHidden > 0 需要 1 行，bottomHidden > 0 需要 1 行）
  const indicatorRows = (topHidden > 0 ? 1 : 0) + (bottomHidden > 0 ? 1 : 0);
  if (indicatorRows > 0 && used + indicatorRows > budget) {
    // 二次收缩：从远离 selected 的一侧削减项
    while (used + indicatorRows > budget && hi > lo) {
      const distUp = selectedIndex - lo;
      const distDown = hi - selectedIndex;
      if (distDown >= distUp) {
        used -= rowsPerItem(items[hi], hi);
        hi -= 1;
      } else {
        used -= rowsPerItem(items[lo], lo);
        lo += 1;
      }
    }
  }

  return {
    startIndex: lo,
    endIndex: hi,
    topHidden: lo,
    bottomHidden: N - 1 - hi,
  };
}
