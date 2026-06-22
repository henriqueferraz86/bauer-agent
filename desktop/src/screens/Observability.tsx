import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Summary {
  cost_today_usd: number;
  tokens_today: number;
  calls_today: number;
  sessions_today: number;
  cost_total_usd: number;
  p50_ms: number | null;
  p95_ms: number | null;
}
interface ModelCost { model: string; cost_usd: number; total_tokens: number; calls: number; }
interface Span { name?: string; duration_ms?: number; trace_id?: string; }

export default function Observability() {
  const [sum, setSum] = useState<Summary | null>(null);
  const [byModel, setByModel] = useState<ModelCost[]>([]);
  const [spans, setSpans] = useState<Span[]>([]);

  useEffect(() => {
    const load = () => {
      api.get<Summary>("/api/obs/summary").then(setSum).catch(() => {});
      api.get<{ by_model: ModelCost[] }>("/api/obs/cost").then((r) => setByModel(r.by_model)).catch(() => {});
      api.get<{ spans: Span[] }>("/api/obs/traces?limit=20").then((r) => setSpans(r.spans)).catch(() => {});
    };
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);

  const maxCost = Math.max(...byModel.map((m) => m.cost_usd), 0.0001);
  const maxDur = Math.max(...spans.map((s) => s.duration_ms || 0), 1);

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-chart-bar head-icon" />
        <span className="title">Observabilidade</span>
      </div>
      <div className="content">
        <div className="metric-grid" style={{ marginBottom: 16 }}>
          <div className="metric"><div className="lbl"><i className="ti ti-coin" /> custo hoje</div><div className="val">${(sum?.cost_today_usd ?? 0).toFixed(3)}</div></div>
          <div className="metric"><div className="lbl"><i className="ti ti-brain" /> tokens</div><div className="val">{(sum?.tokens_today ?? 0).toLocaleString()}</div></div>
          <div className="metric"><div className="lbl"><i className="ti ti-message-2" /> sessões</div><div className="val">{sum?.sessions_today ?? 0}</div></div>
          <div className="metric"><div className="lbl"><i className="ti ti-clock" /> p95</div><div className="val">{sum?.p95_ms ? `${Math.round(sum.p95_ms)}ms` : "—"}</div></div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16 }}>
          <div>
            <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}><i className="ti ti-timeline" /> SPANS RECENTES</div>
            {spans.length === 0 ? <div className="empty">Sem traces ainda.</div> : spans.map((s, i) => (
              <div className="span-row" key={i}>
                <span className="span-name">{s.name || "span"}</span>
                <div className="span-track">
                  <div className="span-bar" style={{ left: 0, width: `${((s.duration_ms || 0) / maxDur) * 100}%` }} />
                </div>
                <span className="span-dur">{s.duration_ms ? `${Math.round(s.duration_ms)}ms` : "—"}</span>
              </div>
            ))}
          </div>

          <div>
            <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}><i className="ti ti-coin" /> CUSTO POR MODELO</div>
            {byModel.length === 0 ? <div className="empty">Sem custo registrado.</div> : byModel.slice(0, 8).map((m) => (
              <div key={m.model} style={{ marginBottom: 8 }}>
                <div className="row" style={{ justifyContent: "space-between", marginBottom: 3 }}>
                  <span className="mono" style={{ fontSize: 11 }}>{m.model}</span>
                  <span className="mono" style={{ fontSize: 11, color: m.cost_usd ? "var(--purple)" : "var(--green)" }}>
                    ${m.cost_usd.toFixed(3)}
                  </span>
                </div>
                <div className="bar-track"><div className="bar-fill" style={{ width: `${(m.cost_usd / maxCost) * 100}%` }} /></div>
              </div>
            ))}
            <div className="muted mono" style={{ fontSize: 11, marginTop: 12 }}>
              total acumulado: ${(sum?.cost_total_usd ?? 0).toFixed(4)}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
