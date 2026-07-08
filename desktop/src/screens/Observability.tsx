import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";

interface Summary {
  cost_today_usd: number;
  tokens_today: number;
  calls_today: number;
  sessions_today: number;
  cost_total_usd: number;
  p95_ms: number | null;
}

interface RunRecord {
  id: string;
  session_id: string;
  agent_id: string;
  runtime_adapter: string;
  status: string;
  input: Record<string, unknown>;
  output?: Record<string, unknown> | null;
  error?: string | null;
  started_at: string;
  finished_at?: string | null;
  cost_estimate?: number | null;
  tool_calls_count: number;
}

interface RuntimeEvent {
  id: string;
  event_type: string;
  timestamp: string;
  status?: string | null;
  message?: string | null;
  tool_name?: string | null;
  data?: Record<string, unknown>;
}

interface TraceSpan {
  id: string;
  name: string;
  timestamp: string;
  status?: string | null;
  attributes?: Record<string, unknown>;
}

interface Approval {
  id: string;
  operation: string;
  tool_name: string;
  status: string;
  reason: string;
  risk_level: string;
  run_id?: string | null;
}

export default function Observability() {
  const [sum, setSum] = useState<Summary | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [spans, setSpans] = useState<TraceSpan[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");

  useEffect(() => {
    const load = () => {
      api.get<Summary>("/api/obs/summary").then(setSum).catch(() => {});
      api.get<{ runs: RunRecord[] }>("/api/obs/runs?limit=80").then((r) => {
        const ordered = [...r.runs].reverse();
        setRuns(ordered);
        setSelectedRunId((current) => current || ordered[0]?.id || "");
      }).catch(() => {});
      api.get<{ approvals: Approval[] }>("/api/obs/approvals?status=pending").then((r) => {
        setApprovals(r.approvals);
      }).catch(() => {});
    };
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!selectedRunId) {
      setEvents([]);
      setSpans([]);
      return;
    }
    api.get<{ events: RuntimeEvent[] }>(`/api/obs/runs/${selectedRunId}/events`).then((r) => {
      setEvents(r.events);
    }).catch(() => setEvents([]));
    api.get<{ spans: TraceSpan[] }>(`/api/obs/runs/${selectedRunId}/trace`).then((r) => {
      setSpans(r.spans);
    }).catch(() => setSpans([]));
  }, [selectedRunId]);

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) || null,
    [runs, selectedRunId]
  );
  const failed = runs.filter((run) => run.status === "failed").length;
  const active = runs.filter((run) => ["queued", "running", "waiting_approval"].includes(run.status)).length;

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-chart-bar head-icon" />
        <span className="title">Observabilidade</span>
      </div>
      <div className="content">
        <div className="metric-grid" style={{ marginBottom: 16 }}>
          <div className="metric"><div className="lbl"><i className="ti ti-player-play" /> runs</div><div className="val">{runs.length}</div></div>
          <div className="metric"><div className="lbl"><i className="ti ti-activity" /> ativas</div><div className="val">{active}</div></div>
          <div className="metric"><div className="lbl"><i className="ti ti-alert-triangle" /> falhas</div><div className="val">{failed}</div></div>
          <div className="metric"><div className="lbl"><i className="ti ti-shield-question" /> aprovacoes</div><div className="val">{approvals.length}</div></div>
          <div className="metric"><div className="lbl"><i className="ti ti-brain" /> tokens hoje</div><div className="val">{(sum?.tokens_today ?? 0).toLocaleString()}</div></div>
          <div className="metric"><div className="lbl"><i className="ti ti-clock" /> p95</div><div className="val">{sum?.p95_ms ? `${Math.round(sum.p95_ms)}ms` : "-"}</div></div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 0.9fr) minmax(360px, 1.4fr)", gap: 16 }}>
          <div>
            <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}><i className="ti ti-list-details" /> RUNS</div>
            {runs.length === 0 ? <div className="empty">Sem runs ainda.</div> : runs.map((run) => (
              <button
                key={run.id}
                onClick={() => setSelectedRunId(run.id)}
                className="row"
                style={{
                  width: "100%",
                  justifyContent: "space-between",
                  marginBottom: 6,
                  border: selectedRunId === run.id ? "1px solid var(--purple)" : "1px solid var(--border)",
                  background: "var(--panel)",
                  color: "var(--text)",
                  padding: "9px 10px",
                  borderRadius: 6,
                  cursor: "pointer",
                }}
              >
                <span className="mono" style={{ fontSize: 11 }}>{run.id.replace("run-", "run-").slice(0, 18)}</span>
                <span className="mono" style={{ fontSize: 11 }}>{run.status}</span>
              </button>
            ))}
          </div>

          <div>
            <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}><i className="ti ti-route" /> DETALHE DA RUN</div>
            {!selectedRun ? <div className="empty">Selecione uma run.</div> : (
              <>
                <div className="metric-grid" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))", marginBottom: 12 }}>
                  <div className="metric"><div className="lbl">status</div><div className="val" style={{ fontSize: 16 }}>{selectedRun.status}</div></div>
                  <div className="metric"><div className="lbl">adapter</div><div className="val" style={{ fontSize: 16 }}>{selectedRun.runtime_adapter}</div></div>
                  <div className="metric"><div className="lbl">tools</div><div className="val" style={{ fontSize: 16 }}>{selectedRun.tool_calls_count}</div></div>
                  <div className="metric"><div className="lbl">custo</div><div className="val" style={{ fontSize: 16 }}>${(selectedRun.cost_estimate ?? 0).toFixed(4)}</div></div>
                </div>

                {selectedRun.error ? <div className="empty" style={{ marginBottom: 12 }}>{selectedRun.error}</div> : null}

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                  <div>
                    <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}><i className="ti ti-timeline" /> EVENTOS</div>
                    {events.length === 0 ? <div className="empty">Sem eventos.</div> : events.map((event) => (
                      <div className="span-row" key={event.id}>
                        <span className="span-name">{event.event_type}</span>
                        <span className="span-dur">{event.status || event.tool_name || ""}</span>
                      </div>
                    ))}
                  </div>
                  <div>
                    <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}><i className="ti ti-stairs" /> TRACE</div>
                    {spans.length === 0 ? <div className="empty">Sem trace.</div> : spans.map((span) => (
                      <div className="span-row" key={span.id}>
                        <span className="span-name">{span.name}</span>
                        <span className="span-dur">{span.status || ""}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>

        {approvals.length > 0 ? (
          <div style={{ marginTop: 16 }}>
            <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}><i className="ti ti-shield-question" /> APROVACOES PENDENTES</div>
            {approvals.map((approval) => (
              <div className="span-row" key={approval.id}>
                <span className="span-name">{approval.operation} / {approval.tool_name}</span>
                <span className="span-dur">{approval.risk_level}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
