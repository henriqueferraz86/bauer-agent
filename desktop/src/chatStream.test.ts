import { describe, expect, it } from "vitest";
import { currentChatTurn, runChatTurn, subscribeChatTurn, ChatTurnState } from "./chatStream";
import { SSEEvent } from "./api/client";

describe("chat stream lifecycle", () => {
  it("persists the final response and session after the screen unsubscribes", async () => {
    let emit!: (event: SSEEvent) => void;
    let finish!: () => void;
    const persisted: ChatTurnState[] = [];
    const visible: ChatTurnState[] = [];
    const unsubscribe = subscribeChatTurn((state) => visible.push(structuredClone(state)));
    const running = runChatTurn({ messages: [], sessionId: "" }, "hello", (state) => persisted.push(structuredClone(state)),
      async (_path, callback) => {
        emit = callback;
        await new Promise<void>((resolve) => { finish = resolve; });
      });
    emit({ event: "message", data: "first" });
    unsubscribe();
    const lastVisibleCount = visible.length;
    emit({ event: "message", data: " final" });
    emit({ event: "done", data: "saved-session" });
    finish();
    expect(await running).toBe("first final");
    expect(visible.length).toBe(lastVisibleCount);
    const final = persisted[persisted.length - 1];
    expect(final).toMatchObject({ busy: false, sessionId: "saved-session" });
    expect(final.messages[final.messages.length - 1]).toMatchObject({ text: "first final", streaming: false });
    expect(currentChatTurn()).toBeNull();
  });

  it("a returning screen receives the active turn and prevents overlapping sends", async () => {
    let finish!: () => void;
    const running = runChatTurn({ messages: [], sessionId: "existing" }, "hello", () => {},
      async () => { await new Promise<void>((resolve) => { finish = resolve; }); });
    let resumed: ChatTurnState | undefined;
    const unsubscribe = subscribeChatTurn((state) => { resumed = state; });
    expect(resumed).toMatchObject({ busy: true, sessionId: "existing" });
    await expect(runChatTurn({ messages: [], sessionId: "" }, "other", () => {})).rejects.toThrow("already running");
    finish();
    await running;
    expect(resumed?.busy).toBe(false);
    unsubscribe();
  });

  it("terminates and persists a failed transport without leaving chat busy", async () => {
    let final: ChatTurnState | undefined;
    await runChatTurn({ messages: [], sessionId: "known" }, "hello", (state) => { final = state; },
      async () => { throw new Error("controlled unavailable"); });
    expect(final?.busy).toBe(false);
    const last = final?.messages[final.messages.length - 1];
    expect(last?.text).toContain("Não foi possível concluir");
    expect(last?.streaming).toBe(false);
    expect(currentChatTurn()).toBeNull();
  });

  it("finishes the transport and releases busy when auxiliary storage or views fail", async () => {
    const unsubscribe = subscribeChatTurn(() => { throw new Error("broken view"); });
    try {
      const answer = await runChatTurn({ messages: [], sessionId: "" }, "hello",
        () => { throw new Error("storage quota"); },
        async (_path, emit) => {
          emit({ event: "message", data: "completed" });
          emit({ event: "done", data: "persisted-server-session" });
        });
      expect(answer).toBe("completed");
      expect(currentChatTurn()).toBeNull();
    } finally { unsubscribe(); }
  });
});
