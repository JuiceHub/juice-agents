import assert from "node:assert/strict";
import test from "node:test";

import { GatewayClient } from "../../frontend/cli/src/gateway/client.js";
import {
  acceptCommandCompletion,
  buildGhostText,
  buildInlineHint,
  getCommandCandidates,
} from "../../frontend/cli/src/hooks/useCompletion.js";
import type { SelectorItem } from "../../frontend/cli/src/hooks/useInteractiveSelector.js";
import { getCommandExecutionPolicy, shouldQueueSubmission } from "../../frontend/cli/src/lib/commandScheduling.js";
import { executeCommand } from "../../frontend/cli/src/lib/commands.js";
import { openImageModelSelector } from "../../frontend/cli/src/lib/imageModelSelector.js";
import type { MessageBlock } from "../../frontend/cli/src/lib/presenter.js";
import type { ImageModelsResponse } from "../../frontend/shared/src/gateway/types.js";
import { presentCommandHelp } from "../../frontend/shared/src/presenter/command.js";
import { WebGatewayClient } from "../../frontend/web/src/gateway/client.js";

const baseDir = "/tmp/image-model-workspace";
const imageNames = ["seedream", "seedream_backup"];

/** The double deliberately has no chat, RM, session or generic save methods. */
function harness(enabled = false) {
  const calls: { method: string; params: unknown }[] = [];
  const blocks: MessageBlock[] = [];
  const catalog: ImageModelsResponse = {
    image_models: imageNames.map((model_name) => ({
      model_name, provider: "ark", backend: "doubao", provider_model_name: `${model_name}-deployment`,
    })),
    current_image_model_name: "seedream",
    image_enabled: enabled,
  };
  const client: any = {
    listImageModels: async (params: unknown) => {
      calls.push({ method: "listImageModels", params });
      return catalog;
    },
    setImageConfig: async (params: { model_name?: string; enabled?: boolean }) => {
      calls.push({ method: "setImageConfig", params });
      if (params.model_name) catalog.current_image_model_name = params.model_name;
      if (params.enabled !== undefined) catalog.image_enabled = params.enabled;
      return { enabled: catalog.image_enabled, runner_id: "runner", image_model_name: catalog.current_image_model_name };
    },
  };
  const opened: { title: string; items: SelectorItem[]; confirm: (item: SelectorItem) => void; options: unknown }[] = [];
  const selector = {
    open: (title: string, items: SelectorItem[], confirm: (item: SelectorItem) => void, options?: unknown) => {
      opened.push({ title, items, confirm, options });
    },
  };
  const args = { client, baseDir, selector, addMessages: (messages: MessageBlock[]) => blocks.push(...messages) };
  return { client, calls, catalog, blocks, opened, args };
}

test("image_model dispatch opens the picker without issuing an RPC", async () => {
  const state = harness();
  const result = await executeCommand("/image_model", state.client, baseDir);
  assert.equal(result.interactiveAction, "image-model-selector");
  assert.deepEqual(result.blocks, []);
  assert.deepEqual(state.calls, []);
});

for (const enabled of [false, true]) {
  test(`image_model selects independently and preserves enabled=${enabled}`, async () => {
    const state = harness(enabled);
    const result = await executeCommand("/image_model seedream_backup", state.client, baseDir, { sessionStatus: null });
    assert.deepEqual(state.calls, [
      { method: "listImageModels", params: { base_dir: baseDir } },
      { method: "setImageConfig", params: { base_dir: baseDir, model_name: "seedream_backup" } },
    ]);
    assert.equal(state.catalog.image_enabled, enabled);
    assert.match(result.blocks[0].text || "", /Image model: seedream_backup/);
    assert.match(result.blocks[0].text || "", enabled ? /Image generation: enabled/ : /Image generation: disabled/);
    assert.match(result.blocks[0].text || "", /\/config/);
  });
}

test("unknown and language model names never reach image configuration", async () => {
  for (const name of ["chat-model", "missing"]) {
    const state = harness();
    const result = await executeCommand(`/image_model ${name}`, state.client, baseDir);
    assert.equal(result.blocks[0].kind, "error");
    assert.match(result.blocks[0].text || "", /Unknown image model:/);
    assert.deepEqual(state.calls.map((call) => call.method), ["listImageModels"]);
    assert.equal(state.catalog.current_image_model_name, "seedream");
  }
});

test("image_model rejects effort and extra arguments before any RPC", async () => {
  const state = harness();
  const result = await executeCommand("/image_model seedream high", state.client, baseDir);
  assert.match(result.blocks[0].text || "", /Usage: \/image_model/);
  assert.deepEqual(state.calls, []);
});

test("image picker marks current selection and canceling writes nothing", async () => {
  const state = harness();
  let completedCatalog: unknown;
  await openImageModelSelector({ ...state.args, onCatalogLoaded: (models) => { completedCatalog = models; } });
  assert.deepEqual(state.calls, [{ method: "listImageModels", params: { base_dir: baseDir } }]);
  assert.equal(completedCatalog, state.catalog.image_models);
  const page = state.opened[0];
  assert.equal(page.title, "Select Image Model (disabled)");
  assert.equal(page.options, undefined, "image models have no reasoning effort selector");
  assert.deepEqual(page.items.filter((item) => item.active).map((item) => item.value), ["seedream"]);
  assert.match(page.items[0].description || "", /ark.*doubao.*seedream-deployment/);
  // The shared overlay cancels by closing without invoking confirmation.
  assert.equal(state.catalog.current_image_model_name, "seedream");
  assert.equal(state.catalog.image_enabled, false);
  assert.deepEqual(state.blocks, []);
});

