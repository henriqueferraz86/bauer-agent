import { api } from "./api/client";

export const MODEL_SELECTION_EVENT = "bauer:model-selection";
export interface ModelSelection { model: string; provider: string }
let revision = 0;

function publish(selection: ModelSelection): void {
  window.dispatchEvent(new CustomEvent(MODEL_SELECTION_EVENT, { detail: selection }));
}

export async function refreshModelSelection(): Promise<ModelSelection | null> {
  const startedAt = revision;
  const status = await api.get<ModelSelection>("/status");
  // A slow status request started before a switch cannot undo the new HUD.
  if (startedAt !== revision) return null;
  const selection = { model: status.model, provider: status.provider || "" };
  publish(selection);
  return selection;
}

export async function switchModel(model: string, provider?: string): Promise<ModelSelection> {
  revision += 1;
  const response = await api.post<{ active: string; provider: string }>("/models/switch", {
    model, ...(provider ? { provider } : {}),
  });
  revision += 1;
  const selection = { model: response.active, provider: response.provider || "" };
  publish(selection);
  return selection;
}
