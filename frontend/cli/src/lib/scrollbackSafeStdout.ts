/**
 * Keep Ink's full-screen redraws from deleting the terminal scrollback.
 *
 * Ink uses ansi-escapes.clearTerminal when its output reaches the terminal
 * height. CSI 3 J clears scrollback, while CSI 2 J can also reset the viewport
 * in xterm.js-based terminals. Replacing the full sequence with cursor-home
 * plus erase-down redraws the visible screen without moving scrollback.
 */

const INK_CLEAR_TERMINAL_SEQUENCE = "\u001B[2J\u001B[3J\u001B[H";
const SCROLLBACK_SAFE_CLEAR_SEQUENCE = "\u001B[H\u001B[0J";
const ERASE_SCROLLBACK_SEQUENCE = "\u001B[3J";

export function preserveTerminalScrollback(chunk: string): string {
  // Ink writes the full clear sequence in one call today. Keep the standalone
  // CSI 3J removal as a second guard so dependency changes cannot erase history.
  return chunk
    .replaceAll(INK_CLEAR_TERMINAL_SEQUENCE, SCROLLBACK_SAFE_CLEAR_SEQUENCE)
    .replaceAll(ERASE_SCROLLBACK_SEQUENCE, "");
}

export function createScrollbackSafeStdout(
  stdout: NodeJS.WriteStream
): NodeJS.WriteStream {
  const write = ((chunk: unknown, ...args: unknown[]) => {
    const safeChunk =
      typeof chunk === "string"
        ? preserveTerminalScrollback(chunk)
        : Buffer.isBuffer(chunk)
          ? Buffer.from(preserveTerminalScrollback(chunk.toString()))
          : chunk;

    return (stdout.write as (...writeArgs: unknown[]) => boolean).call(
      stdout,
      safeChunk,
      ...args
    );
  }) as NodeJS.WriteStream["write"];

  return new Proxy(stdout, {
    get(target, property) {
      if (property === "write") {
        return write;
      }

      const value = Reflect.get(target, property, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}
