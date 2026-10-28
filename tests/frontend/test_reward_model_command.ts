import assert from "node:assert/strict";
import test from "node:test";

import { GatewayClient } from "../../frontend/cli/src/gateway/client.js";
import {
  acceptCommandCompletion,
  buildGhostText,
  buildInlineHint,
  getCommandCandidates,
} from "../../frontend/cli/src/hooks/useCompletion.js";
import { executeCommand } from "../../frontend/cli/src/lib/commands.js";
import { presentCommandHelp } from "../../frontend/shared/src/presenter/command.js";
import type { RewardModelConfiguration } from "../../frontend/shared/src/gateway/types.js";

const workspace = "/tmp/reward-model-workspace";
const modelNames = ["generator", "judge"];

/** Keep generation model and breadth observable so RM cannot change either. */
function clientHarness(initial: Partial<RewardModelConfiguration> = {}) {
  const calls: { method: string; params: unknown }[] = [];
  const state: RewardModelConfiguration = {
    model_name: null,
    scorer_configured: false,
    breadth: 1,
    execution: "linear",
    ...initial,
  };
  const client: any = {
    listModels: async (params: unknown) => {
      calls.push({ method: "listModels", params });
      return modelNames.map((model_name) => ({ model_name }));
    },
    configureRewardModel: async (params: { model_name?: string; reset?: boolean }) => {
      calls.push({ method: "configureRewardModel", params });
      if (params.model_name || params.reset) {
        state.model_name = params.reset ? null : params.model_name!;
        state.scorer_configured = !params.reset;
        state.execution = state.scorer_configured && state.breadth > 1 ? "candidate_selection" : "linear";
      }
      return { ...state };
    },
    switchModel: async () => assert.fail("/rm must preserve the generation model"),
    switchStrategy: async () => assert.fail("/rm must preserve candidate breadth"),
  };
  return { client, calls, state };
}

test("rm opens the reward model selector without issuing an RPC", async () => {
  const { client, calls } = clientHarness();
  const result = await executeCommand("/rm", client, workspace);
  assert.deepEqual(calls, []);
  assert.deepEqual(result.blocks, []);
  assert.equal(result.interactiveAction, "reward-model-selector");
});

test("rm selects a model from the same workspace catalog without changing breadth", async () => {
  const { client, calls, state } = clientHarness();
  const result = await executeCommand("/rm judge", client, workspace, { sessionStatus: null });
  assert.deepEqual(calls, [
    { method: "listModels", params: { base_dir: workspace } },
    { method: "configureRewardModel", params: { base_dir: workspace, model_name: "judge" } },
  ]);
  assert.equal(state.breadth, 1);
  assert.match(result.blocks[0].text || "", /Reward model: judge/);
  assert.match(result.blocks[0].text || "", /\/strategy breadth=N \(N > 1\)/);
});

test("rm reports candidate selection with an existing breadth", async () => {
  const { client, state } = clientHarness({ breadth: 3 });
  const result = await executeCommand("/rm judge", client, workspace);
  assert.equal(state.breadth, 3);
  assert.match(result.blocks[0].text || "", /Per-step candidate selection/);
  assert.doesNotMatch(result.blocks[0].text || "", /Use \/strategy/);
});

test("rm reset clears the scorer without changing breadth or fetching models", async () => {
  const { client, calls, state } = clientHarness({ model_name: "judge", scorer_configured: true, breadth: 3 });
  const result = await executeCommand("/rm reset", client, workspace);
  assert.deepEqual(calls, [{ method: "configureRewardModel", params: { base_dir: workspace, reset: true } }]);
  assert.equal(state.breadth, 3);
  assert.equal(state.scorer_configured, false);
  assert.match(result.blocks[0].text || "", /Reward model: not configured/);
  assert.match(result.blocks[0].text || "", /Linear execution/);
});

test("unknown reward model names never reach the configuration RPC", async () => {
  const { client, calls } = clientHarness();
  const result = await executeCommand("/rm missing", client, workspace);
  assert.equal(result.blocks[0].kind, "error");
  assert.match(result.blocks[0].text || "", /Unknown reward model: missing.*\/models/);
  assert.deepEqual(calls, [{ method: "listModels", params: { base_dir: workspace } }]);
});

