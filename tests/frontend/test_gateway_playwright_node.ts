import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { GatewayClient } from "../../frontend/cli/src/gateway/client.js";

test("gateway passes its Node executable to Playwright unless explicitly configured", { timeout: 10000 }, async () => {
  const directory = mkdtempSync(path.join(tmpdir(), "juice-gateway-node-"));
  const python = path.join(directory, "fake-python");
  const originalNodePath = process.env.PLAYWRIGHT_NODEJS_PATH;
  // The stub reports its real inherited environment through the same stdio
  // transport as the Python gateway, without starting a browser or touching the network.
  writeFileSync(python, `#!/usr/bin/env python
import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"node_path": os.environ.get("PLAYWRIGHT_NODEJS_PATH")}}), flush=True)
`);
  chmodSync(python, 0o755);

  try {
    for (const configuredPath of [undefined, "/custom/node"]) {
      if (configuredPath === undefined) delete process.env.PLAYWRIGHT_NODEJS_PATH;
      else process.env.PLAYWRIGHT_NODEJS_PATH = configuredPath;

      const client = new GatewayClient(python, directory);
      try {
        await client.start();
        const result = await client.loadWorkspaceConfig({ base_dir: directory });
        assert.deepEqual(result, { node_path: configuredPath ?? process.execPath });
      } finally {
        client.stop();
      }
    }
  } finally {
    if (originalNodePath === undefined) delete process.env.PLAYWRIGHT_NODEJS_PATH;
    else process.env.PLAYWRIGHT_NODEJS_PATH = originalNodePath;
    rmSync(directory, { recursive: true, force: true });
  }
});
