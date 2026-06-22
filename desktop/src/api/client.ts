// API client — wrapper fetch + SSE para o bauer serve. A base é relativa ("")
// porque a SPA é servida pelo próprio serve; em dev o Vite faz proxy p/ :8000.

const API_KEY_STORAGE = "bauer.apiKey";

export function getApiKey(): string {
  return localStorage.getItem(API_KEY_STORAGE) || "";
}
export function setApiKey(key: string): void {
  localStorage.setItem(API_KEY_STORAGE, key);
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json", ...extra };
  const key = getApiKey();
  if (key) h["X-API-Key"] = key;
  return h;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => fetch(path, { headers: headers() }).then((r) => handle<T>(r)),
  post: <T>(path: string, body?: unknown) =>
    fetch(path, { method: "POST", headers: headers(), body: body ? JSON.stringify(body) : undefined }).then(
      (r) => handle<T>(r)
    ),
  put: <T>(path: string, body: unknown) =>
    fetch(path, { method: "PUT", headers: headers(), body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  del: <T>(path: string) => fetch(path, { method: "DELETE", headers: headers() }).then((r) => handle<T>(r)),
};

export interface SSEEvent {
  event: string; // "message" (default), "tool", "done"
  data: string;
}

// SSE via fetch streaming (suporta header X-API-Key, ao contrário de EventSource).
// Cada bloco SSE pode ter linhas `event:` e `data:`; preservamos o tipo.
export async function streamSSE(
  path: string,
  onEvent: (e: SSEEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(path, { headers: headers(), signal });
  if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      let ev = "message";
      const datas: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) ev = line.slice(6).trim();
        else if (line.startsWith("data:")) datas.push(line.slice(5).replace(/^ /, ""));
      }
      if (datas.length) onEvent({ event: ev, data: datas.join("\n") });
    }
  }
}