for (const argument of ["judge high", "reset judge", "judge breadth=3"]) {
  test(`rm rejects extra arguments before issuing any RPC: ${argument}`, async () => {
    const { client, calls } = clientHarness();
    const result = await executeCommand(`/rm ${argument}`, client, workspace);
    assert.equal(result.blocks[0].kind, "error");
    assert.match(result.blocks[0].text || "", /Usage: \/rm/);
    assert.deepEqual(calls, []);
  });
}

test("rm presents gateway configuration failures", async () => {
  const { client } = clientHarness();
  client.configureRewardModel = async () => { throw new Error("runner busy"); };
  const result = await executeCommand("/rm judge", client, workspace);
  assert.equal(result.blocks[0].kind, "error");
  assert.match(result.blocks[0].text || "", /Failed to configure reward model: runner busy/);
});

test("rm help and strategy help connect reward selection to the model catalog", async () => {
  assert.match(presentCommandHelp().text || "", /\/rm \[model_name\|reset\]/);
  const { client, calls } = clientHarness();
  const result = await executeCommand("/strategy", client, workspace);
  assert.match(result.blocks[0].text || "", /\/rm <model_name>.*\/models/);
  assert.deepEqual(calls, []);
});

test("strategy breadth=1 reports an existing scorer while remaining linear", async () => {
  const client: any = {
    switchStrategy: async () => ({
      message: "configured",
      strategy: { params: { breadth: 1 }, execution: "linear", scorer_configured: true },
    }),
  };
  const result = await executeCommand("/strategy breadth=1", client, workspace);
  assert.match(result.blocks[0].text || "", /Linear execution/);
  assert.match(result.blocks[0].text || "", /Configured breadth: 1; scorer configured/);
  assert.doesNotMatch(result.blocks[0].text || "", /no scorer configured/);
});

test("rm completion shares model names and closes completed or extra arguments", () => {
  assert.ok(getCommandCandidates("/").some((candidate) => candidate.label === "/rm"));
  assert.deepEqual(getCommandCandidates("/rm"), []);
  assert.deepEqual(getCommandCandidates("/rm ", modelNames).map((item) => item.label), [...modelNames, "reset"]);
  assert.deepEqual(getCommandCandidates("/rm j", modelNames).map((item) => item.label), ["judge"]);
  assert.deepEqual(getCommandCandidates("/rm r", modelNames).map((item) => item.label), ["reset"]);
  for (const input of ["/rm judge", "/rm judge ", "/rm reset", "/rm reset ", "/rm judge h"]) {
    assert.deepEqual(getCommandCandidates(input, modelNames), []);
  }
});

test("rm completion acceptance and inline hints preserve a usable command", () => {
  const candidate = getCommandCandidates("/rm j", modelNames)[0];
  assert.equal(buildGhostText({ input: "/rm j", candidates: [candidate], selectedIndex: 0, visible: true }), "udge");
  assert.deepEqual(acceptCommandCompletion({ input: "/rm j", candidate }), { input: "/rm judge ", cursor: 10 });
  assert.equal(buildInlineHint({ input: "/rm ", candidates: [] }), "<model_name|reset>");
  assert.equal(buildInlineHint({ input: "/rm judge ", candidates: [] }), null);
});

test("GatewayClient uses the configure_reward_model RPC and preserves its typed result", async () => {
  const client = new GatewayClient();
  const sent: any[] = [];
  // Simulate stdio only; no process or provider request is needed for routing.
  (client as any).process = { stdin: { write: (raw: string) => sent.push(JSON.parse(raw)) } };
  const pending = client.configureRewardModel({ base_dir: workspace, model_name: "judge" });
  assert.equal(sent[0].method, "configure_reward_model");
  assert.deepEqual(sent[0].params, { base_dir: workspace, model_name: "judge" });
  const expected: RewardModelConfiguration = {
    model_name: "judge", scorer_configured: true, breadth: 3, execution: "candidate_selection",
  };
  (client as any).handleResponse(JSON.stringify({ jsonrpc: "2.0", id: sent[0].id, result: expected }));
  assert.deepEqual(await pending, expected);
});
