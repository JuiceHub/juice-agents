/** Real stdio transport: framework imports and Python cwd stay independent. */
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { GatewayClient } from "../../frontend/cli/src/gateway/client.js";
import { resolveWorkspaceDir } from "../../frontend/cli/src/lib/workspace.js";

test("explicit workspace outranks inherited workspace and validates directories", () => {
  const directory = mkdtempSync(path.join(tmpdir(), "juice-workspace-"));
  try {
    assert.equal(resolveWorkspaceDir({ workspace: directory, cwd: "/", env: { JUICE_WORKSPACE_DIR: "/absent" }, validate: true }), directory);
    assert.throws(() => resolveWorkspaceDir({ workspace: path.join(directory, "missing"), cwd: "/", env: {}, validate: true }), /Workspace is missing/);
    const file = path.join(directory, "file");
    writeFileSync(file, "");
    assert.throws(() => resolveWorkspaceDir({ workspace: file, cwd: "/", env: {}, validate: true }), /not a directory/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("gateway starts in a Unicode project and uses effective backend configuration", { timeout: 20000 }, async () => {
  const directory = mkdtempSync(path.join(tmpdir(), "juice 项目 -"));
  const capture = path.join(directory, "launch.json");
  // Project names must not shadow the framework's top-level imports.
  writeFileSync(path.join(directory, "adapters.py"), "raise RuntimeError('project shadow module')\n");
  writeFileSync(path.join(directory, "juice_agents.py"), "raise RuntimeError('project shadow module')\n");
  const python = process.env.JUICE_PYTHON_PATH || "python";
  const resolvedPython = spawnSync(python, ["-c", "import sys; print(sys.executable)"], { encoding: "utf8" }).stdout.trim();
  const wrapper = path.join(directory, "python-wrapper");
  writeFileSync(wrapper, `#!${resolvedPython}\nimport json, os, sys\nfrom pathlib import Path\nPath(${JSON.stringify(capture)}).write_text(json.dumps({"cwd": os.getcwd(), "path": os.environ.get("PATH")}))\nos.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n`);
  chmodSync(wrapper, 0o755);
  const client = new GatewayClient(wrapper, directory);
  try {
    await client.start();
    const effective = await client.loadEffectiveConfig({ base_dir: directory });
    assert.ok(effective.models, "SDK defaults are available outside the source checkout");
    assert.deepEqual(await client.loadWorkspaceConfig({ base_dir: directory }), {});
    await client.saveWorkspaceConfig({ base_dir: directory, config: { runtime: { agent_type: "codeact" } } });
    assert.equal((await client.loadEffectiveConfig({ base_dir: directory })).runtime?.agent_type, "codeact");
    const launched = JSON.parse(readFileSync(capture, "utf8"));
    assert.equal(launched.cwd, directory);
    assert.equal(launched.path, process.env.PATH);
  } finally {
    client.stop();
    rmSync(directory, { recursive: true, force: true });
  }
});

test("model persistence leaves an unchanged global model inherited", async () => {
  const { modelConfigPatch } = await import("../../frontend/cli/src/lib/commands.js");
  assert.deepEqual(modelConfigPatch({ model_name: "global", model_effort: "disabled" },
    { model_name: "global", model_effort: "high" }), { model_effort: "high" });
  assert.deepEqual(modelConfigPatch({ model_name: "global", model_effort: "high" },
    { model_name: "project", model_effort: "high" }), { model_name: "project" });
});
