import { api } from "./api/client";

export const ACTIVITY_STATUSES = [
  "created", "planning", "policy_check", "queued", "running", "waiting_approval", "evaluating",
  "retrying", "paused", "completed", "failed", "cancelled", "delegated", "unknown",
] as const;
export type ActivityStatus = typeof ACTIVITY_STATUSES[number];

export interface AgentActivity {
  agent_id: string;
  name: string;
  task: string;
  status: ActivityStatus;
  activity: string;
  tool_name: string | null;
  updated_at: string;
}

export interface RunActivity {
  run_id: string;
  task: string;
  status: ActivityStatus;
  agent_id: string;
  agent_name: string;
  runtime: string;
  started_at: string;
  updated_at: string;
  agents: AgentActivity[];
}

export interface ChatActivitySnapshot { session_id: string | null; runs: RunActivity[] }
export interface ChatActivityState {
  sessionId: string;
  snapshot: ChatActivitySnapshot | null;
  status: "awaiting" | "loading" | "ready" | "unavailable";
}

const SAFE_ID = /^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$/;
const SAFE_TOOL = /^[a-zA-Z][a-zA-Z0-9_.:-]{0,63}$/;
const TERMINAL = new Set<ActivityStatus>(["completed", "failed", "cancelled"]);

export function redactActivityText(value: string, limit: number): string {
  return value
    .replace(/-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)/g, "[REDACTED]")
    .replace(/(\bBearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[REDACTED]")
    .replace(/(\b(?:PASSWORD|PASS|SECRET|TOKEN|API_KEY|KEY|DATABASE_URL|REDIS_URL|CONNECTION_STRING|AUTHORIZATION|COOKIE|PRIVATE_KEY)\b["']?\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,}]+)/gi, "$1[REDACTED]")
    .replace(/\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,})\b/g, "[REDACTED]")
    .replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, limit);
}

function text(value: unknown, limit: number): string | null {
  return typeof value === "string" ? redactActivityText(value, limit) : null;
}
function id(value: unknown): value is string { return typeof value === "string" && SAFE_ID.test(value); }
function agentId(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= 128 && !/[\u0000-\u001f\u007f]/.test(value);
}
function timestamp(value: unknown): value is string {
  return typeof value === "string" && value.length <= 64 && Number.isFinite(Date.parse(value));
}
function status(value: unknown): ActivityStatus {
  return ACTIVITY_STATUSES.includes(value as ActivityStatus) ? value as ActivityStatus : "unknown";
}

function parseAgent(value: unknown): AgentActivity | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const name = text(item.name, 80);
  const task = text(item.task, 800);
  const activity = text(item.activity, 240);
  if (!agentId(item.agent_id) || !name || task === null || activity === null || typeof item.status !== "string"
    || !timestamp(item.updated_at) || !(item.tool_name === null || typeof item.tool_name === "string")) return null;
  return {
    agent_id: item.agent_id, name, task, activity, status: status(item.status), updated_at: item.updated_at,
    tool_name: typeof item.tool_name === "string" && SAFE_TOOL.test(item.tool_name) ? item.tool_name : null,
  };
}

function parseRun(value: unknown): RunActivity | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const task = text(item.task, 800);
  const agentName = text(item.agent_name, 80);
  const runtime = text(item.runtime, 80);
  if (!id(item.run_id) || !agentId(item.agent_id) || task === null || !agentName || runtime === null
    || typeof item.status !== "string" || !timestamp(item.started_at) || !timestamp(item.updated_at)
    || !Array.isArray(item.agents)) return null;
  const agents = item.agents.slice(0, 40).map(parseAgent);
  if (agents.some((agent) => !agent)) return null;
  return {
    run_id: item.run_id, task, agent_id: item.agent_id, agent_name: agentName, runtime,
    status: status(item.status), started_at: item.started_at, updated_at: item.updated_at,
    agents: agents.filter((agent): agent is AgentActivity => agent !== null)
      .filter((agent, index, all) => all.findIndex((other) => other.agent_id === agent.agent_id) === index),
  };
}

export function parseChatActivity(value: unknown): ChatActivitySnapshot | null {
  try {
    if (typeof value === "string" && value.length > 128000) return null;
    const payload = typeof value === "string" ? JSON.parse(value) : value;
    if (!payload || typeof payload !== "object") return null;
    const item = payload as Record<string, unknown>;
    if (!(item.session_id === null || id(item.session_id)) || !Array.isArray(item.runs)) return null;
    const runs = item.runs.slice(0, 5).map(parseRun);
    if (runs.some((run) => !run)) return null;
    return {
      session_id: item.session_id as string | null,
      runs: runs.filter((run): run is RunActivity => run !== null)
        .filter((run, index, all) => all.findIndex((other) => other.run_id === run.run_id) === index)
        .sort((a, b) => Number(TERMINAL.has(a.status)) - Number(TERMINAL.has(b.status))
          || Date.parse(b.updated_at) - Date.parse(a.updated_at)),
    };
  } catch { return null; }
}

