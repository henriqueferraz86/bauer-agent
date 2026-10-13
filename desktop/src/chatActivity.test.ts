import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { api } from "./api/client";
import {
  ChatActivitySnapshot, ChatActivityStore, RunActivity, activityPollDelay,
  activityStatusDisplay, fetchChatActivity, parseChatActivity, stripLeakedActivityPrefix,
} from "./chatActivity";
import ChatActivityPanel from "./components/ChatActivityPanel";

const run: RunActivity = {
  run_id: "run-a", task: "Investigar arquivos do projeto", status: "running", agent_id: "bauer",
  agent_name: "Bauer", runtime: "kernel", started_at: "2026-10-13T12:00:00Z", updated_at: "2026-10-13T12:00:01Z",
  agents: [{
    agent_id: "bauer", name: "Bauer", task: "Investigar arquivos do projeto", status: "running",
    activity: "Lendo arquivos", tool_name: "read_file", updated_at: "2026-10-13T12:00:01Z",
  }, {
    agent_id: "reviewer", name: "Revisor", task: "Revisar os testes", status: "waiting_approval",
    activity: "Aguardando aprovação", tool_name: null, updated_at: "2026-10-13T12:00:01Z",
  }],
};
const snapshot: ChatActivitySnapshot = { session_id: "session-a", runs: [run] };

afterEach(() => vi.restoreAllMocks());

