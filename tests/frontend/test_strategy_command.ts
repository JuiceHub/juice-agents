import assert from "node:assert/strict";
import test from "node:test";

import { executeCommand } from "../../frontend/cli/src/lib/commands.js";
import { presentCommandHelp } from "../../frontend/shared/src/presenter/command.js";

function clientHarness(selection = false) {
  const calls: unknown[] = [];
  const client: any = {
    switchStrategy: async (params: { breadth?: number; reset?: boolean }) => {
      calls.push(params);
      return {
        status: "pending",
        strategy: {
          type: params.reset ? "greedy" : "exploration",
          params: params.reset ? {} : { breadth: params.breadth },
          execution: selection ? "candidate_selection" : "linear",
          scorer_configured: selection,
        },
        message: "configured",
      };
    },
  };
  return { client, calls };
}

test("strategy help describes per-step selection and linear fallback", async () => {
  const { client, calls } = clientHarness();
  const result = await executeCommand("/strategy", client, "/tmp/workspace");
  assert.match(result.blocks[0].text || "", /generate N candidates per step, select one, execute once/);
  assert.match(result.blocks[0].text || "", /线性执行/);
  assert.doesNotMatch(result.blocks[0].text || "", /depth=|beam_width=|breadth=\[/);
  assert.deepEqual(calls, []);
  assert.match(presentCommandHelp().text || "", /\/strategy \[breadth=N\|reset\]/);
});

test("breadth config includes the cold workspace and clearly reports linear execution", async () => {
  const { client, calls } = clientHarness();
  const result = await executeCommand("/strategy breadth=3", client, "/tmp/workspace");
  assert.deepEqual(calls, [{ base_dir: "/tmp/workspace", breadth: 3 }]);
  assert.match(result.blocks[0].text || "", /线性执行 \(Linear execution\)/);
  assert.match(result.blocks[0].text || "", /Configured breadth: 3; no scorer configured/);
});

test("existing scorer enables per-step selection feedback", async () => {
  const { client } = clientHarness(true);
  const result = await executeCommand("/strategy breadth=2", client, "/tmp/workspace");
  assert.match(result.blocks[0].text || "", /Per-step candidate selection \(breadth=2\)/);
});

test("reset only sends the supported reset configuration", async () => {
  const { client, calls } = clientHarness();
  const result = await executeCommand("/strategy reset", client, "/tmp/workspace");
  assert.deepEqual(calls, [{ base_dir: "/tmp/workspace", reset: true }]);
  assert.match(result.blocks[0].text || "", /线性执行/);
});

for (const args of [
  "breadth=0", "breadth=-1", "breadth=2.5", "breadth=3junk", "breadth=[3,2]",
  "breadth=true", "breadth=9007199254740992", "depth=2", "beam_width=2",
  "temperature=0.5", "breadth=2 depth=2", "reset breadth=3", "breadth=2 breadth=3",
]) {
  test(`invalid strategy arguments never issue RPC: ${args}`, async () => {
    const { client, calls } = clientHarness();
    const result = await executeCommand(`/strategy ${args}`, client, "/tmp/workspace");
    assert.equal(result.blocks[0].kind, "error");
    assert.deepEqual(calls, []);
  });
}
