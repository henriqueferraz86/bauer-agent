import { describe, expect, it, vi } from "vitest";
import { currentChatTurn, runChatTurn, subscribeChatTurn, updateChatApproval, ChatTurnState } from "./chatStream";
import { SSEEvent } from "./api/client";
import { ChatActivitySnapshot, chatActivityStore } from "./chatActivity";

describe("chat stream lifecycle", () => {
  it("keeps approval cards across navigation and updates them without replacing turn text", async () => {
    let emit!: (event: SSEEvent) => void;
    let finish!: () => void;
    let saved: ChatTurnState | undefined;
    const running = runChatTurn({ messages: [], sessionId: "" }, "validar navegador", (state) => { saved = state; }, async (_path, callback) => {
      emit = callback;
      await new Promise<void>((resolve) => { finish = resolve; });
    });
    emit({ event: "approval", data: JSON.stringify({ id: "approval-1", session_id: "approval-session", run_id: "run-1", operation: "shell.execute", tool_name: "run_command", status: "pending", reason: "approval needed" }) });
    expect(saved?.sessionId).toBe("approval-session");
    expect(saved?.messages[1].text).toBe("");
    let resumed: ChatTurnState | undefined;
    const off = subscribeChatTurn((state) => { resumed = state; });
    expect(resumed?.messages[1].approvals?.["approval-1"].status).toBe("pending");
    updateChatApproval("approval-1", { submitting: true });
    expect(resumed?.messages[1].approvals?.["approval-1"].submitting).toBe(true);
    emit({ event: "approval", data: JSON.stringify({ id: "approval-1", status: "approved" }) });
    emit({ event: "message", data: "validado" });
    emit({ event: "done", data: "approval-session" });
    finish();
    expect(await running).toBe("validado");
    expect(saved?.messages[1].approvals?.["approval-1"].status).toBe("approved");
    expect(saved?.messages[1].approvalTurnEnded).toBe(true);
    off();
  });
  it("separates internal activity from text through the actual fragmented SSE parser", async () => {
    const snapshot = { session_id: "network-session", runs: [] };
    const wire = 'event: internal\ndata: ' + JSON.stringify({ kind: "chat.activity", payload: snapshot })
      + '\n\nevent: future-control\ndata: {"private":true}\n\ndata: Resposta pública\n\nevent: done\ndata: network-session\n\n';
    const encoded = new TextEncoder().encode(wire);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new ReadableStream({
      start(controller) {
        for (let i = 0; i < encoded.length; i += 7) controller.enqueue(encoded.slice(i, i + 7));
        controller.close();
      },
    }))));
    try {
      expect(await runChatTurn({ messages: [], sessionId: "network-session" }, "teste", () => {})).toBe("Resposta pública");
      expect(chatActivityStore.current()).toMatchObject({ snapshot, status: "ready" });
    } finally { vi.unstubAllGlobals(); }
  });
  it("receives backwards compatible internal activity without rendering JSON or future control events", async () => {
    const snapshot = { session_id: "compatible-session", runs: [] };
    const answer = await runChatTurn({ messages: [], sessionId: "compatible-session" }, "teste", () => {}, async (_path, emit) => {
      emit({ event: "internal", data: JSON.stringify({ kind: "chat.activity", payload: snapshot }) });
      emit({ event: "internal", data: "{bad json" });
      emit({ event: "future-control", data: '{"secret":"metadata"}' });
      emit({ event: "message", data: "Resposta formatada" });
      emit({ event: "done", data: "compatible-session" });
    });
    expect(answer).toBe("Resposta formatada");
    expect(chatActivityStore.current()).toMatchObject({ snapshot, status: "ready" });
  });
  it("activity metadata survives navigation and transport done never declares a running task complete", async () => {
    const snapshot: ChatActivitySnapshot = {
      session_id: "activity-session", runs: [{
        run_id: "activity-run", task: "Revisar código", status: "running", agent_id: "bauer", agent_name: "Bauer",
        runtime: "kernel", started_at: "2026-10-13T12:00:00Z", updated_at: "2026-10-13T12:00:01Z", agents: [],
      }],
    };
    let emit!: (event: SSEEvent) => void;
    let finish!: () => void;
    let saved: ChatTurnState | undefined;
    const running = runChatTurn({ messages: [], sessionId: "activity-session" }, "revise", (state) => { saved = state; },
      async (_path, callback) => {
        emit = callback;
        await new Promise<void>((resolve) => { finish = resolve; });
      });
    emit({ event: "activity", data: JSON.stringify(snapshot) });
    let resumed: ChatTurnState | undefined;
    const off = subscribeChatTurn((state) => { resumed = state; });
    expect(resumed?.activity).toEqual(snapshot);
    expect(resumed?.messages[1].text).toBe("");
    off();
    emit({ event: "message", data: "Ainda em execução no servidor" });
    emit({ event: "done", data: "activity-session" });
    finish();
    expect(await running).toBe("Ainda em execução no servidor");
    expect(saved?.activity?.runs[0].status).toBe("running");
    expect(chatActivityStore.current()).toMatchObject({ snapshot, status: "ready" });
    await chatActivityStore.refresh(async () => ({ ...snapshot, runs: [{ ...snapshot.runs[0], status: "completed" }] }));
    expect(chatActivityStore.current().snapshot?.runs[0].status).toBe("completed");
  });

  it("ignores another session's activity and signals uncertainty on a failed transport", async () => {
    const snapshot: ChatActivitySnapshot = { session_id: "another-session", runs: [] };
    const response = await runChatTurn({ messages: [], sessionId: "selected-session" }, "teste", () => {}, async (_path, emit) => {
      emit({ event: "activity", data: JSON.stringify(snapshot) });
      emit({ event: "activity", data: "{ malformed payload" });
      throw new Error("stream disconnected");
    });
    expect(response).toBe("");
    expect(chatActivityStore.current()).toMatchObject({ sessionId: "selected-session", snapshot: null, status: "unavailable" });
  });

  it("a null activity fallback signals unknown status while preserving the public answer", async () => {
    const answer = await runChatTurn({ messages: [], sessionId: "selected-session" }, "teste", () => {}, async (_path, emit) => {
      emit({ event: "activity", data: "null" });
      emit({ event: "message", data: "Resposta preservada" });
      emit({ event: "done", data: "selected-session" });
    });
    expect(answer).toBe("Resposta preservada");
    expect(chatActivityStore.current().status).toBe("unavailable");
  });

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
