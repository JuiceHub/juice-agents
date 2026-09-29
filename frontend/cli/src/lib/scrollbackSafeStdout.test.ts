import assert from "node:assert/strict";
import test from "node:test";

import {
  createScrollbackSafeStdout,
  preserveTerminalScrollback,
} from "./scrollbackSafeStdout.js";

test("removes only the ANSI scrollback erase command", () => {
  const clearTerminal = "\u001B[2J\u001B[3J\u001B[H";

  assert.equal(
    preserveTerminalScrollback(clearTerminal),
    "\u001B[H\u001B[0J"
  );
  assert.equal(preserveTerminalScrollback("\u001B[2J"), "\u001B[2J");
  assert.equal(
    preserveTerminalScrollback(`${clearTerminal}a${clearTerminal}b`),
    "\u001B[H\u001B[0Ja\u001B[H\u001B[0Jb"
  );
  assert.equal(
    preserveTerminalScrollback("before\u001B[3Jafter"),
    "beforeafter"
  );
  assert.equal(
    preserveTerminalScrollback("\u001B[31mred\u001B[0m"),
    "\u001B[31mred\u001B[0m"
  );
  assert.equal(preserveTerminalScrollback("normal output"), "normal output");
});

test("stdout proxy preserves Buffer writes, callbacks, and backpressure results", () => {
  const writes: string[] = [];
  let callbackCalled = false;
  const stdout = {
    columns: 120,
    rows: 40,
    write(chunk: string | Buffer) {
      writes.push(chunk.toString());
      const callback = Array.from(arguments).find(
        (value) => typeof value === "function"
      ) as (() => void) | undefined;
      callback?.();
      return false;
    },
    on() {
      return this;
    },
    off() {
      return this;
    },
  } as unknown as NodeJS.WriteStream;

  const safeStdout = createScrollbackSafeStdout(stdout);
  const accepted = safeStdout.write(
    Buffer.from("\u001B[2J\u001B[3J\u001B[Hframe"),
    () => {
      callbackCalled = true;
    }
  );

  assert.deepEqual(writes, ["\u001B[H\u001B[0Jframe"]);
  assert.equal(safeStdout.columns, 120);
  assert.equal(callbackCalled, true);
  assert.equal(accepted, false);
});
