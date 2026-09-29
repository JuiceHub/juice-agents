/**
 * Transcript 行级渲染缓存
 *
 * 性能优化：
 * - 缓存每个 block 的 RowVM[] 结果
 * - 仅在内容或终端宽度变化时重新计算
 * - LRU 策略保留最近 200 个 block
 *
 * 使用方式：
 *   const cache = new TranscriptRowCache();
 *   const rows = cache.getCachedRows(block, columns, blockKey);
 */

import type { MessageBlock as SharedMessageBlock } from "@juice-agents/shared/presenter/stream";
import type { RowVM } from "./transcriptRows.js";

type MessageBlock = SharedMessageBlock & { timestamp?: number };

interface CacheEntry {
  rows: RowVM[];
  columns: number;
  blockTimestamp: number;
  lastAccessed: number;
}

export class TranscriptRowCache {
  private cache = new Map<string, CacheEntry>();
  private maxSize = 200; // LRU 缓存上限

  /**
   * 获取缓存的行数据或重新计算
   *
   * @param block - 消息块
   * @param columns - 终端列数
   * @param blockKey - 块的唯一标识
   * @param computeFn - 计算函数（缓存未命中时调用）
   * @returns 行数据数组
   */
  getCachedRows(
    block: MessageBlock,
    columns: number,
    blockKey: string,
    computeFn: () => RowVM[]
  ): RowVM[] {
    const cacheKey = this.generateCacheKey(blockKey, columns);
    const cached = this.cache.get(cacheKey);

    // 检查缓存是否有效
    if (cached && this.isCacheValid(cached, block, columns)) {
      // 更新访问时间（LRU）
      cached.lastAccessed = Date.now();
      return cached.rows;
    }

    // 缓存未命中，重新计算
    const rows = computeFn();

    // 存入缓存
    this.cache.set(cacheKey, {
      rows,
      columns,
      blockTimestamp: block.timestamp || 0,
      lastAccessed: Date.now(),
    });

    // LRU 清理
    this.evictIfNeeded();

    return rows;
  }

  /**
   * 生成缓存键
   */
  private generateCacheKey(blockKey: string, columns: number): string {
    return `${blockKey}:${columns}`;
  }

  /**
   * 检查缓存是否有效
   */
  private isCacheValid(
    cached: CacheEntry,
    block: MessageBlock,
    columns: number
  ): boolean {
    // 终端宽度变化，缓存失效
    if (cached.columns !== columns) {
      return false;
    }

    // 块内容变化（通过 timestamp 检测），缓存失效
    const blockTimestamp = block.timestamp || 0;
    if (cached.blockTimestamp !== blockTimestamp) {
      return false;
    }

    return true;
  }

  /**
   * LRU 清理：移除最久未访问的条目
   */
  private evictIfNeeded(): void {
    if (this.cache.size <= this.maxSize) {
      return;
    }

    // 找出最久未访问的条目
    let oldestKey: string | null = null;
    let oldestTime = Infinity;

    for (const [key, entry] of this.cache.entries()) {
      if (entry.lastAccessed < oldestTime) {
        oldestTime = entry.lastAccessed;
        oldestKey = key;
      }
    }

    if (oldestKey) {
      this.cache.delete(oldestKey);
    }
  }

  /**
   * 手动清空缓存
   */
  clear(): void {
    this.cache.clear();
  }

  /**
   * 获取缓存统计信息
   */
  getStats() {
    return {
      size: this.cache.size,
      maxSize: this.maxSize,
      keys: Array.from(this.cache.keys()),
    };
  }

  /**
   * 设置缓存上限
   */
  setMaxSize(size: number): void {
    this.maxSize = size;
    this.evictIfNeeded();
  }
}

// 全局缓存实例（可选）
let globalCache: TranscriptRowCache | null = null;

export function getGlobalTranscriptCache(): TranscriptRowCache {
  if (!globalCache) {
    globalCache = new TranscriptRowCache();
  }
  return globalCache;
}

export function clearGlobalTranscriptCache(): void {
  if (globalCache) {
    globalCache.clear();
  }
}