describe("public activity payload", () => {
  it("repairs only the leaked snapshot prefix matching this session and preceding task", () => {
    const leaked = JSON.stringify(snapshot);
    expect(stripLeakedActivityPrefix(leaked + "Resumo preservado", "session-a", run.task)).toBe("Resumo preservado");
    expect(stripLeakedActivityPrefix(leaked, "session-a", run.task)).toBe("");
    expect(stripLeakedActivityPrefix(leaked, "session-b", run.task)).toBe(leaked);
    expect(stripLeakedActivityPrefix(leaked, "session-a", "outra tarefa")).toBe(leaked);
    expect(stripLeakedActivityPrefix("```json\n" + leaked, "session-a", run.task)).toContain(leaked);
    expect(stripLeakedActivityPrefix('{"session_id": "incompleto', "session-a", run.task)).toContain("incompleto");
    const quoted = { ...snapshot, runs: [{ ...run, task: 'Revisar {"chave": "valor"}' }] };
    expect(stripLeakedActivityPrefix(JSON.stringify(quoted) + "Resposta", "session-a", quoted.runs[0].task)).toBe("Resposta");
  });
  it("parses HTTP/SSE metadata and drops internal fields and tool arguments", () => {
    const value = { ...snapshot, provider: "internal", runs: [{ ...run, prompt: "private", agents: run.agents.map((agent) => ({ ...agent, arguments: "private" })) }] };
    expect(parseChatActivity(value)).toEqual(snapshot);
    expect(parseChatActivity(JSON.stringify(value))).toEqual(snapshot);
    expect(parseChatActivity({ session_id: null, runs: [] })).toEqual({ session_id: null, runs: [] });
  });

  it("redacts bounded public text, normalizes unrecognized states and removes unsafe tool names", () => {
    const value = { ...snapshot, runs: [{ ...run, task: `API_KEY=secret-value ${"x".repeat(1000)}`,
      agents: [{ ...run.agents[0], name: "Bauer\n\u0000", activity: "Authorization=Bearer secret\nTOKEN=hidden", status: "invented", tool_name: "tool {arguments}" }] }] };
    const parsed = parseChatActivity(value)!;
    expect(parsed.runs[0].task.length).toBeLessThanOrEqual(800);
    expect(parsed.runs[0].task).toContain("API_KEY=[REDACTED]");
    expect(parsed.runs[0].task).not.toContain("secret-value");
    expect(parsed.runs[0].agents[0]).toMatchObject({ name: "Bauer", status: "unknown", tool_name: null });
    expect(parsed.runs[0].agents[0].activity).not.toContain("hidden");
    expect(parsed.runs[0].agents[0].activity).not.toContain("secret");
    expect(parsed.runs[0].agents[0].activity).not.toContain("\n");
  });

  it("accepts custom agent identities containing spaces and accents and redacts quoted secret assignments", () => {
    const value = { ...snapshot, runs: [{ ...run, agent_id: "Revisor de código",
      agents: [{ ...run.agents[0], agent_id: "Revisor de código", name: "Revisor de código", activity: 'Revisando "API_KEY": "very-secret"' }] }] };
    const parsed = parseChatActivity(value)!;
    expect(parsed.runs[0].agent_id).toBe("Revisor de código");
    expect(parsed.runs[0].agents[0].name).toBe("Revisor de código");
    expect(parsed.runs[0].agents[0].activity).not.toContain("very-secret");
    expect(parseChatActivity({ ...snapshot, runs: [{ ...run, agent_id: "bad\nidentity" }] })).toBeNull();
  });

  it("rejects invalid IDs, dates, shape and oversized serialized payloads", () => {
    for (const value of [null, "{", {}, { ...snapshot, session_id: "../ bad" }, { ...snapshot, runs: {} },
      { ...snapshot, runs: [{ ...run, run_id: "bad id" }] }, { ...snapshot, runs: [{ ...run, started_at: "invalid" }] },
      { ...snapshot, runs: [{ ...run, agents: [{ ...run.agents[0], name: null }] }] }, "x".repeat(128001)]) {
      expect(parseChatActivity(value)).toBeNull();
    }
  });

  it("bounds runs/agents, deduplicates identity and prioritizes active runs", () => {
    const agents = Array.from({ length: 60 }, (_, index) => ({ ...run.agents[0], agent_id: `agent-${index}` }));
    const many = Array.from({ length: 10 }, (_, index) => ({ ...run, run_id: `run-${index}`, agents }));
    many[0].status = "completed";
    many[1].agents[1].agent_id = many[1].agents[0].agent_id;
    const parsed = parseChatActivity({ ...snapshot, runs: many })!;
    expect(parsed.runs).toHaveLength(5);
    expect(parsed.runs[0].run_id).toBe("run-1");
    expect(parsed.runs[0].agents).toHaveLength(39);
    expect(parsed.runs[4].status).toBe("completed");
  });

  it("fetches only the selected session and rejects malformed endpoint responses", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(snapshot);
    expect(await fetchChatActivity("session-a")).toEqual(snapshot);
    expect(get).toHaveBeenCalledWith("/chat/activity?session_id=session-a");
    get.mockResolvedValue({ runs: null });
    await expect(fetchChatActivity("session-a")).rejects.toThrow("inválida");
  });
});

