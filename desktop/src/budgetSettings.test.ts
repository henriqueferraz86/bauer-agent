import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api/client";
import {
  BudgetSettingsController, BudgetSettingsState, BudgetSettingsStatus, budgetSaveFeedback,
  fetchBudgetSettings, formatBudgetUSD, parseBudgetSettings, parseDailyBudgetInput, saveDailyBudget,
} from "./budgetSettings";
import { SettingsBudgetView } from "./components/SettingsBudgetCard";

const exceeded: BudgetSettingsStatus = { daily: { limit_usd: 2, used_usd: 9.6945, remaining_usd: 0, exceeded: true } };
const available: BudgetSettingsStatus = { daily: { limit_usd: 20, used_usd: 9.6945, remaining_usd: 10.3055, exceeded: false } };
const viewState: BudgetSettingsState = { budget: exceeded, draft: "2", dirty: false, phase: "ready", error: null, feedback: null };

afterEach(() => vi.restoreAllMocks());

describe("daily budget validation and API", () => {
  it("accepts a finite nonnegative user limit including zero without rounding it", () => {
    for (const [draft, value] of [["0", 0], ["20.05", 20.05], [".5", 0.5], [" 9.6945 ", 9.6945], ["1e2", 100]] as const) {
      expect(parseDailyBudgetInput(draft)).toEqual({ value, error: null });
    }
  });

  it("rejects empty, negative, malformed and nonfinite drafts", () => {
    for (const draft of ["", "  ", "-1", "NaN", "Infinity", "1e999", "false", "0x10", "1,50", "1.2.3"]) {
      expect(parseDailyBudgetInput(draft)).toMatchObject({ value: null, error: expect.any(String) });
    }
  });

  it("uses the server's real daily figures and ignores unrelated policy fields", () => {
    expect(parseBudgetSettings({ ...exceeded, profile: { mode: "supervised" }, weekly: { limit_usd: 100 } })).toEqual(exceeded);
    expect(formatBudgetUSD(exceeded.daily.used_usd)).toContain("9,6945");
    expect(formatBudgetUSD(available.daily.remaining_usd)).toContain("10,3055");
  });

  it("rejects missing or corrupt status values instead of inventing defaults", () => {
    for (const value of [null, {}, { daily: {} }, { daily: { ...exceeded.daily, limit_usd: "2" } },
      { daily: { ...exceeded.daily, used_usd: NaN } }, { daily: { ...exceeded.daily, remaining_usd: Infinity } },
      { daily: { ...exceeded.daily, used_usd: -1 } }, { daily: { ...exceeded.daily, exceeded: "true" } }]) {
      expect(parseBudgetSettings(value)).toBeNull();
    }
  });

  it("reads the policy endpoint and saves only the numeric daily limit", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(exceeded);
    const put = vi.spyOn(api, "put").mockResolvedValue(available);
    expect(await fetchBudgetSettings()).toEqual(exceeded);
    expect(get).toHaveBeenCalledWith("/api/obs/budget");
    expect(await saveDailyBudget(20)).toEqual(available);
    expect(put).toHaveBeenCalledWith("/api/obs/budget", { daily_budget_usd: 20 });
    for (const limit of [-1, NaN, Infinity]) await expect(saveDailyBudget(limit)).rejects.toThrow("inválido");
    expect(put).toHaveBeenCalledTimes(1);
  });

  it("rejects invalid GET and PUT confirmations", async () => {
    vi.spyOn(api, "get").mockResolvedValue({});
    vi.spyOn(api, "put").mockResolvedValue({});
    await expect(fetchBudgetSettings()).rejects.toThrow("orçamento inválido");
    await expect(saveDailyBudget(20)).rejects.toThrow("confirmar");
  });
});

