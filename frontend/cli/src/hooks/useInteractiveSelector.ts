/**
 * Generic interactive selector hook for the CLI.
 */

import { useState, useCallback, useRef } from "react";
import { cycleModelEffort, type ModelEffort } from "../lib/modelEffort.js";

export interface SelectorItem {
  label: string;
  value: string;
  description?: string;
  active?: boolean;
  backend?: string;
  supported_efforts?: string[];
}

export function useInteractiveSelector() {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [items, setItems] = useState<SelectorItem[]>([]);
  const [title, setTitle] = useState("");
  const [effort, setEffort] = useState<ModelEffort | undefined>(undefined);
  const [onConfirmCallback, setOnConfirmCallback] = useState<
    ((item: SelectorItem, effort?: ModelEffort) => void) | null
  >(null);
  const isOpenRef = useRef(false);
  const titleRef = useRef("");

  const open = useCallback(
    (
      newTitle: string,
      newItems: SelectorItem[],
      onConfirm: (item: SelectorItem, effort?: ModelEffort) => void,
      options: { effort?: ModelEffort } = {}
    ) => {
      setTitle(newTitle);
      titleRef.current = newTitle;
      setItems(newItems);
      setEffort(options.effort);
      setOnConfirmCallback(() => onConfirm);
      const activeIndex = newItems.findIndex((item) => item.active);
      setSelectedIndex(activeIndex >= 0 ? activeIndex : 0);
      isOpenRef.current = true;
      setIsOpen(true);
    },
    []
  );

  const close = useCallback(() => {
    isOpenRef.current = false;
    titleRef.current = "";
    setIsOpen(false);
    setItems([]);
    setEffort(undefined);
    setOnConfirmCallback(null);
    setSelectedIndex(0);
  }, []);

  const selectPrev = useCallback(() => {
    setSelectedIndex((prev) => Math.max(0, prev - 1));
  }, []);

  const selectNext = useCallback(() => {
    setSelectedIndex((prev) => {
      const maxIndex = Math.max(0, items.length - 1);
      return Math.min(prev + 1, maxIndex);
    });
  }, [items.length]);

  const updateItems = useCallback((newItems: SelectorItem[]) => {
    setItems((prevItems) => {
      setSelectedIndex((prevIndex) => {
        const selectedValue = prevItems[prevIndex]?.value;
        const preservedIndex = newItems.findIndex((item) => item.value === selectedValue);
        if (preservedIndex >= 0) {
          return preservedIndex;
        }
        const activeIndex = newItems.findIndex((item) => item.active);
        return activeIndex >= 0 ? activeIndex : 0;
      });
      return newItems;
    });
  }, []);

  const updateItemsIfOpen = useCallback((expectedTitle: string, newItems: SelectorItem[]): boolean => {
    if (!isOpenRef.current || titleRef.current !== expectedTitle) {
      return false;
    }
    updateItems(newItems);
    return true;
  }, [updateItems]);

  const confirm = useCallback(() => {
    if (onConfirmCallback && items[selectedIndex]) {
      onConfirmCallback(items[selectedIndex], effort);
    }
    close();
  }, [onConfirmCallback, items, selectedIndex, effort, close]);

  const cancel = useCallback(() => {
    close();
  }, [close]);

  const selectEffortLeft = useCallback(() => {
    setEffort((prev) => {
      if (prev === undefined) return prev;
      return cycleModelEffort(prev, "left", items[selectedIndex]);
    });
  }, [items, selectedIndex]);

  const selectEffortRight = useCallback(() => {
    setEffort((prev) => {
      if (prev === undefined) return prev;
      return cycleModelEffort(prev, "right", items[selectedIndex]);
    });
  }, [items, selectedIndex]);

  return {
    isOpen,
    selectedIndex,
    items,
    title,
    effort,
    open,
    close,
    selectPrev,
    selectNext,
    updateItems,
    updateItemsIfOpen,
    selectEffortLeft,
    selectEffortRight,
    confirm,
    cancel,
  };
}