describe("activity state and request ordering", () => {
  it("does not claim a task before the backend confirms one", async () => {
    const store = new ChatActivityStore();
    expect(store.current().status).toBe("awaiting");
    const fetch = vi.fn(async () => snapshot);
    await store.refresh(fetch);
    expect(fetch).not.toHaveBeenCalled();
    store.selectSession("session-a");
    expect(store.current()).toMatchObject({ snapshot: null, status: "loading" });
    await store.refresh(async () => ({ session_id: "session-a", runs: [] }));
    expect(store.current()).toMatchObject({ snapshot: { runs: [] }, status: "ready" });
  });

  it("session reset clears old activity and rejects a delayed response", async () => {
    const store = new ChatActivityStore();
    store.selectSession("session-a");
    store.accept(snapshot);
    let finish!: (value: ChatActivitySnapshot) => void;
    const old = store.refresh(async () => new Promise((resolve) => { finish = resolve; }));
    store.selectSession("", true);
    finish(snapshot);
    await old;
    expect(store.current()).toEqual({ sessionId: "", snapshot: null, status: "awaiting" });
    expect(store.accept(snapshot)).toBe(false);
  });

  it("an SSE snapshot supersedes pending polling and its delayed transport error", async () => {
    const store = new ChatActivityStore();
    store.selectSession("session-a");
    let finish!: () => void;
    const old = store.refresh(async () => {
      await new Promise<void>((resolve) => { finish = resolve; });
      throw new Error("delayed error");
    });
    store.accept(snapshot);
    finish();
    await old;
    expect(store.current()).toMatchObject({ status: "ready", snapshot });
  });

  it("rejects responses from other sessions and replaces uncertainty with recovered evidence", async () => {
    const store = new ChatActivityStore();
    store.selectSession("session-a");
    store.accept(snapshot);
    await store.refresh(async () => ({ ...snapshot, session_id: "session-b" }));
    expect(store.current().status).toBe("unavailable");
    expect(activityStatusDisplay(run.status, false)).toMatchObject({ label: "Desconhecido", working: false });
    await store.refresh(async () => snapshot);
    expect(store.current()).toMatchObject({ status: "ready", snapshot });
  });

  it("survives navigation and allows detached subscribers without losing a task", () => {
    const store = new ChatActivityStore();
    store.selectSession("session-a");
    let count = 0;
    const off = store.subscribe(() => { count += 1; });
    off();
    store.accept(snapshot);
    store.selectSession("session-a");
    expect(count).toBe(1);
    let resumed;
    store.subscribe((state) => { resumed = state; });
    expect(resumed).toMatchObject({ snapshot, status: "ready" });
  });

  it("polls active, approval, paused and unknown tasks quickly and completed tasks at idle cadence", () => {
    const store = new ChatActivityStore();
    store.selectSession("session-a");
    for (const status of ["running", "waiting_approval", "paused", "unknown"] as const) {
      store.accept({ ...snapshot, runs: [{ ...run, status }] });
      expect(activityPollDelay(store.current())).toBe(2000);
    }
    store.accept({ ...snapshot, runs: [{ ...run, status: "completed" }] });
    expect(activityPollDelay(store.current())).toBe(10000);
    expect(activityPollDelay(store.current(), true)).toBe(2000);
    store.unavailable();
    expect(activityPollDelay(store.current())).toBe(2000);
  });
});

describe("agent activity rendering", () => {
  it("renders real names, tasks, tool and approval states with dedicated delegated members", () => {
    const markup = renderToStaticMarkup(createElement(ChatActivityPanel, { state: { sessionId: "session-a", snapshot, status: "ready" } }));
    for (const text of ["Atividade dos agentes", "Bauer", "Investigar arquivos", "Revisor", "Revisar os testes", "read_file", "Aguardando aprovação", "Agentes delegados"]) {
      expect(markup).toContain(text);
    }
    expect(markup).not.toContain("%");
    expect(markup.match(/Investigar arquivos do projeto/g)).toHaveLength(1);
  });

  it("preserves a principal agent's distinct task description", () => {
    const changed = { ...snapshot, runs: [{ ...run, agents: [{ ...run.agents[0], task: "Coordenar revisores" }] }] };
    const markup = renderToStaticMarkup(createElement(ChatActivityPanel, { state: { sessionId: "session-a", snapshot: changed, status: "ready" } }));
    expect(markup).toContain("Investigar arquivos do projeto");
    expect(markup).toContain("Coordenar revisores");
  });

  it("on connection failure keeps task identity while removing unconfirmed working and tool indicators", () => {
    const markup = renderToStaticMarkup(createElement(ChatActivityPanel, { state: { sessionId: "session-a", snapshot, status: "unavailable" } }));
    expect(markup).toContain("Bauer");
    expect(markup).toContain("Desconhecido");
    expect(markup).toContain("Atualização indisponível");
    expect(markup).not.toContain("Trabalhando");
    expect(markup).not.toContain("read_file");
    expect(markup).not.toContain("spin");
  });
});
