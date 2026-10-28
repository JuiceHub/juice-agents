/** Reward-model selection uses the same overlay and command path as the CLI. */

import type { RuntimeModelInfo } from "@juice-agents/shared/gateway/types";
import type { GatewayClient } from "../gateway/client.js";
import type { useInteractiveSelector } from "../hooks/useInteractiveSelector.js";
import { executeCommand } from "./commands.js";
import type { MessageBlock } from "./presenter.js";

/**
 * Load current scoring state without starting a session. Opening or cancelling
 * the selector never configures a model; only its confirmation callback writes.
 * Keep this controller outside React so both loading and confirmation can be
 * verified using the real command handler with an offline gateway double.
 */
export async function openRewardModelSelector(params: {
  client: GatewayClient;
  baseDir: string;
  models: RuntimeModelInfo[];
  selector: Pick<ReturnType<typeof useInteractiveSelector>, "open">;
  addMessages: (blocks: MessageBlock[]) => void;
}): Promise<void> {
  const { client, baseDir, selector, addMessages } = params;
  // Reuse the startup catalog, just like `/model`; only fetch it when startup
  // has not supplied one. Current RM state is read afresh on every opening.
  const [configuration, models] = await Promise.all([
    client.configureRewardModel({ base_dir: baseDir }),
    params.models.length ? Promise.resolve(params.models) : client.listModels({ base_dir: baseDir }),
  ]);
  selector.open(
    "Select Reward Model",
    [
      {
        label: "No reward model",
        value: "",
        description: configuration.scorer_configured && !configuration.model_name
          ? "Clear the current SDK scorer; keep candidate count"
          : "Disable scoring; keep candidate count",
        active: !configuration.scorer_configured,
      },
      // `reset` is the reserved direct-command action, also excluded from /rm
      // completion. The selector's empty value distinguishes its clear action.
      ...models.filter((model) => model.model_name !== "reset").map((model) => ({
        label: model.model_name,
        value: model.model_name,
        description: `${model.backend} | ${model.provider_model_name}`,
        active: configuration.scorer_configured && model.model_name === configuration.model_name,
        backend: model.backend,
      })),
    ],
    async (item) => {
      // Share catalog validation, RPC handling and feedback with direct /rm
      // commands. This path never invokes /model or changes candidate breadth.
      const result = await executeCommand(`/rm ${item.value || "reset"}`, client, baseDir);
      addMessages(result.blocks);
    },
  );
}
