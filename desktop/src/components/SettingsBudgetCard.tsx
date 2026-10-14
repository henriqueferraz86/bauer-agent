import { useEffect, useRef, useState } from "react";
import { BudgetSettingsController, BudgetSettingsState, formatBudgetUSD } from "../budgetSettings";

export function SettingsBudgetView({ state, onEdit, onSave, onRetry }: {
  state: BudgetSettingsState;
  onEdit: (value: string) => void;
  onSave: () => void;
  onRetry: () => void;
}) {
  const loading = state.phase === "loading";
  const saving = state.phase === "saving";
  const confirmedNow = state.phase === "ready";
  const daily = state.budget?.daily;
  return (
    <section className="card settings-budget-card" aria-label="Orçamento de execução" aria-busy={loading || saving}>
      <div className="settings-budget-heading"><i className="ti ti-wallet" aria-hidden="true" /><strong>Orçamento de execução</strong></div>
      <p className="settings-budget-description">O limite diário considera o consumo das últimas 24 horas. Alterações são aplicadas imediatamente, sem reiniciar.</p>
      {loading && <div className="settings-budget-notice" role="status"><i className="ti ti-loader-2 spin" aria-hidden="true" /> Consultando orçamento…</div>}
      {daily && (
        <>
          {!confirmedNow && <div className="settings-budget-notice">Último estado confirmado</div>}
          <div className="settings-budget-stats">
            <div><span>Consumo nas últimas 24 horas</span><strong>{formatBudgetUSD(daily.used_usd)}</strong></div>
            <div><span>Saldo diário</span><strong>{formatBudgetUSD(daily.remaining_usd)}</strong></div>
          </div>
          <div className={`tag ${daily.exceeded ? "red" : "green"}`}>
            {daily.exceeded ? "Orçamento diário atingido" : "Saldo diário disponível"}
          </div>
          {daily.exceeded && <p className="settings-budget-blocked">{confirmedNow ? "O consumo atingiu ou ultrapassou o limite diário." : "Saldo diário esgotado na última consulta."}</p>}
        </>
      )}
      <form className="settings-budget-form" noValidate onSubmit={(event) => { event.preventDefault(); onSave(); }}>
        <label htmlFor="settings-daily-budget">Limite diário (US$)
          <input id="settings-daily-budget" className="in mono" type="number" min="0" step="0.01"
            value={state.draft} onChange={(event) => onEdit(event.target.value)} disabled={loading || saving || !daily}
            aria-invalid={Boolean(state.error) && state.phase !== "load_error" && state.phase !== "save_error"}
            aria-describedby="settings-budget-help" />
        </label>
        <button className="btn primary" type="submit" disabled={loading || saving || !daily}>
          {saving && <i className="ti ti-loader-2 spin" aria-hidden="true" />}{saving ? "Salvando…" : "Salvar limite diário"}
        </button>
      </form>
      <p id="settings-budget-help" className="settings-budget-help">Limite zero bloqueia novas execuções.{daily && <> {confirmedNow ? "Limite em vigor" : "Último limite confirmado"}: {formatBudgetUSD(daily.limit_usd)}.</>}</p>
      {state.error && (
        <div className="settings-budget-error" role="alert">
          <span>{state.error}</span>
          {state.phase === "load_error" && <button className="btn" type="button" onClick={onRetry}>Tentar novamente</button>}
          {state.phase === "save_error" && <button className="btn" type="button" onClick={onRetry}>Consultar orçamento</button>}
        </div>
      )}
      {state.feedback && <div className="settings-budget-feedback" role="status">{state.feedback}</div>}
    </section>
  );
}

export default function SettingsBudgetCard() {
  const controller = useRef(new BudgetSettingsController());
  const [state, setState] = useState(controller.current.current());
  useEffect(() => {
    const budgetController = controller.current;
    const off = budgetController.subscribe(setState);
    void budgetController.load();
    return () => { off(); budgetController.invalidate(); };
  }, []);
  return <SettingsBudgetView state={state} onEdit={(value) => controller.current.edit(value)}
    onSave={() => { void controller.current.save(); }} onRetry={() => { void controller.current.load(); }} />;
}
