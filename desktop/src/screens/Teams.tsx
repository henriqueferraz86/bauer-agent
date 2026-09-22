import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";

interface Member {
  id: string;
  name?: string;
  description?: string;
  runtime_adapter?: string;
}

interface Team {
  id: string;
  name: string;
  agents: string[];
  members: Member[];
  coordination: Record<string, unknown>;
  limits: Record<string, unknown>;
}

interface Budget {
  team_id: string;
  used_usd: number;
  limit_usd: number | null;
  remaining_usd: number | null;
  exceeded: boolean;
}

interface TeamRun {
  id?: string;
  run_id?: string;
  status: string;
  output?: unknown;
  result?: { output?: unknown };
  error?: string | null;
  team_id?: string;
}

interface RuntimeEvent {
  id?: string;
  event_type: string;
  agent_id?: string | null;
  status?: string | null;
  message?: string | null;
  timestamp?: string;
  data?: Record<string, unknown>;
}

function printable(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  return JSON.stringify(value, null, 2);
}

export default function Teams() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [budget, setBudget] = useState<Budget | null>(null);
  const [task, setTask] = useState("");
  const [run, setRun] = useState<TeamRun | null>(null);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const selected = useMemo(() => teams.find((team) => team.id === selectedId) || null, [teams, selectedId]);

  async function loadTeams() {
    try {
      const response = await api.get<{ teams: Team[] }>("/api/teams");
      setTeams(response.teams);
      setSelectedId((current) => current || response.teams[0]?.id || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function loadTeamData(teamId: string) {
    if (!teamId) return;
    try {
      setBudget(await api.get<Budget>(`/api/teams/${encodeURIComponent(teamId)}/budget`));
      if (run?.run_id || run?.id) {
        const runId = run.run_id || run.id;
        const response = await api.get<{ events: RuntimeEvent[] }>(`/api/teams/runs/${runId}/events`);
        setEvents(response.events);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => { void loadTeams(); }, []);
  useEffect(() => { void loadTeamData(selectedId); }, [selectedId, run?.id, run?.run_id]);

  async function executeTeam() {
    if (!selectedId || !task.trim()) return;
    setBusy(true);
    setError("");
    setEvents([]);
    try {
      const response = await api.post<TeamRun>(`/api/teams/${encodeURIComponent(selectedId)}/runs`, { task: task.trim() });
      setRun(response);
      const runId = response.run_id || response.id;
      if (runId) {
        const eventResponse = await api.get<{ events: RuntimeEvent[] }>(`/api/teams/runs/${runId}/events`);
        setEvents(eventResponse.events);
      }
      await loadTeamData(selectedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function cancelRun() {
    const runId = run?.run_id || run?.id;
    if (!runId) return;
    try {
      const response = await api.post<TeamRun>(`/api/teams/runs/${runId}/cancel`);
      setRun(response);
      await loadTeamData(selectedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-users-group head-icon" />
        <span className="title">Teams</span>
        <span className="sub">Agno como orquestrador default</span>
      </div>
      <div className="content">
        {error && <div className="audit-alert">{error}</div>}
        <div className="metric-grid" style={{ marginBottom: 16 }}>
          <div className="metric"><div className="lbl">times</div><div className="val">{teams.length}</div></div>
          <div className="metric"><div className="lbl">orquestrador</div><div className="val" style={{ fontSize: 16 }}>Agno</div></div>
          <div className="metric"><div className="lbl">run atual</div><div className="val" style={{ fontSize: 16 }}>{run?.status || "-"}</div></div>
          <div className="metric"><div className="lbl">custo usado</div><div className="val" style={{ fontSize: 16 }}>${(budget?.used_usd || 0).toFixed(4)}</div></div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", gap: 16 }}>
          <section>
            <div className="section-label">Times disponíveis</div>
            {teams.length === 0 ? <div className="empty">Nenhum time carregado.</div> : teams.map((team) => (
              <button className={"list-item" + (team.id === selectedId ? " active" : "")} key={team.id} onClick={() => setSelectedId(team.id)} style={{ width: "100%", textAlign: "left", border: 0 }}>
                <div style={{ flex: 1 }}><div className="mono">{team.id}</div><div className="muted">{team.name}</div></div>
                <span className="tag accent">{team.agents.length} agents</span>
              </button>
            ))}
          </section>
          <section>
            {selected ? <>
              <div className="section-label">{selected.name}</div>
              <div className="card">
                <div className="muted" style={{ marginBottom: 8 }}>Supervisor: <span className="mono">{String(selected.coordination.supervisor || selected.agents[0] || "-")}</span></div>
                <div className="muted" style={{ marginBottom: 12 }}>Orçamento: {budget?.limit_usd === null || budget?.limit_usd === undefined ? "sem limite" : `$${budget.limit_usd.toFixed(4)}`} · restante {budget?.remaining_usd === null || budget?.remaining_usd === undefined ? "-" : `$${budget.remaining_usd.toFixed(4)}`}</div>
                <div className="section-label">Membros</div>
                {selected.members.map((member) => <div className="list-item" key={member.id} style={{ cursor: "default" }}><div style={{ flex: 1 }}><div className="mono">{member.id}</div><div className="muted">{member.description || "agent do time"}</div></div><span className="tag green">Agno</span></div>)}
                <div className="section-label" style={{ marginTop: 16 }}>Executar tarefa</div>
                <div className="row"><input className="in" style={{ flex: 1 }} value={task} onChange={(event) => setTask(event.target.value)} placeholder="Descreva a tarefa do time" onKeyDown={(event) => event.key === "Enter" && void executeTeam()} /><button className="btn primary" onClick={() => void executeTeam()} disabled={busy || !task.trim()}>{busy ? "Executando…" : "Executar"}</button></div>
              </div>
              {run && <div className="card" style={{ marginTop: 16 }}><div className="row"><strong>Run {run.run_id || run.id}</strong><span className="tag accent">{run.status}</span>{run.status === "running" && <button className="btn danger" onClick={() => void cancelRun()}>Cancelar</button>}</div>{run.error && <div className="audit-alert" style={{ marginTop: 10 }}>{run.error}</div>}{Boolean(run.output || run.result?.output) && <pre className="code-block" style={{ marginTop: 10, whiteSpace: "pre-wrap" }}>{printable(run.output || run.result?.output)}</pre>}<div className="section-label" style={{ marginTop: 14 }}>Timeline</div>{events.length === 0 ? <div className="empty">Sem eventos para esta run.</div> : events.map((event, index) => <div className="list-item" key={event.id || `${event.event_type}-${index}`} style={{ cursor: "default" }}><i className="ti ti-point" /><div style={{ flex: 1 }}><div>{event.event_type}</div><div className="muted">{event.agent_id || "runtime"}{event.message ? ` · ${event.message}` : ""}</div></div><span className="tag">{event.status || "event"}</span></div>)}</div>}
            </> : <div className="empty">Selecione um time.</div>}
          </section>
        </div>
      </div>
    </div>
  );
}
