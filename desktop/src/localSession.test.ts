import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearLocalSession, listenForRemoteLogout, LOGOUT_SIGNAL_KEY, logoutAndClear, onLocalSessionCleared,
} from "./localSession";
import { currentChatTurn, runChatTurn, ChatTurnState } from "./chatStream";
import { SSEEvent } from "./api/client";

// WEB-08 (auditoria 2026-10-12): logout revogava o cookie, mas a conversa e o
// session_id continuavam em localStorage e reidratavam no próximo login.

class MemoryStorage implements Storage {
  private data = new Map<string, string>();
  writes: string[] = [];
  get length(): number { return this.data.size; }
  clear(): void { this.data.clear(); }
  getItem(key: string): string | null { return this.data.has(key) ? this.data.get(key)! : null; }
  key(index: number): string | null { return [...this.data.keys()][index] ?? null; }
  removeItem(key: string): void { this.data.delete(key); }
  setItem(key: string, value: string): void { this.writes.push(key); this.data.set(key, String(value)); }
}

let local: MemoryStorage;
let session: MemoryStorage;

beforeEach(() => {
  local = new MemoryStorage();
  session = new MemoryStorage();
  vi.stubGlobal("localStorage", local);
  vi.stubGlobal("sessionStorage", session);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("logout limpa o storage local (WEB-08)", () => {
  it("apaga a conversa, o session_id e qualquer chave bauer.* nos dois storages", async () => {
    local.setItem("bauer.chatState.v1", JSON.stringify({ messages: [{ role: "user", text: "segredo" }], sessionId: "s1" }));
    local.setItem("bauer.apiKey", "legado");
    local.setItem("outra-app", "fica");
    session.setItem("bauer.qualquer", "x");
    const revoke = vi.fn(async () => ({}));

    await logoutAndClear(revoke);

    expect(revoke).toHaveBeenCalledOnce();
    expect(local.getItem("bauer.chatState.v1")).toBeNull();
    expect(local.getItem("bauer.apiKey")).toBeNull();
    expect(session.getItem("bauer.qualquer")).toBeNull();
    expect(local.getItem("outra-app")).toBe("fica");
    expect(local.getItem(LOGOUT_SIGNAL_KEY)).toBeNull();
  });

  it("limpa mesmo quando a revogação no servidor falha", async () => {
    local.setItem("bauer.chatState.v1", "{}");
    await expect(logoutAndClear(async () => { throw new Error("rede"); })).rejects.toThrow("rede");
    expect(local.getItem("bauer.chatState.v1")).toBeNull();
  });

  it("sinaliza as outras abas pelo evento storage", () => {
    clearLocalSession();
    expect(local.writes).toContain(LOGOUT_SIGNAL_KEY);
  });

  it("a aba que recebe o sinal limpa a memória e volta ao login", () => {
    const target = new EventTarget() as unknown as Window;
    const reset = vi.fn();
    const off = onLocalSessionCleared(reset);
    const onRemote = vi.fn();
    const stop = listenForRemoteLogout(onRemote, target);
    local.setItem("bauer.chatState.v1", "{}");

    const event = new Event("storage") as StorageEvent;
    Object.assign(event, { key: LOGOUT_SIGNAL_KEY, newValue: "1" });
    target.dispatchEvent(event);
    const removal = new Event("storage") as StorageEvent;
    Object.assign(removal, { key: LOGOUT_SIGNAL_KEY, newValue: null });
    target.dispatchEvent(removal);

    expect(onRemote).toHaveBeenCalledOnce();
    expect(reset).toHaveBeenCalledOnce();
    expect(local.getItem("bauer.chatState.v1")).toBeNull();
    stop();
    off();
  });

  it("storage indisponível não impede o logout", async () => {
    vi.stubGlobal("localStorage", { get length() { throw new Error("bloqueado"); } });
    await expect(logoutAndClear(async () => ({}))).resolves.toBeUndefined();
  });

  it("um turno de chat em andamento não regrava a conversa depois do logout", async () => {
    let emit!: (event: SSEEvent) => void;
    let finish!: () => void;
    const persist = (state: ChatTurnState) => {
      localStorage.setItem("bauer.chatState.v1", JSON.stringify(state));
    };
    const running = runChatTurn({ messages: [], sessionId: "" }, "pergunta sensível", persist, async (_path, callback) => {
      emit = callback;
      await new Promise<void>((resolve) => { finish = resolve; });
    });
    emit({ event: "message", data: "parte 1" });
    expect(local.getItem("bauer.chatState.v1")).toContain("parte 1");

    clearLocalSession();
    expect(currentChatTurn()).toBeNull();

    emit({ event: "message", data: "parte 2 depois do logout" });
    emit({ event: "done", data: "sessao" });
    finish();
    await running;

    expect(local.getItem("bauer.chatState.v1")).toBeNull();
    expect(currentChatTurn()).toBeNull();
  });
});
