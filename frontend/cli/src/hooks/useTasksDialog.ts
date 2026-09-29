/**
 * useTasksDialog: 维护 async tasks 面板的 list / detail 状态。
 *
 * 不直接发 RPC——拉取任务列表与读取 output 的副作用由 app.tsx 持有，
 * 这里只负责面板自身的 UI 状态机：是否打开、选中哪条、看列表还是详情。
 */

import { useCallback, useState } from "react";

export type TasksDialogMode = "list" | "detail";

export interface TasksDialogState {
  isOpen: boolean;
  mode: TasksDialogMode;
  selectedIndex: number;
  selectedTaskId: string | null;
}

export function useTasksDialog() {
  const [state, setState] = useState<TasksDialogState>({
    isOpen: false,
    mode: "list",
    selectedIndex: 0,
    selectedTaskId: null,
  });

  const open = useCallback(() => {
    setState({ isOpen: true, mode: "list", selectedIndex: 0, selectedTaskId: null });
  }, []);

  const close = useCallback(() => {
    setState({ isOpen: false, mode: "list", selectedIndex: 0, selectedTaskId: null });
  }, []);

  const moveUp = useCallback(() => {
    setState((prev) =>
      prev.mode === "list"
        ? { ...prev, selectedIndex: Math.max(0, prev.selectedIndex - 1) }
        : prev
    );
  }, []);

  const moveDown = useCallback((maxIndex: number) => {
    setState((prev) =>
      prev.mode === "list"
        ? { ...prev, selectedIndex: Math.min(Math.max(0, maxIndex), prev.selectedIndex + 1) }
        : prev
    );
  }, []);

  const enterDetail = useCallback((taskId: string) => {
    setState((prev) => ({ ...prev, mode: "detail", selectedTaskId: taskId }));
  }, []);

  const backToList = useCallback(() => {
    setState((prev) => ({ ...prev, mode: "list", selectedTaskId: null }));
  }, []);

  /** 列表长度变化时，把选中下标钳到合法范围内。 */
  const clampSelectedIndex = useCallback((listLength: number) => {
    setState((prev) => {
      if (prev.mode !== "list") {
        return prev;
      }
      const max = Math.max(0, listLength - 1);
      if (prev.selectedIndex <= max) {
        return prev;
      }
      return { ...prev, selectedIndex: max };
    });
  }, []);

  return {
    state,
    open,
    close,
    moveUp,
    moveDown,
    enterDetail,
    backToList,
    clampSelectedIndex,
  };
}
