import assert from "node:assert/strict";
import test from "node:test";

import type { SelectorItem } from "../../frontend/cli/src/hooks/useInteractiveSelector.js";
import type { MessageBlock } from "../../frontend/cli/src/lib/presenter.js";
import { openRewardModelSelector } from "../../frontend/cli/src/lib/rewardModelSelector.js";
import type { RewardModelConfiguration, RuntimeModelInfo } from "../../frontend/shared/src/gateway/types.js";

const baseDir = "/tmp/reward-model-selector-workspace";
const catalog: RuntimeModelInfo[] = [
  { model_name: "generator", backend: "openai", provider_model_name: "generator-provider", supported_efforts: ["low", "high"] },
  { model_name: "judge", backend: "anthropic", provider_model_name: "judge-provider", supported_efforts: ["low", "high"] },
];

/** Capture the actual selector callback so tests cover opening and confirmation independently. */
function selectorHarness(initial: Partial<RewardModelConfiguration> = {}) {
  const calls: { method: string; params: unknown }[] = [];
  const blocks: MessageBlock[] = [];
  const state: RewardModelConfiguration = {
    model_name: "judge", scorer_configured: true, breadth: 3, execution: "candidate_selection", ...initial,
  };
  const opened: {
    title: string;
    items: SelectorItem[];
    confirm: (item: SelectorItem) => void;
    options: unknown;
  }[] = [];
  const client: any = {
    listModels: async (params: unknown) => {
      calls.push({ method: "listModels", params });
      return catalog;
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
    switchModel: async () => assert.fail("reward selection must preserve the generation model"),
    switchStrategy: async () => assert.fail("reward selection must preserve breadth"),
    startSession: async () => assert.fail("reward selection must not create a session"),
  };
  const selector = {
    open: (title: string, items: SelectorItem[], confirm: (item: SelectorItem) => void, options?: unknown) => {
      opened.push({ title, items, confirm, options });
    },
  };
  const args = { client, baseDir, models: catalog, selector, addMessages: (messages: MessageBlock[]) => blocks.push(...messages) };
  return { args, calls, blocks, state, opened, client };
}

test("reward selector uses cached catalog, highlights the reward model, and leaves effort disabled", async () => {
  const harness = selectorHarness();
  await openRewardModelSelector(harness.args);
  assert.deepEqual(harness.calls, [{ method: "configureRewardModel", params: { base_dir: baseDir } }]);
  assert.equal(harness.opened.length, 1);
  const page = harness.opened[0];
  assert.equal(page.title, "Select Reward Model");
  assert.equal(page.options, undefined);
  assert.equal(page.items.find((item) => item.value === "judge")?.active, true);
  assert.equal(page.items.find((item) => item.value === "generator")?.active, false);
  assert.equal(page.items.find((item) => item.value === "")?.active, false);
  assert.match(page.items.find((item) => item.value === "judge")?.description || "", /anthropic/);
  assert.match(page.items.find((item) => item.value === "judge")?.description || "", /judge-provider/);
  assert.deepEqual(harness.blocks, []);
});

test("reward selector loads an empty catalog concurrently with current configuration", async () => {
  const harness = selectorHarness();
  let finishConfiguration!: (state: RewardModelConfiguration) => void;
  harness.client.configureRewardModel = (params: unknown) => {
    harness.calls.push({ method: "configureRewardModel", params });
    return new Promise<RewardModelConfiguration>((resolve) => { finishConfiguration = resolve; });
  };
  const pending = openRewardModelSelector({ ...harness.args, models: [] });
  // listModels must start without waiting for the configuration RPC to resolve.
  assert.ok(harness.calls.some((call) => call.method === "listModels"));
  assert.equal(harness.opened.length, 0);
  finishConfiguration(harness.state);
  await pending;
  assert.deepEqual(harness.calls.find((call) => call.method === "listModels")?.params, { base_dir: baseDir });
  assert.ok(harness.opened[0].items.some((item) => item.value === "judge"));
});

test("unconfigured reward selector marks the clear option active", async () => {
  const harness = selectorHarness({ model_name: null, scorer_configured: false, execution: "linear" });
  await openRewardModelSelector(harness.args);
  assert.equal(harness.opened[0].items.find((item) => item.value === "")?.active, true);
  assert.equal(harness.opened[0].items.filter((item) => item.active).length, 1);
});

test("SDK reward scorer is explained without marking the clear option active", async () => {
  const harness = selectorHarness({ model_name: null, scorer_configured: true });
  await openRewardModelSelector(harness.args);
  const clear = harness.opened[0].items.find((item) => item.value === "");
  assert.equal(clear?.active, false);
  assert.match(clear?.description || "", /SDK/);
  assert.equal(harness.opened[0].items.filter((item) => item.active).length, 0);
});

test("confirming a reward model uses command validation and preserves candidate breadth", async () => {
  const harness = selectorHarness({ model_name: null, scorer_configured: false, execution: "linear" });
  await openRewardModelSelector(harness.args);
  const page = harness.opened[0];
  await page.confirm(page.items.find((item) => item.value === "judge")!);
  assert.deepEqual(harness.calls.slice(1), [
    { method: "listModels", params: { base_dir: baseDir } },
    { method: "configureRewardModel", params: { base_dir: baseDir, model_name: "judge" } },
  ]);
  assert.equal(harness.state.breadth, 3);
  assert.equal(harness.state.model_name, "judge");
  assert.match(harness.blocks[0].text || "", /Reward model: judge/);
});

test("confirming clear resets the scorer without requesting models or changing breadth", async () => {
  const harness = selectorHarness();
  await openRewardModelSelector(harness.args);
  const page = harness.opened[0];
  await page.confirm(page.items.find((item) => item.value === "")!);
  assert.deepEqual(harness.calls.slice(1), [
    { method: "configureRewardModel", params: { base_dir: baseDir, reset: true } },
  ]);
  assert.equal(harness.state.scorer_configured, false);
  assert.equal(harness.state.breadth, 3);
  assert.match(harness.blocks[0].text || "", /Reward model: not configured/);
});

test("canceling the selector without confirming leaves reward configuration untouched", async () => {
  const harness = selectorHarness();
  const before = { ...harness.state };
  await openRewardModelSelector(harness.args);
  // Escape closes the shared selector without invoking this callback.
  assert.deepEqual(harness.state, before);
  assert.deepEqual(harness.calls, [{ method: "configureRewardModel", params: { base_dir: baseDir } }]);
  assert.deepEqual(harness.blocks, []);
});

for (const failingRpc of ["listModels", "configureRewardModel"] as const) {
  test(`reward selector propagates ${failingRpc} load failure without opening a partial page`, async () => {
    const harness = selectorHarness();
    const before = { ...harness.state };
    harness.client[failingRpc] = async () => { throw new Error("catalog unavailable"); };
    // The app's command boundary presents loading errors; the helper must not
    // open a partial selector or write configuration when either read fails.
    await assert.rejects(openRewardModelSelector({ ...harness.args, models: [] }), /catalog unavailable/);
    assert.deepEqual(harness.opened, []);
    assert.deepEqual(harness.blocks, []);
    assert.deepEqual(harness.state, before);
  });
}

test("reward selector confirmation renders the existing command error block", async () => {
  const harness = selectorHarness();
  await openRewardModelSelector(harness.args);
  harness.client.configureRewardModel = async () => { throw new Error("runner busy"); };
  const page = harness.opened[0];
  await page.confirm(page.items.find((item) => item.value === "judge")!);
  assert.equal(harness.blocks[0].kind, "error");
  assert.match(harness.blocks[0].text || "", /Failed to configure reward model: runner busy/);
});
