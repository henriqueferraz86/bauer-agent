import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";

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
  tool_name?: string | null;
  status?: string | null;
  message?: string | null;
  data?: Record<string, unknown>;
}

function ms(start: string, end?: string | null): string {
  const a = Date.parse(start);
  const b = end ? Date.parse(end) : Date.now();
  if (!Number.isFinite(a) || !Number.isFinite(b)) return "-";
  const sec = Math.max(0, Math.round((b - a) / 1000));
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

function short(id: string): string {
  return id.length > 18 ? `${id.slice(0, 18)}…` : id;
}

export default function Runs() {
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [selectedId, setSelectedId] = useState("");

  useEffect(() => {
    const load = () => {
      api.get<{ runs: RunRecord[] }>("/api/obs/runs?limit=200").then((r) => {
        const ordered = [...r.runs].reverse();
        setRuns(ordered);
        setSelectedId((current) => current || ordered[0]?.id || "");
      }).catch(() => {});
    };
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setEvents([]);
      return;
    }
    api.get<{ events: RuntimeEvent[] }>(`/api/obs/runs/${selectedId}/events`).then((r) => setEvents(r.events)).catch(() => setEvents([]));
  }, [selectedId]);

  const selected = useMemo(() => runs.find((run) => run.id === selectedId) || null, [runs, selectedId]);
  const toolEvents = events.filter((event) => event.event_type.startsWith("tool.call"));
  const policyEvents = events.filter((event) => event.event_type === "policy.evaluated");

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-player-play head-icon" />
        <span className="title">Runs</span>
        <span className="sub">{runs.length} registradas</span>
      </div>
      <div className="content">
        <div style={{ display: "grid", gridTemplateColumns: "minmax(360px, 1fr) minmax(420px, 1.2fr)", gap: 16 }}>
          <div>
            <div className="section-label">Runs</div>
            {runs.length === 0 ? <div className="empty">Sem runs registradas.</div> : runs.map((run) => (
              <div key={run.id} className={"list-item" + (run.id === selectedId ? " active" : "")} onClick={() => setSelectedId(run.id)}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="mono" style={{ color: "#e6edf3", fontSize: 12 }}>{short(run.id)}</div>
                  <div className="muted" style={{ fontSize: 11 }}>{run.agent_id} · {run.runtime_adapter} · {new Date(run.started_at).toLocaleString()}</div>
                </div>
                <span className={"tag" + (run.status === "completed" ? " green" : run.status === "failed" ? "" : " accent")}>{run.status}</span>
                <span className="mono" style={{ fontSize: 11 }}>${(run.cost_estimate ?? 0).toFixed(4)}</span>
              </div>
            ))}
          </div>

          <div>
            <div className="section-label">Detalhe</div>
            {!selected ? <div className="empty">Selecione uma run.</div> : (
              <>
                <div className="metric-grid" style={{ marginBottom: 12 }}>
                  <div className="metric"><div className="lbl">status</div><div className="val" style={{ fontSize: 16 }}>{selected.status}</div></div>
                  <div className="metric"><div className="lbl">agent</div><div className="val" style={{ fontSize: 16 }}>{selected.agent_id}</div></div>
                  <div className="metric"><div className="lbl">duracao</div><div className="val" style={{ fontSize: 16 }}>{ms(selected.started_at, selected.finished_at)}</div></div>
                  <div className="metric"><div className="lbl">tools</div><div className="val" style={{ fontSize: 16 }}>{selected.tool_calls_count}</div></div>
                </div>
                {selected.error && <div className="card" style={{ color: "var(--red)", marginBottom: 12 }}>{selected.error}</div>}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
                  <pre className="card logbox">{JSON.stringify(selected.input, null, 2)}</pre>
                  <pre className="card logbox">{JSON.stringify(selected.output || {}, null, 2)}</pre>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div>
                    <div className="section-label">Tools</div>
                    {toolEvents.length === 0 ? <div className="empty">Sem tools.</div> : toolEvents.map((event) => (
                      <div className="span-row" key={event.id}><span className="span-name">{event.tool_name || event.event_type}</span><span className="span-dur">{event.status || ""}</span></div>
                    ))}
                  </div>
                  <div>
                    <div className="section-label">Policy</div>
                    {policyEvents.length === 0 ? <div className="empty">Sem decisões.</div> : policyEvents.map((event) => (
                      <div className="span-row" key={event.id}><span className="span-name">{event.message || "policy"}</span><span className="span-dur">{event.status || ""}</span></div>
                    ))}
                  </div>
                </div>
                <div className="section-label">Eventos</div>
                {events.map((event) => (
                  <div className="span-row" key={event.id}><span className="span-name">{event.event_type}</span><span className="span-dur">{event.status || event.tool_name || ""}</span></div>
                ))}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
