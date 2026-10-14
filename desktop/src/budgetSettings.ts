import { api } from "./api/client";

export interface DailyBudgetStatus {
  limit_usd: number;
  used_usd: number;
  remaining_usd: number;
  exceeded: boolean;
}

export interface BudgetSettingsStatus { daily: DailyBudgetStatus }

export function parseBudgetSettings(value: unknown): BudgetSettingsStatus | null {
  if (!value || typeof value !== "object") return null;
  const daily = (value as Record<string, unknown>).daily;
  if (!daily || typeof daily !== "object") return null;
  const item = daily as Record<string, unknown>;
  if (![item.limit_usd, item.used_usd, item.remaining_usd].every((number) =>
    typeof number === "number" && Number.isFinite(number) && number >= 0)
    || typeof item.exceeded !== "boolean") return null;
  return { daily: {
    limit_usd: item.limit_usd as number,
    used_usd: item.used_usd as number,
    remaining_usd: item.remaining_usd as number,
    exceeded: item.exceeded,
  } };
}

export function parseDailyBudgetInput(draft: string): { value: number; error: null } | { value: null; error: string } {
  const input = draft.trim();
  const value = Number(input);
  const numeric = /^\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(input);
  if (!numeric || !Number.isFinite(value) || value < 0) {
    return { value: null, error: "Informe um limite em US$ com número finito maior ou igual a zero." };
  }
  return { value, error: null };
}

export async function fetchBudgetSettings(): Promise<BudgetSettingsStatus> {
  const parsed = parseBudgetSettings(await api.get<unknown>("/api/obs/budget"));
  if (!parsed) throw new Error("O servidor retornou um orçamento inválido. Tente consultar novamente.");
  return parsed;
}

export async function saveDailyBudget(value: number): Promise<BudgetSettingsStatus> {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw new Error("Limite diário inválido.");
  const parsed = parseBudgetSettings(await api.put<unknown>("/api/obs/budget", { daily_budget_usd: value }));
  if (!parsed) throw new Error("Não foi possível confirmar o orçamento salvo. Tente consultar novamente.");
  return parsed;
}

export function formatBudgetUSD(value: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 6,
  }).format(value);
}

export function budgetSaveFeedback(budget: BudgetSettingsStatus): string {
  if (budget.daily.limit_usd === 0) return "Limite zero salvo e aplicado imediatamente. Novas execuções ficam bloqueadas.";
  if (budget.daily.exceeded) return "Limite salvo e aplicado imediatamente. O consumo atingiu ou ultrapassou o limite diário; o saldo diário está esgotado.";
  return "Limite diário salvo e aplicado imediatamente, sem reiniciar.";
}

export interface BudgetSettingsState {
  budget: BudgetSettingsStatus | null;
  draft: string;
  dirty: boolean;
  phase: "loading" | "ready" | "saving" | "load_error" | "save_error";
  error: string | null;
  feedback: string | null;
}

/** Keeps edits intact on a failed save/reload; only confirmed API data updates policy facts. */
export class BudgetSettingsController {
  private revision = 0;
  private state: BudgetSettingsState = { budget: null, draft: "", dirty: false, phase: "loading", error: null, feedback: null };
  private listeners = new Set<(state: BudgetSettingsState) => void>();

  current(): BudgetSettingsState { return this.state; }
  subscribe(listener: (state: BudgetSettingsState) => void): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => { this.listeners.delete(listener); };
  }
  private publish(state: BudgetSettingsState): void {
    this.state = state;
    this.listeners.forEach((listener) => listener(state));
  }
  invalidate(): void { this.revision += 1; }
  edit(draft: string): void {
    if (this.state.phase === "saving") return;
    this.publish({ ...this.state, draft, dirty: true, error: null, feedback: null,
      phase: this.state.phase === "save_error" ? "ready" : this.state.phase });
  }
  async load(fetchBudget: typeof fetchBudgetSettings = fetchBudgetSettings): Promise<void> {
    const startedAt = ++this.revision;
    this.publish({ ...this.state, phase: "loading", error: null, feedback: null });
    try {
      const budget = await fetchBudget();
      if (startedAt !== this.revision) return;
      this.publish({ ...this.state, budget, draft: this.state.dirty ? this.state.draft : String(budget.daily.limit_usd), phase: "ready" });
    } catch (error) {
      if (startedAt !== this.revision) return;
      this.publish({ ...this.state, phase: "load_error", error: error instanceof Error ? error.message : "Não foi possível consultar o orçamento." });
    }
  }
  async save(writeBudget: typeof saveDailyBudget = saveDailyBudget): Promise<boolean> {
    if (!this.state.budget || this.state.phase === "loading" || this.state.phase === "saving") return false;
    const parsed = parseDailyBudgetInput(this.state.draft);
    if (parsed.value === null) { this.publish({ ...this.state, error: parsed.error, feedback: null }); return false; }
    const startedAt = ++this.revision;
    this.publish({ ...this.state, phase: "saving", error: null, feedback: null });
    try {
      const budget = await writeBudget(parsed.value);
      if (startedAt !== this.revision) return false;
      this.publish({ ...this.state, budget, draft: String(budget.daily.limit_usd), dirty: false, phase: "ready", feedback: budgetSaveFeedback(budget) });
      return true;
    } catch (error) {
      if (startedAt !== this.revision) return false;
      this.publish({ ...this.state, phase: "save_error", error: error instanceof Error ? error.message : "Não foi possível salvar o limite diário." });
      return false;
    }
  }
}
