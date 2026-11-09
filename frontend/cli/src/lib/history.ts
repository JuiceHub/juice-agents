/** Persistent CLI input history shared by all workspaces and Runner processes. */
import { closeSync, fstatSync, mkdirSync, openSync, readFileSync, readSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { pushHistory, submitDraft } from "./composer.js";

function defaultHistoryPath(): string {
  return join(homedir(), ".juice", "cli_history.jsonl");
}

/** Read complete JSONL records; a damaged or partial record must not hide others. */
export function readHistory(filePath = defaultHistoryPath()): string[] | null {
  let contents: string;
  try {
    contents = readFileSync(filePath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    console.error("Failed to read CLI input history:", error);
    return null;
  }

  const history: string[] = [];
  // Ignore the unfinished final line if another process is still writing it.
  for (const line of contents.split("\n").slice(0, -1)) {
    try {
      const entry: unknown = JSON.parse(line);
      const normalized = typeof entry === "string" ? submitDraft(entry) : null;
      if (normalized && history[history.length - 1] !== normalized) history.push(normalized);
    } catch {
      // Skip damaged records without discarding the remaining history.
    }
  }
  return history;
}

/** Append one JSON record in a single write, including embedded newlines. */
export function appendHistory(submitted: string, filePath = defaultHistoryPath()): boolean {
  const normalized = submitDraft(submitted);
  if (!normalized) return true;
  let file: number | undefined;
  try {
    mkdirSync(dirname(filePath), { recursive: true, mode: 0o700 });
    file = openSync(filePath, "a+", 0o600);
    const size = fstatSync(file).size;
    const lastByte = Buffer.alloc(1);
    if (size > 0) readSync(file, lastByte, 0, 1, size - 1);
    // If a prior writer was interrupted, terminate its incomplete record first
    // so the new entry remains independently readable. O_APPEND keeps the
    // separator and record in one write even when multiple CLIs submit at once.
    const separator = size > 0 && lastByte[0] !== 10 ? "\n" : "";
    writeFileSync(file, `${separator}${JSON.stringify(normalized)}\n`, "utf8");
    return true;
  } catch (error) {
    console.error("Failed to save CLI input history:", error);
    return false;
  } finally {
    if (file !== undefined) closeSync(file);
  }
}

// The composer can unmount when a selector opens. Keep failed writes here so
// the current process can still recall them after the composer remounts.
let unsavedHistory: string[] = [];

export function recordSharedHistory(submitted: string): void {
  if (!appendHistory(submitted)) {
    unsavedHistory = pushHistory(unsavedHistory, submitted);
  }
}

export function loadSharedHistory(fallback: string[]): string[] {
  const history = readHistory();
  return history === null ? fallback : unsavedHistory.reduce(pushHistory, history);
}
