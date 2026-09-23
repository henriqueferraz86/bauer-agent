export interface OpenAIAuthStatus {
  experimental: boolean;
  connected: boolean;
  status: "disconnected" | "pending" | "completing" | "connected" | "error";
  auth_type?: "" | "api_key" | "chatgpt_oauth";
  expired?: boolean;
  has_refresh?: boolean;
  expires_at?: number | null;
  error?: string;
}

export interface OpenAIAuthStart {
  authorization_url: string;
  expires_at: number;
}

interface AuthPopup {
  location: { href: string };
  close?: () => void;
}

export function validateOpenAIAuthorizationUrl(raw: string): string {
  const url = new URL(raw);
  if (url.protocol !== "https:" || url.hostname !== "auth.openai.com") {
    throw new Error("URL de autenticação OpenAI inválida.");
  }
  return url.toString();
}

export async function waitForOpenAIAuth(
  readStatus: () => Promise<OpenAIAuthStatus>,
  options: { attempts?: number; delay?: () => Promise<void> } = {},
): Promise<OpenAIAuthStatus> {
  const attempts = options.attempts ?? 300;
  const delay = options.delay ?? (() => new Promise((resolve) => setTimeout(resolve, 1000)));
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const status = await readStatus();
    if (status.connected || status.status === "error") return status;
    await delay();
  }
  throw new Error("O login OpenAI expirou. Tente novamente.");
}

export async function startOpenAIBrowserAuth(
  start: () => Promise<OpenAIAuthStart>,
  readStatus: () => Promise<OpenAIAuthStatus>,
  openPopup: () => AuthPopup | null,
  wait = waitForOpenAIAuth,
): Promise<OpenAIAuthStatus> {
  // Abre de forma síncrona dentro do clique para não cair no popup blocker.
  const popup = openPopup();
  if (!popup) throw new Error("O navegador bloqueou o popup da OpenAI.");
  try {
    const started = await start();
    popup.location.href = validateOpenAIAuthorizationUrl(started.authorization_url);
    return await wait(readStatus);
  } catch (error) {
    popup.close?.();
    throw error;
  }
}
