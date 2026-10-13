import { describe, expect, it } from "vitest";
import { currentChatTurn, runChatTurn, subscribeChatTurn, ChatTurnState } from "./chatStream";
import { SSEEvent } from "./api/client";

describe("chat stream lifecycle", () => {
  it("keeps authoritative context across navigation without placing metadata in the answer", async () => {
    const project = { id: "a", name: "Projeto A", path: "/projetos/a" };
    const context = {
      session_id: "session-a", project, workspace: project.path,
      source: "active", active_project: project, warning: null,
    };
    let emit!: (event: SSEEvent) => void;
    let finish!: () => void;
    let final: ChatTurnState | undefined;
    let requestPath = "";
    const running = runChatTurn({ messages: [], sessionId: "" }, "resuma o projeto", (state) => { final = state; },
      async (path, callback) => {
        requestPath = path;
        emit = callback;
        await new Promise<void>((resolve) => { finish = resolve; });
      });
    emit({ event: "context", data: JSON.stringify(context) });
    let resumed: ChatTurnState | undefined;
    const unsubscribe = subscribeChatTurn((state) => { resumed = state; });
    expect(resumed).toMatchObject({ sessionId: "session-a", projectContext: context });
    expect(resumed?.messages[1].text).toBe("");
    unsubscribe();
    emit({ event: "message", data: "Resumo do projeto A" });
    emit({ event: "done", data: "session-a" });
    finish();
    expect(await running).toBe("Resumo do projeto A");
    expect(final).toMatchObject({ sessionId: "session-a", projectContext: context, busy: false });
    expect(new URLSearchParams(requestPath.split("?")[1]).has("project_id")).toBe(false);
  });

  it("discards malformed context events without revealing them as response text", async () => {
    const answer = await runChatTurn({ messages: [], sessionId: "existing" }, "hello", () => {},
      async (path, emit) => {
        const query = new URLSearchParams(path.split("?")[1]);
        expect(query.get("session_id")).toBe("existing");
        expect(query.has("project_id")).toBe(false);
        emit({ event: "context", data: "{invalid context" });
        emit({ event: "message", data: "Resposta" });
        emit({ event: "done", data: "existing" });
      });
    expect(answer).toBe("Resposta");
  });

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
