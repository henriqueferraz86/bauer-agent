export type ApprovalStatus = "pending" | "approved" | "denied" | "expired" | "cancelled";
export type ApprovalScope = "once" | "run" | "always";
export interface ApprovalCard {
  id: string;
  operation?: string;
  tool_name?: string;
  status?: ApprovalStatus;
  summary?: string;
  reason?: string;
  risk_level?: string;
  run_id?: string;
  session_id?: string;
  scope?: ApprovalScope;
  submitting?: boolean;
  error?: string;
}
export type ApprovalsById = Record<string, ApprovalCard>;

function publicText(value: string): string {
  return value.replace(/[\u0000-\u001f\u007f]/g, " ").trim()
    .replace(/(\b(?:password|pass|secret|token|api_key|key|database_url|redis_url|connection_string|authorization|cookie|private_key)\b["']?\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,}]+)/gi, "$1[REDACTED]")
    .replace(/(\bBearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[REDACTED]")
    .replace(/\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,})\b/g, "[REDACTED]")
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, "$1[REDACTED]@");
}

/** Only public metadata enters state/storage, never payloads or raw REST errors. */
export function sanitizeApproval(value: unknown): ApprovalCard | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  if (typeof source.id !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$/.test(source.id)) return null;
  const result: ApprovalCard = { id: source.id };
  for (const field of ["operation", "tool_name", "summary", "reason", "risk_level", "run_id", "session_id"] as const) {
    if (typeof source[field] === "string" && source[field].trim()) {
      result[field] = publicText(source[field]).slice(0, field === "reason" ? 1000 : 128);
    }
  }
  if (["pending", "approved", "denied", "expired", "cancelled"].includes(String(source.status))) {
    result.status = source.status as ApprovalStatus;
  }
  if (source.scope === "once" || source.scope === "run" || source.scope === "always") result.scope = source.scope;
  return result;
}

export function mergeApproval(current: ApprovalsById | undefined, value: unknown): ApprovalsById {
  const patch = sanitizeApproval(value);
  if (!patch) return current ?? {};
  const previous = current && Object.prototype.hasOwnProperty.call(current, patch.id) ? current[patch.id] : undefined;
  // Replayed pending events must not undo a confirmed decision.
  if (previous?.status && previous.status !== "pending") {
    if (patch.status === "pending") delete patch.scope;
    delete patch.status;
  }
  const card = { ...previous, ...patch };
  if (card.status && card.status !== "pending") {
    card.submitting = false;
    card.error = undefined;
  }
  return { ...current, [patch.id]: card };
}

export function updateApprovalRequest(current: ApprovalsById | undefined, id: string, patch: Partial<ApprovalCard>): ApprovalsById | undefined {
  if (!current || !Object.prototype.hasOwnProperty.call(current, id)) return current;
  const result = mergeApproval(current, { ...patch, id });
  const card = result[id];
  // A REST failure may arrive after SSE confirmed a decision. Never revert it.
  if (!card.status || card.status === "pending") {
    result[id] = { ...card, submitting: patch.submitting, error: patch.error };
  }
  return result;
}

export function parseApprovalEvent(data: string): ApprovalCard | null {
  try { return sanitizeApproval(JSON.parse(data)); } catch { return null; }
}

/** Also migrates the old single-card format; transient request state is not restored. */
export function restoreApprovals(value: unknown, legacy?: unknown): ApprovalsById {
  let result = mergeApproval(undefined, legacy);
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const card of Object.values(value)) result = mergeApproval(result, card);
  }
  return result;
}

export function canApproveRun(card: ApprovalCard): boolean {
  return card.operation === "shell.execute" && card.tool_name === "run_command"
    && Boolean(card.run_id?.trim() && card.session_id?.trim());
}

export function approvalStatusText(card: ApprovalCard, streaming: boolean, turnEnded = false): string {
  if (card.submitting) return "Registrando decisão…";
  switch (card.status) {
    case "approved": {
      const label = card.scope === "always" ? "Autorização permanente registrada" : card.scope === "run" ? "Comandos desta tarefa aprovados" : "Aprovação confirmada";
      return `${label}${turnEnded ? " · turno encerrado." : streaming ? " · aguardando conclusão." : ". Consulte a execução na aba Runs."}`;
    }
    case "denied": return "Aprovação negada.";
    case "expired": return "Aprovação expirada.";
    case "cancelled": return "Aprovação cancelada.";
    case "pending": return "Aguardando sua decisão.";
    default: return "Estado da aprovação não confirmado. Consulte a aba Approvals.";
  }
}

export async function submitApprovalDecision(
  card: ApprovalCard,
  action: "approve" | "deny",
  scope: ApprovalScope,
  inFlight: Set<string>,
  post: (path: string, body?: unknown) => Promise<unknown>,
  update: (id: string, patch: Partial<ApprovalCard>) => void,
): Promise<void> {
  if (card.status !== "pending" || card.submitting || inFlight.has(card.id)) return;
  if (scope !== "once" && (action !== "approve" || !canApproveRun(card))) return;
  inFlight.add(card.id);
  update(card.id, { submitting: true, error: undefined });
  try {
    await post(`/api/approvals/${encodeURIComponent(card.id)}/${action}`, scope !== "once" ? { scope } : undefined);
    update(card.id, { status: action === "approve" ? "approved" : "denied", scope, submitting: false, error: undefined });
  } catch {
    update(card.id, { submitting: false, error: "Não foi possível confirmar a decisão. Tente novamente neste cartão ou consulte a aba Approvals." });
  } finally {
    inFlight.delete(card.id);
  }
}
