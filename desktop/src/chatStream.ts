/** A chat turn belongs to the application and survives screen navigation. */
import { streamSSE, SSEEvent } from "./api/client";
import { parseToolActivityEvent, ToolActivity } from "./toolActivity";
import { ChatProjectContext, parseChatProjectContext } from "./chatProjectContext";

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  loop?: {
    runId: string;
    state: string;
    rounds: number;
    toolCalls: number;
    costUsd: number;
    activity?: string;
    stopReason?: string | null;
  };
  streaming?: boolean;
  toolActivities?: ToolActivity[];
  toolActivityCount?: number;
}

export interface ChatTurnState {
  messages: ChatMessage[];
  sessionId: string;
  busy: boolean;
  tokensPerSecond?: number;
  projectContext?: ChatProjectContext;
}

let active: ChatTurnState | null = null;
const listeners = new Set<(state: ChatTurnState) => void>();

export function currentChatTurn(): ChatTurnState | null {
  return active;
}

export function subscribeChatTurn(listener: (state: ChatTurnState) => void): () => void {
  listeners.add(listener);
  if (active) listener(active);
  return () => { listeners.delete(listener); };
}

export async function runChatTurn(
  state: { messages: ChatMessage[]; sessionId: string },
  text: string,
  persist: (state: ChatTurnState) => void,
  stream: typeof streamSSE = streamSSE,
): Promise<string> {
  if (active?.busy) throw new Error("A chat turn is already running");
  active = {
    ...state, busy: true,
    messages: [...state.messages, { role: "user", text }, { role: "assistant", text: "", streaming: true }],
  };
  let responseText = "";
  let chars = 0;
  let started = 0;
  const publish = () => {
    if (!active) return;
    // Storage and mounted views are auxiliary; neither can strand a turn.
    try { persist(active); } catch { /* storage unavailable/full */ }
    listeners.forEach((listener) => {
      try { listener(active!); } catch { /* detached/broken subscriber */ }
    });
  };
  const updateLast = (update: (message: ChatMessage) => void) => {
    if (!active) return;
    const messages = [...active.messages];
    const last = { ...messages[messages.length - 1] };
    update(last);
    messages[messages.length - 1] = last;
    active = { ...active, messages };
  };
  publish();
  const query = new URLSearchParams({ message: text });
  if (state.sessionId) query.set("session_id", state.sessionId);
  try {
    await stream(`/stream?${query}`, (event: SSEEvent) => {
      if (!active) return;
      if (event.event === "context") {
        const context = parseChatProjectContext(event.data);
        if (!context) return;
        active = { ...active, projectContext: context, sessionId: context.session_id ?? active.sessionId };
      } else if (event.event === "tool") {
        const activity = parseToolActivityEvent(event.data);
        if (!activity) return;
        updateLast((last) => {
          last.toolActivities = [...(last.toolActivities ?? []), activity].slice(-20);
          last.toolActivityCount = (last.toolActivityCount ?? 0) + 1;
        });
      } else if (["skill", "route", "tool_result", "debug", "internal"].includes(event.event)) {
        return;
      } else if (event.event === "done") {
        active = { ...active, sessionId: event.data };
        updateLast((last) => { last.streaming = false; });
      } else {
        updateLast((last) => { last.text += event.data; });
        responseText += event.data;
        const now = performance.now();
        if (!started) started = now;
        chars += event.data.length;
        const seconds = (now - started) / 1000;
        if (seconds > 0.4) active = { ...active!, tokensPerSecond: chars / 4 / seconds };
      }
      // Persistence is independent of React state and mounted subscribers.
      publish();
    });
  } catch {
    updateLast((last) => { last.text += "\nNão foi possível concluir a resposta. Tente novamente."; });
  } finally {
    updateLast((last) => { last.streaming = false; });
    active = { ...active!, busy: false };
    publish();
    active = null;
  }
  return responseText;
}
