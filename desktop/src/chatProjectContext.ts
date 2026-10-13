import { api } from "./api/client";

export interface ChatProject {
  id: string;
  name: string;
  path: string;
}

export interface ChatProjectContext {
  session_id: string | null;
  project: ChatProject | null;
  workspace: string;
  source: "session" | "active" | "explicit" | "default";
  active_project: ChatProject | null;
  warning: string | null;
}

function isProject(value: unknown): value is ChatProject {
  if (!value || typeof value !== "object") return false;
  const project = value as Record<string, unknown>;
  return [project.id, project.name, project.path].every((item) => typeof item === "string" && item.length > 0);
}

export function parseChatProjectContext(value: unknown): ChatProjectContext | null {
  try {
    const payload = typeof value === "string" ? JSON.parse(value) : value;
    if (!payload || typeof payload !== "object") return null;
    const context = payload as Record<string, unknown>;
    if (!(context.session_id === null || typeof context.session_id === "string")
      || !(context.project === null || isProject(context.project))
      || typeof context.workspace !== "string" || !context.workspace
      || !["session", "active", "explicit", "default"].includes(String(context.source))
      || !(context.active_project === null || isProject(context.active_project))
      || !(context.warning === null || typeof context.warning === "string")) return null;
    return {
      session_id: context.session_id as string | null,
      project: context.project as ChatProject | null,
      workspace: context.workspace,
      source: context.source as ChatProjectContext["source"],
      active_project: context.active_project as ChatProject | null,
      warning: context.warning as string | null,
    };
  } catch { return null; }
}

export async function fetchChatProjectContext(sessionId: string): Promise<ChatProjectContext> {
  const query = new URLSearchParams();
  if (sessionId) query.set("session_id", sessionId);
  const payload = await api.get<unknown>(`/chat/context${query.size ? `?${query}` : ""}`);
  const context = parseChatProjectContext(payload);
  if (!context) throw new Error("Contexto do chat inválido");
  return context;
}

/** A session change or authoritative stream event supersedes pending HTTP reads. */
export class ChatContextLoader {
  private revision = 0;
  private sessionId = "";

  reset(sessionId: string): void {
    this.sessionId = sessionId;
    this.invalidate();
  }

  invalidate(): void { this.revision += 1; }

  async load(
    fetchContext: typeof fetchChatProjectContext = fetchChatProjectContext,
  ): Promise<{ context: ChatProjectContext | null; unavailable: boolean } | null> {
    const startedAt = ++this.revision;
    try {
      const context = await fetchContext(this.sessionId);
      if (startedAt !== this.revision) return null;
      // An endpoint must never substitute another session's metadata.
      if (context.session_id !== (this.sessionId || null)) return { context: null, unavailable: true };
      return { context, unavailable: false };
    } catch {
      return startedAt === this.revision ? { context: null, unavailable: true } : null;
    }
  }
}

export function chatContextDescription(context: ChatProjectContext): { label: string; warning: string | null } {
  const labels: Record<ChatProjectContext["source"], string> = {
    session: "Vinculado à sessão", active: "Projeto ativo", explicit: "Projeto escolhido", default: "Workspace padrão",
  };
  const active = context.active_project;
  const different = context.project && active && (active.id !== context.project.id || active.path !== context.workspace);
  const nextSession = different
    ? `O projeto ativo é ${active.name}. Nova sessão usa ${active.name} (${active.path}).`
    : null;
  return { label: context.project ? labels[context.source] : labels.default, warning: [context.warning, nextSession].filter(Boolean).join(" ") || null };
}
