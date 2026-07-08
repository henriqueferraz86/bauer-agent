import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";

interface RecentRun {
  id: string;
  agent_id: string;
  status: string;
  runtime_adapter?: string;
  started_at?: string;
  updated_at?: string;
}

interface FailedTask {
  id: string;
  name: string;
  last_error: string;
  last_run_id?: string | null;
  next_run_at?: string | null;
}

interface HomeData {
  active_agents: number;
  pending_approvals_count: number;
  pending_approvals: { id: string; operation?: string; risk_level?: string }[];
  failed_scheduled_count: number;
  failed_scheduled: FailedTask[];
  budget: {
    used_usd?: number | null;
    limit_usd?: number | null;
    remaining_usd?: number | null;
    exceeded?: boolean | null;
    cost_today_usd?: number | null;
    calls_today?: number | null;
  };
  recent_runs: RecentRun[];
}

// Converte USD (unidade interna do runtime) para exibição em R$ usando uma taxa
// fixa de referência. O valor autoritativo continua em USD no backend; aqui é só
// apresentação no idioma/moeda do usuário.
const USD_TO_BRL = 5.0;
function brl(usd: number | null | undefined): string {
  const v = (usd ?? 0) * USD_TO_BRL;
  return `R$ ${v.toFixed(2).replace(".", ",")}`;
}

function runTone(status: string): string {
  if (status === "completed") return " green";
  if (status === "failed") return " red";
  if (status === "cancelled") return "";
  return " accent"; // queued / running / waiting_approval
}

export default function Home() {
  const [data, setData] = useState<HomeData | null>(null);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    const load = () =>
      api
        .get<HomeData>("/api/os/home")
        .then((d) => {
          setData(d);
          setError("");
        })
        .catch((e) => setError(String(e)));
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, []);

  const budget = data?.budget || {};
  const usedPct =
    budget.limit_usd && budget.limit_usd > 0
      ? Math.min(100, Math.round(((budget.used_usd || 0) / budget.limit_usd) * 100))
      : 0;

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-home head-icon" />
        <span className="title">Bauer OS</span>
        <span className="sub">Hoje</span>
      </div>
      <div className="content">
        {error && !data ? (
          <div className="empty">Não consegui carregar o painel. {error}</div>
        ) : !data ? (
          <div className="empty">Carregando painel.</div>
        ) : (
          <>
            <div className="metric-grid" style={{ marginBottom: 16 }}>
              <div
                className="metric"
                style={{ cursor: "pointer" }}
                onClick={() => navigate("/runs")}
                title="Ver execuções"
              >
                <div className="lbl">agentes ativos</div>
                <div className="val">{data.active_agents}</div>
              </div>
              <div
                className="metric"
                style={{ cursor: "pointer" }}
                onClick={() => navigate("/approvals")}
                title="Ver aprovações"
              >
                <div className="lbl">aprovações pendentes</div>
                <div className="val">{data.pending_approvals_count}</div>
              </div>
              <div
                className="metric"
                style={{ cursor: data.failed_scheduled_count ? "pointer" : "default" }}
                onClick={() => data.failed_scheduled_count && navigate("/observability")}
                title="Tarefas agendadas com falha"
              >
                <div className="lbl">tarefas agendadas falharam</div>
                <div className="val" style={{ color: data.failed_scheduled_count ? "#f85149" : undefined }}>
                  {data.failed_scheduled_count}
                </div>
              </div>
              <div className="metric" title="Budget do dia">
                <div className="lbl">budget usado</div>
                <div className="val" style={{ fontSize: 18 }}>
                  {brl(budget.used_usd)} <span className="muted" style={{ fontSize: 12 }}>/ {brl(budget.limit_usd)}</span>
                </div>
                <div className="budget-bar" style={{ marginTop: 6, height: 4, background: "#21262d", borderRadius: 2 }}>
                  <div
                    style={{
                      width: `${usedPct}%`,
                      height: "100%",
                      borderRadius: 2,
                      background: budget.exceeded ? "#f85149" : usedPct >= 80 ? "#d29922" : "#2ea043",
                    }}
                  />
                </div>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <div>
                <div className="section-label">Últimas execuções</div>
                {data.recent_runs.length === 0 ? (
                  <div className="empty">Nenhuma execução ainda.</div>
                ) : (
                  data.recent_runs.map((run) => (
                    <div
                      className="list-item"
                      key={run.id}
                      onClick={() => navigate("/runs")}
                      style={{ cursor: "pointer" }}
                    >
                      <div style={{ flex: 1 }}>
                        <div className="mono" style={{ color: "#e6edf3" }}>{run.agent_id}</div>
                        <div className="muted" style={{ fontSize: 11 }}>{run.started_at}</div>
                      </div>
                      <span className={"tag" + runTone(run.status)}>{run.status}</span>
                    </div>
                  ))
                )}
              </div>
              <div>
                <div className="section-label">Precisa de você</div>
                {data.pending_approvals_count === 0 && data.failed_scheduled_count === 0 ? (
                  <div className="empty">Tudo em dia. Nada pendente.</div>
                ) : (
                  <>
                    {data.pending_approvals.map((appr) => (
                      <div
                        className="list-item"
                        key={appr.id}
                        onClick={() => navigate("/approvals")}
                        style={{ cursor: "pointer" }}
                      >
                        <div style={{ flex: 1 }}>
                          <div className="mono" style={{ color: "#e6edf3" }}>{appr.operation || "ação"}</div>
                          <div className="muted" style={{ fontSize: 11 }}>aguardando aprovação</div>
                        </div>
                        <span className="tag accent">{appr.risk_level || "?"}</span>
                      </div>
                    ))}
                    {data.failed_scheduled.map((task) => (
                      <div
                        className="list-item"
                        key={task.id}
                        onClick={() => navigate("/observability")}
                        style={{ cursor: "pointer" }}
                      >
                        <div style={{ flex: 1 }}>
                          <div className="mono" style={{ color: "#e6edf3" }}>{task.name}</div>
                          <div className="muted" style={{ fontSize: 11 }}>{task.last_error}</div>
                        </div>
                        <span className="tag red">falhou</span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
