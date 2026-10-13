import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api/client";
import { MODEL_SELECTION_EVENT, refreshModelSelection, switchModel } from "./modelSelection";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function events() {
  const emitted: unknown[] = [];
  vi.stubGlobal("window", { dispatchEvent: (event: Event) => { emitted.push(event); } });
  vi.stubGlobal("CustomEvent", class extends Event {
    detail: unknown;
    constructor(type: string, options: { detail: unknown }) { super(type); this.detail = options.detail; }
  });
  return emitted;
}

describe("model selection publication", () => {
  it("publishes the backend's actual model/provider after switching", async () => {
    const emitted = events();
    vi.spyOn(api, "post").mockResolvedValue({ active: "resolved-model", provider: "ollama" });
    expect(await switchModel("requested-model", "ollama")).toEqual({ model: "resolved-model", provider: "ollama" });
    expect(emitted[0]).toMatchObject({ type: MODEL_SELECTION_EVENT, detail: { model: "resolved-model", provider: "ollama" } });
  });

  it("ignores a status response started before a successful switch", async () => {
    const emitted = events();
    let resolve!: (status: unknown) => void;
    vi.spyOn(api, "get").mockImplementation(() => new Promise((done) => { resolve = done; }));
    vi.spyOn(api, "post").mockResolvedValue({ active: "new", provider: "ollama" });
    const loading = refreshModelSelection();
    await switchModel("new");
    resolve({ model: "old", provider: "ollama" });
    expect(await loading).toBeNull();
    expect(emitted).toHaveLength(1);
  });

  it("publishes status changes made by another component", async () => {
    const emitted = events();
    vi.spyOn(api, "get").mockResolvedValue({ model: "updated", provider: "ollama" });
    expect(await refreshModelSelection()).toEqual({ model: "updated", provider: "ollama" });
    expect(emitted[0]).toMatchObject({ detail: { model: "updated", provider: "ollama" } });
  });

  it("ignores a stale status request started during an in-flight switch", async () => {
    const emitted = events();
    let resolveSwitch!: (value: unknown) => void;
    let resolveStatus!: (value: unknown) => void;
    vi.spyOn(api, "post").mockImplementation(() => new Promise((done) => { resolveSwitch = done; }));
    vi.spyOn(api, "get").mockImplementation(() => new Promise((done) => { resolveStatus = done; }));
    const switching = switchModel("new");
    const loading = refreshModelSelection();
    resolveSwitch({ active: "new", provider: "ollama" });
    await switching;
    resolveStatus({ model: "old", provider: "ollama" });
    expect(await loading).toBeNull();
    expect(emitted).toHaveLength(1);
  });
});