test("image picker confirmation shares direct-command validation and preserves switch", async () => {
  const state = harness();
  await openImageModelSelector(state.args);
  const page = state.opened[0];
  await page.confirm(page.items[1]);
  assert.deepEqual(state.calls.slice(1), [
    { method: "listImageModels", params: { base_dir: baseDir } },
    { method: "setImageConfig", params: { base_dir: baseDir, model_name: "seedream_backup" } },
  ]);
  assert.equal(state.catalog.image_enabled, false);
  assert.match(state.blocks[0].text || "", /Image model: seedream_backup/);
});

test("image picker propagates load failures and handles an empty catalog without writing", async () => {
  const failed = harness();
  failed.client.listImageModels = async () => { throw new Error("Provider missing"); };
  await assert.rejects(openImageModelSelector(failed.args), /Provider missing/);
  assert.deepEqual(failed.opened, []);
  assert.deepEqual(failed.calls, []);

  const empty = harness();
  empty.catalog.image_models = [];
  await openImageModelSelector(empty.args);
  assert.deepEqual(empty.opened, []);
  assert.equal(empty.calls.length, 1);
  assert.match(empty.blocks[0].text || "", /No image models configured/);
});

test("image selection presents gateway failures without changing the model", async () => {
  const state = harness();
  await openImageModelSelector(state.args);
  state.client.setImageConfig = async () => { throw new Error("runner busy"); };
  await state.opened[0].confirm(state.opened[0].items[1]);
  assert.equal(state.catalog.current_image_model_name, "seedream");
  assert.equal(state.blocks[0].kind, "error");
  assert.match(state.blocks[0].text || "", /Failed to configure image model: runner busy/);
});

test("image command completion uses only image catalog and closes completed arguments", () => {
  const candidates = (input: string) => getCommandCandidates(input, ["chat"], [], undefined, [], [], imageNames);
  assert.ok(candidates("/").some((item) => item.label === "/image_model"));
  assert.deepEqual(candidates("/image_model"), []);
  assert.deepEqual(candidates("/image_model ").map((item) => item.label), imageNames);
  assert.deepEqual(candidates("/image_model seedream_b").map((item) => item.label), ["seedream_backup"]);
  assert.deepEqual(candidates("/model ").map((item) => item.label), ["chat"]);
  assert.deepEqual(candidates("/rm ").map((item) => item.label), ["chat", "reset"]);
  for (const input of ["/image_model seedream ", "/image_model seedream high", "/image_model chat"]) {
    assert.deepEqual(candidates(input), []);
  }
  const candidate = candidates("/image_model seedream_b")[0];
  assert.equal(buildGhostText({ input: "/image_model seedream_b", candidates: [candidate], selectedIndex: 0, visible: true }), "ackup");
  assert.deepEqual(acceptCommandCompletion({ input: "/image_model seedream_b", candidate }), { input: "/image_model seedream_backup ", cursor: 29 });
  assert.equal(buildInlineHint({ input: "/image_model ", candidates: [] }), "<model_name>");
  assert.equal(buildInlineHint({ input: "/image_model seedream ", candidates: [] }), null);
  assert.match(presentCommandHelp().text || "", /\/image_model \[model_name\]/);
});

test("image picker and direct selection stay in the ordinary FIFO", () => {
  for (const input of ["/image_model", "/image_model seedream"]) {
    assert.equal(getCommandExecutionPolicy(input), "fifo");
    assert.equal(shouldQueueSubmission({ input, streaming: true, queuedCount: 1 }), true);
    assert.equal(shouldQueueSubmission({ input, streaming: false, queuedCount: 1 }), true);
    assert.equal(shouldQueueSubmission({ input, streaming: true, fromQueue: true }), false);
  }
});

test("stdio and Web clients send image catalog and sparse image updates", async () => {
  const expected = harness().catalog;
  const configured = { enabled: false, runner_id: "runner", image_model_name: "seedream_backup" };
  const cli = new GatewayClient();
  const requests: any[] = [];
  (cli as any).process = { stdin: { write: (raw: string) => requests.push(JSON.parse(raw)) } };
  const list = cli.listImageModels({ base_dir: baseDir });
  const set = cli.setImageConfig({ base_dir: baseDir, model_name: "seedream_backup" });
  assert.deepEqual(requests.map(({ method, params }) => ({ method, params })), [
    { method: "list_image_models", params: { base_dir: baseDir } },
    { method: "set_image_config", params: { base_dir: baseDir, model_name: "seedream_backup" } },
  ]);
  for (const [index, result] of [expected, configured].entries()) {
    (cli as any).handleResponse(JSON.stringify({ jsonrpc: "2.0", id: requests[index].id, result }));
  }
  assert.deepEqual(await list, expected);
  assert.deepEqual(await set, configured);

  const web = new WebGatewayClient();
  const frames: any[] = [];
  (web as any).socket = { readyState: WebSocket.OPEN, send: (raw: string) => frames.push(JSON.parse(raw)) };
  const webList = web.listImageModels(baseDir);
  const webSet = web.setImageConfig({ model_name: "seedream_backup" });
  assert.deepEqual(frames.map(({ type, payload }) => ({ type, payload })), [
    { type: "list_image_models", payload: { base_dir: baseDir } },
    { type: "set_image_config", payload: { model_name: "seedream_backup" } },
  ]);
  for (const [index, payload] of [expected, configured].entries()) {
    (web as any).handleMessage(JSON.stringify({ id: frames[index].id, type: "result", payload }));
  }
  assert.deepEqual(await webList, expected);
  assert.deepEqual(await webSet, configured);
});
