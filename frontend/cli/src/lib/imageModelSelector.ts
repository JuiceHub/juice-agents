/** Image model selection shares the generic overlay and direct command path. */

import type { RuntimeImageModelInfo } from "@juice-agents/shared/gateway/types";
import type { GatewayClient } from "../gateway/client.js";
import type { useInteractiveSelector } from "../hooks/useInteractiveSelector.js";
import { executeCommand } from "./commands.js";
import type { MessageBlock } from "./presenter.js";

/**
 * Read fresh model/current-state metadata each time the picker opens. Loading
 * and cancelling never write configuration; confirmation uses the command's
 * validation and error presentation and never implicitly enables the tools.
 */
export async function openImageModelSelector(params: {
  client: GatewayClient;
  baseDir: string;
  selector: Pick<ReturnType<typeof useInteractiveSelector>, "open">;
  addMessages: (blocks: MessageBlock[]) => void;
  onCatalogLoaded?: (models: RuntimeImageModelInfo[]) => void;
}): Promise<void> {
  const { client, baseDir, selector, addMessages } = params;
  const catalog = await client.listImageModels({ base_dir: baseDir });
  params.onCatalogLoaded?.(catalog.image_models);
  if (!catalog.image_models.length) {
    addMessages([{ kind: "error", text: "No image models configured. Add models to image_models in your configuration." }]);
    return;
  }
  selector.open(
    `Select Image Model (${catalog.image_enabled ? "enabled" : "disabled"})`,
    catalog.image_models.map((model) => ({
      label: model.model_name,
      value: model.model_name,
      description: `${model.provider} | ${model.backend} | ${model.provider_model_name}`,
      active: model.model_name === catalog.current_image_model_name,
      backend: model.backend,
    })),
    async (item) => {
      const result = await executeCommand(`/image_model ${item.value}`, client, baseDir);
      addMessages(result.blocks);
    },
  );
}