export async function fetchChatActivity(sessionId: string): Promise<ChatActivitySnapshot> {
  const query = new URLSearchParams({ session_id: sessionId });
  const parsed = parseChatActivity(await api.get<unknown>(`/chat/activity?${query}`));
  if (!parsed) throw new Error("Atividade dos agentes inválida");
  return parsed;
}

/** Repair the exact metadata prefix rendered by an older chat bundle. */
export function stripLeakedActivityPrefix(text: string, sessionId: string, task: string): string {
  if (!sessionId || typeof task !== "string" || !text.startsWith('{"session_id":') || text.length > 256000) return text;
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') quoted = false;
    } else if (char === '"') quoted = true;
    else if (char === "{") depth += 1;
    else if (char === "}" && --depth === 0) {
      const snapshot = parseChatActivity(text.slice(0, i + 1));
      const publicTask = redactActivityText(task.replace(/\s+/g, " ").trim(), 320);
      return snapshot?.session_id === sessionId && snapshot.runs.some((run) => run.task === publicTask)
        ? text.slice(i + 1).replace(/^\s*\n/, "") : text;
    }
  }
  return text;
}

export function activityPollDelay(state: ChatActivityState, busy = false): number {
  return busy || state.status === "unavailable" || state.status === "loading"
    || state.snapshot?.runs.some((run) => !TERMINAL.has(run.status)) ? 2000 : 10000;
}

/** Shared across mounted chat screens; only server snapshots establish runtime status. */
export class ChatActivityStore {
  private revision = 0;
  private state: ChatActivityState = { sessionId: "", snapshot: null, status: "awaiting" };
  private listeners = new Set<(state: ChatActivityState) => void>();

  current(): ChatActivityState { return this.state; }
  subscribe(listener: (state: ChatActivityState) => void): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => { this.listeners.delete(listener); };
  }
  private publish(state: ChatActivityState): void {
    this.state = state;
    this.listeners.forEach((listener) => { try { listener(state); } catch { /* detached view */ } });
  }
  invalidate(): void { this.revision += 1; }
  selectSession(sessionId: string, reset = false): void {
    this.invalidate();
    if (!reset && this.state.sessionId === sessionId) return;
    this.publish({ sessionId, snapshot: null, status: sessionId ? "loading" : "awaiting" });
  }
  accept(snapshot: ChatActivitySnapshot): boolean {
    if (snapshot.session_id !== (this.state.sessionId || null)) return false;
    this.invalidate();
    this.publish({ ...this.state, snapshot, status: "ready" });
    return true;
  }
  unavailable(): void {
    this.invalidate();
    this.publish({ ...this.state, status: this.state.sessionId ? "unavailable" : "awaiting" });
  }
  async refresh(fetchActivity: typeof fetchChatActivity = fetchChatActivity): Promise<void> {
    if (!this.state.sessionId) return;
    const startedAt = ++this.revision;
    const sessionId = this.state.sessionId;
    try {
      const snapshot = await fetchActivity(sessionId);
      if (startedAt !== this.revision) return;
      if (!this.accept(snapshot)) this.unavailable();
    } catch {
      if (startedAt === this.revision) this.unavailable();
    }
  }
}

export const chatActivityStore = new ChatActivityStore();

export function activityStatusDisplay(value: ActivityStatus, confirmed = true): { label: string; tone: string; working: boolean } {
  const labels: Record<ActivityStatus, string> = {
    created: "Criada", planning: "Planejando", policy_check: "Verificando políticas", queued: "Na fila",
    running: "Trabalhando", waiting_approval: "Aguardando aprovação", evaluating: "Verificando resultado",
    retrying: "Tentando novamente", paused: "Pausada", completed: "Concluída", failed: "Falhou",
    cancelled: "Cancelada", delegated: "Delegada", unknown: "Desconhecido",
  };
  const effective = confirmed ? value : "unknown";
  return {
    label: labels[effective],
    tone: effective === "completed" ? "green" : effective === "failed" ? "red" : "accent",
    working: ["planning", "running", "evaluating", "retrying"].includes(effective),
  };
}
