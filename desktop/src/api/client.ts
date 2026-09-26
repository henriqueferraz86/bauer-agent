// API client — wrapper fetch + SSE para o bauer serve. A base é relativa ("")
// porque a SPA é servida pelo próprio serve; em dev o Vite faz proxy p/ :8000.

const LEGACY_API_KEY_STORAGE = "bauer.apiKey";
export const AUTH_REQUIRED_EVENT = "bauer:auth-required";

export function clearLegacyApiKey(): void {
  if (typeof localStorage !== "undefined") localStorage.removeItem(LEGACY_API_KEY_STORAGE);
}

export function cookieValue(cookieHeader: string, name: string): string {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = cookieHeader.split(";").map((part) => part.trim()).find((part) => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

/** Silêncio/áudio sem fala não deve aparecer como erro no chat de voz. */
export function isNoSpeechError(error: unknown): boolean {
  const message = (error instanceof Error ? error.message : String(error)).toLowerCase();
  return message.includes("transcrição vazia") || message.includes("transcricao vazia");
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json", ...extra };
  const csrf = typeof document !== "undefined" ? cookieValue(document.cookie, "bauer_csrf") : "";
  if (csrf) h["X-CSRF-Token"] = csrf;
  return h;
}

function request(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(path, { credentials: "same-origin", ...init }).then((response) => {
    if (response.status === 401 && !path.startsWith("/auth/")) {
      window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
    }
    return response;
  });
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

async function handleAudio(res: Response): Promise<Blob> {
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
  return res.blob();
}

export const api = {
  get: <T>(path: string) => request(path, { headers: headers() }).then((r) => handle<T>(r)),
  post: <T>(path: string, body?: unknown) =>
    request(path, { method: "POST", headers: headers(), body: body ? JSON.stringify(body) : undefined }).then(
      (r) => handle<T>(r)
    ),
  put: <T>(path: string, body: unknown) =>
    request(path, { method: "PUT", headers: headers(), body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  del: <T>(path: string) => request(path, { method: "DELETE", headers: headers() }).then((r) => handle<T>(r)),
  // multipart — sem Content-Type manual: o browser define o boundary sozinho.
  upload: <T>(path: string, blob: Blob, filename: string) => {
    const form = new FormData();
    form.append("file", blob, filename);
    const h = headers();
    delete h["Content-Type"];
    return request(path, { method: "POST", headers: h, body: form }).then((r) => handle<T>(r));
  },
  audio: (path: string, body: unknown) =>
    request(path, { method: "POST", headers: headers(), body: JSON.stringify(body) }).then(handleAudio),
};

export interface SSEEvent {
  event: string; // "message" (default), "tool", "done"
  data: string;
}

// SSE via fetch streaming para enviar o CSRF junto com a sessão HttpOnly.
// Cada bloco SSE pode ter linhas `event:` e `data:`; preservamos o tipo.
export async function streamSSE(
  path: string,
  onEvent: (e: SSEEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await request(path, { headers: headers(), signal });
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
