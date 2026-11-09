import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { PassThrough } from "node:stream";
import test, { type TestContext } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { appendHistory, readHistory } from "../../frontend/cli/src/lib/history.js";
import { createComposerController } from "../../frontend/cli/src/lib/composer.js";

// Resolve the same React and Ink instances used by the CLI workspace.
const requireCli = createRequire(new URL("../../frontend/cli/package.json", import.meta.url));
const { createElement, useState } = requireCli("react");
const ink = import(pathToFileURL(requireCli.resolve("ink")).href);

async function flushInput() {
  for (let turn = 0; turn < 6; turn += 1) {
    await new Promise<void>((resolve) => setImmediate(resolve));
  }
}

function temporaryHistory(t: TestContext): string {
  const directory = mkdtempSync(join(tmpdir(), "juice-cli-history-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  return join(directory, "nested", "history.jsonl");
}

test("shared history persists complete multiline inputs for independent processes", (t) => {
  const historyFile = temporaryHistory(t);
  const multiline = "first line\n  second line\n第三行";
  assert.deepEqual(readHistory(historyFile), []);
  assert.equal(appendHistory(multiline, historyFile), true);

  // A separate Node process models an already-running independent runner.
  const moduleUrl = new URL("../../frontend/cli/src/lib/history.ts", import.meta.url).href;
  const cliDir = fileURLToPath(new URL("../../frontend/cli/", import.meta.url));
  const script = `
    import { readHistory, appendHistory } from ${JSON.stringify(moduleUrl)};
    const file = process.argv[1];
    process.stdout.write(JSON.stringify(readHistory(file)) + "\\n");
    if (!appendHistory("from another runner", file)) process.exit(1);
  `;
  const output = execFileSync(process.execPath, ["--import", "tsx", "--input-type=module", "-e", script, historyFile], {
    cwd: cliDir,
    encoding: "utf8",
  });
  assert.deepEqual(JSON.parse(output.trim()), [multiline]);
  assert.deepEqual(readHistory(historyFile), [multiline, "from another runner"]);
  assert.deepEqual(
    readFileSync(historyFile, "utf8").trimEnd().split("\n").map((line) => JSON.parse(line)),
    [multiline, "from another runner"],
  );
});

test("invalid records and blank submissions do not hide valid history", (t) => {
  const historyFile = temporaryHistory(t);
  assert.equal(appendHistory("valid", historyFile), true);
  appendHistory(" \n\t", historyFile);
  writeFileSync(historyFile, [
    JSON.stringify("valid"),
    JSON.stringify("valid"),
    "not JSON",
    JSON.stringify({ command: "not a string" }),
    JSON.stringify(""),
    JSON.stringify(" \t"),
    JSON.stringify("next\nline"),
    '"unfinished',
    "",
  ].join("\n"));
  assert.deepEqual(readHistory(historyFile), ["valid", "next\nline"]);
});

test("blank submissions are not persisted", (t) => {
  const historyFile = temporaryHistory(t);
  appendHistory(" \n\t", historyFile);
  assert.deepEqual(readHistory(historyFile), []);
  assert.equal(appendHistory("kept", historyFile), true);
  appendHistory("   ", historyFile);
  assert.deepEqual(readHistory(historyFile), ["kept"]);
});

test("appending after an interrupted record preserves the new submission", (t) => {
  const historyFile = temporaryHistory(t);
  assert.equal(appendHistory("before interruption", historyFile), true);
  writeFileSync(historyFile, '"unfinished', { flag: "a" });
  assert.equal(appendHistory("after interruption", historyFile), true);
  assert.deepEqual(readHistory(historyFile), ["before interruption", "after interruption"]);
});

test("unreadable or unwritable history falls back without throwing", (t) => {
  const log = t.mock.method(console, "error", () => {});
  const historyFile = temporaryHistory(t);
  const directory = dirname(dirname(historyFile));
  // Reading or appending a directory fails even when tests run as root.
  assert.equal(readHistory(directory), null);
  assert.equal(appendHistory("not writable", directory), false);
  const regularFile = join(directory, "not-a-directory");
  writeFileSync(regularFile, "file");
  assert.equal(readHistory(join(regularFile, "history")), null);
  assert.equal(appendHistory("not writable", join(regularFile, "history")), false);
  assert.equal(log.mock.callCount(), 4);
});

test("running composers refresh shared history and restore an unsent draft", async () => {
  const tempHome = mkdtempSync(join(tmpdir(), "juice-cli-home-"));
  const previousHome = process.env.HOME;
  process.env.HOME = tempHome;
  const mounted: Array<{ unmount: () => void; cleanup: () => void; stdin: PassThrough; stdout: PassThrough }> = [];
  try {
    const { render } = await ink;
    const { RichComposer } = await import(pathToFileURL(fileURLToPath(new URL("../../frontend/cli/src/components/RichComposer.tsx", import.meta.url))).href);
    function mountComposer() {
      const stdin = new PassThrough();
      Object.assign(stdin, { isTTY: true, setRawMode: () => stdin, ref: () => stdin, unref: () => stdin });
      const stdout = new PassThrough();
      Object.assign(stdout, { columns: 80, rows: 24, isTTY: false });
      let output = "";
      stdout.on("data", (chunk) => { output += chunk.toString(); });
      stdout.resume();
      let currentValue = "";
      const submitted: string[] = [];
      function Probe() {
        const [value, setValue] = useState("");
        const [cursor, setCursor] = useState(0);
        const [controller, setController] = useState(createComposerController());
        currentValue = value;
        return createElement(RichComposer, {
          value, cursor, controller,
          onChange: setValue,
          onCursorChange: setCursor,
          onControllerChange: setController,
          onSubmit: (text: string) => submitted.push(text),
          onTab: () => null,
          onCycleMode: () => {}, onEscape: () => {}, onExit: () => {},
          onToggleActors: () => {}, onToggleTasks: () => {},
          onCandidateUp: () => {}, onCandidateDown: () => {},
          disabled: false, candidatesVisible: false, isActive: true,
        });
      }
      const instance = render(createElement(Probe), {
        stdin, stdout, patchConsole: false, exitOnCtrlC: false,
      });
      mounted.push({ ...instance, stdin, stdout });
      return {
        submitted,
        output: () => output,
        value: () => currentValue,
        send: async (input: string) => {
          stdin.write(input);
          await flushInput();
        },
      };
    }

    const first = mountComposer();
    const second = mountComposer();
    await flushInput();
    await first.send("first message");
    await first.send("\r");
    assert.deepEqual(first.submitted, ["first message"], first.output());
    await second.send("unsent draft");
    await second.send("\u001b[A");
    assert.equal(second.value(), "first message");
    await second.send("\u001b[B");
    assert.equal(second.value(), "unsent draft");

    await first.send("new message");
    await first.send("\r");
    await second.send("\u001b[A");
    assert.equal(second.value(), "new message");
    await second.send("\u001b[A");
    assert.equal(second.value(), "first message");
    await second.send("\u001b[B");
    await second.send("\u001b[B");
    assert.equal(second.value(), "unsent draft");
  } finally {
    for (const instance of mounted) {
      instance.unmount();
      instance.cleanup();
      instance.stdin.destroy();
      instance.stdout.destroy();
    }
    if (previousHome === undefined) delete process.env.HOME;
    else process.env.HOME = previousHome;
    rmSync(tempHome, { recursive: true, force: true });
  }
});
