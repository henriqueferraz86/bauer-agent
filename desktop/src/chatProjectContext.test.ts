import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api/client";
import {
  ChatContextLoader, ChatProjectContext, chatContextDescription, fetchChatProjectContext, parseChatProjectContext,
} from "./chatProjectContext";

const projectA = { id: "a", name: "Projeto A", path: "/projetos/a" };
const projectB = { id: "b", name: "Projeto B", path: "C:\\projetos\\b" };
const contextA: ChatProjectContext = {
  session_id: "session-a", project: projectA, workspace: projectA.path,
  source: "session", active_project: projectA, warning: null,
};

afterEach(() => vi.restoreAllMocks());

describe("chat project metadata", () => {
  it("parses public metadata from HTTP and SSE without adding unrelated payload fields", () => {
    expect(parseChatProjectContext({ ...contextA, tool_result: "internal" })).toEqual(contextA);
    expect(parseChatProjectContext(JSON.stringify(contextA))).toEqual(contextA);
    expect(parseChatProjectContext({
      ...contextA, session_id: null, project: null, source: "default", workspace: "/default", active_project: null,
    })).toMatchObject({ project: null, workspace: "/default", source: "default" });
  });

  it("rejects malformed context instead of displaying stale or invented project details", () => {
    for (const payload of ["{", null, [], {}, { ...contextA, workspace: "" },
      { ...contextA, source: "other" }, { ...contextA, project: { id: "a" } },
      { ...contextA, active_project: "a" }, { ...contextA, warning: {} }]) {
      expect(parseChatProjectContext(payload)).toBeNull();
    }
  });

  it("queries only the session and leaves project selection to the backend", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(contextA);
    expect(await fetchChatProjectContext("session-a")).toEqual(contextA);
    expect(get).toHaveBeenLastCalledWith("/chat/context?session_id=session-a");
    get.mockResolvedValue({ ...contextA, session_id: null, source: "active" });
    await fetchChatProjectContext("");
    expect(get).toHaveBeenLastCalledWith("/chat/context");
  });

  it("rejects malformed endpoint responses", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ workspace: "/default" });
    await expect(fetchChatProjectContext("")).rejects.toThrow("inválido");
  });

  it("shows a session's workspace and explains which project a new session uses", () => {
    const changed = { ...contextA, active_project: projectB };
    expect(chatContextDescription(changed)).toEqual({
      label: "Vinculado à sessão",
      warning: "O projeto ativo é Projeto B. Nova sessão usa Projeto B (C:\\projetos\\b).",
    });
    expect(chatContextDescription(contextA).warning).toBeNull();
    expect(chatContextDescription({ ...contextA, source: "active" }).label).toBe("Projeto ativo");
  });

  it("keeps the backend's fallback warning and displays the actual default workspace", () => {
    const fallback: ChatProjectContext = {
      ...contextA, project: null, source: "default", workspace: "/default",
      warning: "Pasta do projeto ausente. Usando workspace padrão.",
    };
    expect(chatContextDescription(fallback).label).toBe("Workspace padrão");
    expect(chatContextDescription({ ...fallback, source: "session" }).label).toBe("Workspace padrão");
    expect(chatContextDescription(fallback).warning).toContain(fallback.warning);
    expect(chatContextDescription(fallback).warning).not.toContain("Nova sessão usa");
    expect(parseChatProjectContext(fallback)?.workspace).toBe("/default");
  });
});

describe("context refresh ordering", () => {
  it("a new session ignores the previous session's delayed response", async () => {
    const loader = new ChatContextLoader();
    loader.reset("session-a");
    let finish!: (context: ChatProjectContext) => void;
    const old = loader.load(async () => new Promise((resolve) => { finish = resolve; }));
    loader.reset("");
    const newContext: ChatProjectContext = { ...contextA, session_id: null, project: projectB, source: "active", workspace: projectB.path };
    expect(await loader.load(async () => newContext)).toEqual({ context: newContext, unavailable: false });
    finish(contextA);
    expect(await old).toBeNull();
  });

  it("authoritative SSE metadata supersedes both a delayed read and a delayed failure", async () => {
    const loader = new ChatContextLoader();
    loader.reset("session-a");
    let finish!: () => void;
    const old = loader.load(async () => {
      await new Promise<void>((resolve) => { finish = resolve; });
      throw new Error("late failure");
    });
    loader.invalidate();
    finish();
    expect(await old).toBeNull();
    expect(await loader.load(async () => contextA)).toEqual({ context: contextA, unavailable: false });
  });

  it("rejects another session's context and reports current request failures", async () => {
    const loader = new ChatContextLoader();
    loader.reset("session-b");
    expect(await loader.load(async () => contextA)).toEqual({ context: null, unavailable: true });
    expect(await loader.load(async () => { throw new Error("unavailable"); })).toEqual({ context: null, unavailable: true });
  });

  it("keeps the newest polling result when requests complete out of order", async () => {
    const loader = new ChatContextLoader();
    loader.reset("session-a");
    let finish!: (context: ChatProjectContext) => void;
    const old = loader.load(async () => new Promise((resolve) => { finish = resolve; }));
    const changed = { ...contextA, active_project: projectB };
    expect(await loader.load(async () => changed)).toEqual({ context: changed, unavailable: false });
    finish(contextA);
    expect(await old).toBeNull();
  });
});
