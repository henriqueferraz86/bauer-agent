// Retenção local da SPA ao encerrar a sessão (WEB-08, auditoria 2026-10-12).
//
// Política: o navegador NÃO guarda conteúdo da conversa além da sessão
// autenticada. Ao sair (logout explícito) ou quando o servidor declara a sessão
// expirada (401 depois de autenticado), a SPA apaga TUDO que gravou com o
// prefixo `bauer.` em localStorage e sessionStorage, zera o estado em memória
// do chat e avisa as outras abas da mesma origem para fazerem o mesmo.
// O histórico continua no servidor; o que some é a cópia local fora da proteção
// do cookie HttpOnly.

export const BAUER_STORAGE_PREFIX = "bauer.";
/** Chave-sinal: escrever nela dispara o evento `storage` nas outras abas. */
export const LOGOUT_SIGNAL_KEY = "bauer.logout.signal";

type Resetter = () => void;
const resetters = new Set<Resetter>();

/** Estado em memória (turno de chat, atividade) que também precisa sumir. */
export function onLocalSessionCleared(reset: Resetter): () => void {
  resetters.add(reset);
  return () => { resetters.delete(reset); };
}

function bauerKeys(storage: Storage): string[] {
  const keys: string[] = [];
  for (let i = 0; i < storage.length; i += 1) {
    const key = storage.key(i);
    if (key && key.startsWith(BAUER_STORAGE_PREFIX)) keys.push(key);
  }
  return keys;
}

function purge(storage: Storage | undefined): void {
  if (!storage) return;
  try {
    for (const key of bauerKeys(storage)) storage.removeItem(key);
  } catch { /* storage indisponível: nada persistido a apagar */ }
}

function storages(): { local?: Storage; session?: Storage } {
  const out: { local?: Storage; session?: Storage } = {};
  try { if (typeof localStorage !== "undefined") out.local = localStorage; } catch { /* bloqueado */ }
  try { if (typeof sessionStorage !== "undefined") out.session = sessionStorage; } catch { /* bloqueado */ }
  return out;
}

function runResetters(): void {
  resetters.forEach((reset) => {
    try { reset(); } catch { /* um assinante quebrado não impede a limpeza */ }
  });
}

/** Apaga storage + memória locais. `broadcast` avisa as outras abas. */
export function clearLocalSession({ broadcast = true }: { broadcast?: boolean } = {}): void {
  const { local, session } = storages();
  purge(local);
  purge(session);
  runResetters();
  if (broadcast && local) {
    try {
      // valor único: o evento `storage` só dispara quando o valor muda
      local.setItem(LOGOUT_SIGNAL_KEY, `${Date.now()}:${Math.random()}`);
      local.removeItem(LOGOUT_SIGNAL_KEY);
    } catch { /* sem storage não há outra aba lendo dele */ }
  }
}

/**
 * Outra aba saiu: limpa a memória desta aba (o storage é compartilhado e já
 * foi apagado) e chama `onRemoteLogout` para a UI voltar à tela de login.
 */
export function listenForRemoteLogout(onRemoteLogout: () => void, target: Window = window): () => void {
  const handler = (event: StorageEvent) => {
    if (event.key !== LOGOUT_SIGNAL_KEY || !event.newValue) return;
    clearLocalSession({ broadcast: false });
    onRemoteLogout();
  };
  target.addEventListener("storage", handler);
  return () => target.removeEventListener("storage", handler);
}

/**
 * Logout explícito: revoga no servidor e apaga a cópia local MESMO que a
 * revogação falhe (rede caída, servidor fora). Quem clicou em Sair quer que
 * este navegador pare de mostrar a conversa; a falha é repassada ao caller.
 */
export async function logoutAndClear(revoke: () => Promise<unknown>): Promise<void> {
  try {
    await revoke();
  } finally {
    clearLocalSession();
  }
}