describe("budget editor state", () => {
  it("loads the existing limit without choosing a new budget", async () => {
    const controller = new BudgetSettingsController();
    expect(controller.current()).toMatchObject({ draft: "", budget: null, phase: "loading" });
    await controller.load(async () => exceeded);
    expect(controller.current()).toMatchObject({ draft: "2", budget: exceeded, phase: "ready", dirty: false });
  });

  it("preserves an invalid draft without writing a policy change", async () => {
    const controller = new BudgetSettingsController();
    await controller.load(async () => exceeded);
    controller.edit("-3");
    const write = vi.fn(async () => available);
    expect(await controller.save(write)).toBe(false);
    expect(write).not.toHaveBeenCalled();
    expect(controller.current()).toMatchObject({ draft: "-3", budget: exceeded, error: expect.any(String) });
  });

  it("preserves a failed save's draft and reconciles policy without overwriting edits", async () => {
    const controller = new BudgetSettingsController();
    await controller.load(async () => exceeded);
    controller.edit("20");
    expect(await controller.save(async () => { throw new Error("controlled offline"); })).toBe(false);
    expect(controller.current()).toMatchObject({ draft: "20", budget: exceeded, phase: "save_error", error: "controlled offline" });
    await controller.load(async () => available);
    expect(controller.current()).toMatchObject({ draft: "20", budget: available, phase: "ready", dirty: true });
  });

  it("confirms saved status and consumption before clearing the dirty draft", async () => {
    const controller = new BudgetSettingsController();
    await controller.load(async () => exceeded);
    controller.edit("20");
    let finish!: (status: BudgetSettingsStatus) => void;
    const saving = controller.save(async () => new Promise((resolve) => { finish = resolve; }));
    expect(controller.current()).toMatchObject({ phase: "saving", budget: exceeded, draft: "20" });
    controller.edit("30");
    expect(controller.current().draft).toBe("20");
    finish(available);
    expect(await saving).toBe(true);
    expect(controller.current()).toMatchObject({ phase: "ready", draft: "20", budget: available, dirty: false });
    expect(controller.current().feedback).toContain("sem reiniciar");
  });

  it("a lower limit reports exhausted balance and zero explains the execution block", async () => {
    const controller = new BudgetSettingsController();
    await controller.load(async () => available);
    controller.edit("5");
    await controller.save(async () => ({ daily: { ...exceeded.daily, limit_usd: 5 } }));
    expect(controller.current().feedback).toContain("saldo diário está esgotado");
    controller.edit("0");
    await controller.save(async () => ({ daily: { ...exceeded.daily, limit_usd: 0 } }));
    expect(controller.current().feedback).toContain("Limite zero");
    expect(controller.current().budget?.daily.used_usd).toBe(9.6945);
    expect(budgetSaveFeedback(available)).not.toContain("bloqueadas");
  });

  it("load errors expose retry and invalidated late results cannot revive detached UI", async () => {
    const controller = new BudgetSettingsController();
    await controller.load(async () => { throw new Error("not available"); });
    expect(controller.current()).toMatchObject({ phase: "load_error", budget: null, draft: "" });
    let finish!: (status: BudgetSettingsStatus) => void;
    const old = controller.load(async () => new Promise((resolve) => { finish = resolve; }));
    await controller.load(async () => available);
    finish(exceeded);
    await old;
    expect(controller.current().budget).toEqual(available);
    const late = controller.load(async () => exceeded);
    controller.invalidate();
    await late;
    expect(controller.current().budget).toEqual(available);
  });
});

describe("budget settings rendering", () => {
  const render = (state: BudgetSettingsState) => renderToStaticMarkup(createElement(SettingsBudgetView, {
    state, onEdit: () => {}, onSave: () => {}, onRetry: () => {},
  }));

  it("labels the real rolling window, dollar limit, usage and current block", () => {
    const markup = render(viewState);
    for (const text of ["Orçamento de execução", "Limite diário (US$)", "últimas 24 horas", "9,6945", "Saldo diário", "Orçamento diário atingido", "Salvar limite diário"]) {
      expect(markup).toContain(text);
    }
    expect(markup).toContain('min="0"');
    expect(markup).toContain('step="0.01"');
    expect(markup).toContain("sem reiniciar");
  });

  it("shows loading and errors without fabricated policy figures", () => {
    const loading: BudgetSettingsState = { ...viewState, budget: null, draft: "", phase: "loading" };
    expect(render(loading)).toContain("Consultando orçamento");
    expect(render(loading)).not.toContain("Limite em vigor");
    const markup = render({ ...loading, phase: "load_error", error: "Consulta indisponível" });
    expect(markup).toContain("Tentar novamente");
    expect(markup).toContain('role="alert"');
    expect(markup).not.toContain("Saldo diário disponível");
  });

  it("save failure keeps the draft and identifies facts as the last confirmed status", () => {
    const markup = render({ ...viewState, draft: "20", phase: "save_error", error: "Salvamento indisponível" });
    expect(markup).toContain('value="20"');
    expect(markup).toContain("Último estado confirmado");
    expect(markup).toContain("Último limite confirmado");
    expect(markup).toContain("Consultar orçamento");
    expect(markup).not.toContain("aplicado imediatamente");
  });
});
