import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

test("repeated clipped Ink frames stay below the terminal clear threshold", () => {
  const packageRoot = fileURLToPath(new URL("../../", import.meta.url));
  const script = String.raw`
    import React from "react";
    import { PassThrough } from "node:stream";
    import { Box, Text, render } from "ink";

    const output = [];
    const stdout = new PassThrough();
    Object.assign(stdout, { columns: 24, rows: 6, isTTY: true });
    stdout.on("data", chunk => output.push(Buffer.from(chunk)));

    const frame = label => React.createElement(
      Box,
      {
        flexDirection: "column",
        height: Math.max(1, stdout.rows - 1),  // scrollback guard
        overflowY: "hidden",
      },
      ...Array.from({ length: 20 }, (_, index) =>
        React.createElement(Text, { key: index }, label + "-" + index)
      )
    );

    const instance = render(frame("brief"), { stdout, patchConsole: false });
    for (const label of ["planning", "research-1", "research-2", "report"]) {
      instance.rerender(frame(label));
    }
    await new Promise(resolve => setImmediate(resolve));
    instance.unmount();
    process.stdout.write(JSON.stringify(Buffer.concat(output).toString("base64")));
  `;
  const result = spawnSync(
    process.execPath,
    ["--import", "tsx", "--input-type=module", "-e", script],
    {
      cwd: packageRoot,
      encoding: "utf8",
      maxBuffer: 1024 * 1024,
    }
  );

  assert.equal(result.status, 0, result.stderr);
  const rendered = Buffer.from(JSON.parse(result.stdout), "base64").toString();
  assert.doesNotMatch(rendered, /\u001B\[3J/);
  assert.doesNotMatch(rendered, /\u001B\[2J\u001B\[3J\u001B\[H/);
});
