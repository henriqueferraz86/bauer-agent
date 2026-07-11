import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useNavigate, useParams } from "react-router-dom";

type Pair = [string, number];

interface AuditReport {
  window: string;
  runs_total: number;
  runs_completed: number;
  runs_failed: number;
  runs_cancelled: number;
  runs_waiting_approval: number;
  success_rate: number;
  average_duration_ms: number | null;
  approvals_pending: number;
  policy_allow: number;
  policy_ask: number;
  policy_deny: number;
  estimated_cost_usd: number;
  most_used_skills: Pair[];
  most_failed_skills: Pair[];
  most_used_tools: Pair[];
  top_errors: Pair[];
}

interface RunRecord {
  id: string;
  agent_id: string;
  runtime_adapter: string;
  status: string;
  started_at: string;
  cost_estimate?: number | null;
}

interface RunAudit {
  run_id: string;
  status: string;
  agent_id: string;
  runtime_adapter: string;
  duration_ms: number | null;
  prompt: string;
  final_answer: string;
  error?: string | null;
  skills_used: string[];
  tools_used: string[];
  policy_decisions: Array<{ action: string; operation: string; risk_level: string }>;
  approvals: Array<{ type: string; tool_name: string; status: string; message: string }>;
  event_details: Array<{ id: string; timestamp: string; event_type: string; status?: string | null; message?: string | null; tool_name?: string | null; skill_id?: string | null; data?: Record<string, unknown> }>;
}

interface RunScore {
  score: number;
  max_score: number;
  reasons: string[];
  warnings: string[];
}

interface SkillInsights {
  suggestions: Array<{ suggested_id: string; reason: string; tools: string[]; occurrences: number }>;
}

const WINDOWS = ["24h", "7d", "30d"];

export default function Audit() {
  const navigate = useNavigate();
  const { runId = "" } = useParams();
  const [window, setWindow] = useState("24h");
  const [report, setReport] = useState<AuditReport | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [insights, setInsights] = useState<SkillInsights | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<RunAudit | null>(null);
  const [score, setScore] = useState<RunScore | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    Promise.all([
      api.get<AuditReport>(`/api/audit/report?last=${window}`),
      api.get<{ runs: RunRecord[] }>("/api/obs/runs?limit=100"),
      api.get<SkillInsights>(`/api/audit/skills/insights?last=${window}`),
    ]).then(([nextReport, runPayload, nextInsights]) => {
      const ordered = [...runPayload.runs].reverse();
      setReport(nextReport);
      setRuns(ordered);
      setInsights(nextInsights);
      setSelectedId((current) => runId || current || ordered[0]?.id || "");
    }).catch((reason: Error) => setError(reason.message));
  }, [window, runId]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setScore(null);
      return;
    }
    Promise.all([
      api.get<RunAudit>(`/api/audit/runs/${selectedId}`),
      api.get<RunScore>(`/api/audit/runs/${selectedId}/score`),
    ]).then(([nextDetail, nextScore]) => {
      setDetail(nextDetail);
      setScore(nextScore);
    }).catch((reason: Error) => setError(reason.message));
  }, [selectedId]);

  const selectedRun = useMemo(() => runs.find((run) => run.id === selectedId), [runs, selectedId]);
  const scoreTone = (score?.score ?? 0) >= 4 ? "good" : (score?.score ?? 0) >= 3 ? "warn" : "bad";

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-shield-check head-icon" />
        <span className="title">Auditoria</span>
        <span className="sub">governanca do runtime</span>
        <span className="spacer" />
        <div className="audit-segments" aria-label="Janela de auditoria">
          {WINDOWS.map((item) => (
            <button key={item} className={window === item ? "active" : ""} onClick={() => setWindow(item)}>{item}</button>
          ))}
        </div>
      </div>
      <div className="content audit-page">
        {error && <div className="audit-alert"><i className="ti ti-alert-circle" /> {error}</div>}

        <div className="audit-metrics">
          <Metric icon="ti-player-play" label="runs" value={report?.runs_total ?? 0} />
          <Metric icon="ti-circle-check" label="sucesso" value={`${((report?.success_rate ?? 0) * 100).toFixed(1)}%`} tone="good" />
          <Metric icon="ti-alert-triangle" label="runs com falha" value={report?.runs_failed ?? 0} tone={(report?.runs_failed ?? 0) ? "bad" : "good"} />
          <Metric icon="ti-clock" label="duracao media" value={formatDuration(report?.average_duration_ms)} />
          <Metric icon="ti-coin" label="custo" value={`$${(report?.estimated_cost_usd ?? 0).toFixed(4)}`} />
          <Metric icon="ti-shield-question" label="approvals" value={report?.approvals_pending ?? 0} tone={(report?.approvals_pending ?? 0) ? "warn" : "good"} />
          <Metric icon="ti-shield-x" label="policy deny" value={report?.policy_deny ?? 0} tone={(report?.policy_deny ?? 0) ? "bad" : "good"} />
        </div>

        <div className="audit-overview">
          <Ranking title="Skills mais usadas" icon="ti-puzzle" items={report?.most_used_skills ?? []} />
          <Ranking title="Skills com falha" icon="ti-alert-triangle" items={report?.most_failed_skills ?? []} />
          <Ranking title="Tools mais usadas" icon="ti-tool" items={report?.most_used_tools ?? []} />
          <Ranking title="Falhas recorrentes" icon="ti-bug" items={report?.top_errors ?? []} />
        </div>

        <div className="audit-workspace">
          <section className="audit-run-list">
            <div className="section-label">Runs recentes</div>
            {runs.length === 0 ? <div className="empty">Sem runs registradas.</div> : runs.map((run) => (
              <button key={run.id} className={run.id === selectedId ? "active" : ""} onClick={() => { setSelectedId(run.id); navigate(`/audit/runs/${run.id}`); }}>
                <span>
                  <strong>{shortId(run.id)}</strong>
                  <small>{run.agent_id} · {new Date(run.started_at).toLocaleString()}</small>
                </span>
                <em className={`audit-status ${run.status}`}>{run.status}</em>
              </button>
            ))}
          </section>

          <section className="audit-detail">
            <div className="audit-detail-head">
              <div>
                <div className="section-label">Detalhe da run</div>
                <strong className="mono">{selectedRun ? shortId(selectedRun.id) : "-"}</strong>
              </div>
              {score && <div className={`audit-score ${scoreTone}`}><span>{score.score}</span><small>/ {score.max_score}</small></div>}
            </div>
            {!detail ? <div className="empty">Selecione uma run.</div> : (
              <>
                <div className="audit-facts">
                  <span><small>status</small>{detail.status}</span>
                  <span><small>agent</small>{detail.agent_id || "-"}</span>
                  <span><small>adapter</small>{detail.runtime_adapter || "-"}</span>
                  <span><small>duracao</small>{formatDuration(detail.duration_ms)}</span>
                </div>
                {detail.error && <div className="audit-alert"><i className="ti ti-alert-triangle" /> {detail.error}</div>}
                <DetailBlock title="Prompt" icon="ti-message-question" text={detail.prompt || "Sem prompt registrado."} />
                <DetailBlock title="Resposta final" icon="ti-message-check" text={detail.final_answer || "Sem resposta final registrada."} accent />
                <div className="audit-two-col">
                  <TagList title="Skills" items={detail.skills_used} />
                  <TagList title="Tools" items={detail.tools_used} />
                </div>
                <div className="audit-two-col">
                  <EventList title="Policy" items={detail.policy_decisions.map((item) => ({ name: item.operation || "policy", value: item.action }))} />
                  <EventList title="Approvals" items={detail.approvals.map((item) => ({ name: item.tool_name || item.type, value: item.status }))} />
                </div>
                <Timeline events={detail.event_details} />
                {score && (score.warnings.length > 0 || score.reasons.length > 0) && (
                  <div className="audit-score-notes">
                    {score.reasons.map((item) => <span className="ok" key={item}><i className="ti ti-check" />{item}</span>)}
                    {score.warnings.map((item) => <span className="warning" key={item}><i className="ti ti-alert-triangle" />{item}</span>)}
                  </div>
                )}
              </>
            )}
          </section>
        </div>

        <section className="audit-suggestions">
          <div className="section-label">Candidatas a skill</div>
          {(insights?.suggestions ?? []).length === 0 ? <div className="empty">Nenhum padrao repetido no periodo.</div> : insights?.suggestions.map((item) => (
            <div key={item.suggested_id}>
              <i className="ti ti-bulb" />
              <span><strong>{item.suggested_id}</strong><small>{item.tools.join(" -> ")} · {item.occurrences} runs · exige aprovacao humana</small></span>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}

function Metric({ icon, label, value, tone = "" }: { icon: string; label: string; value: string | number; tone?: string }) {
  return <div className={`audit-metric ${tone}`}><div><i className={`ti ${icon}`} />{label}</div><strong>{value}</strong></div>;
}

function Ranking({ title, icon, items }: { title: string; icon: string; items: Pair[] }) {
  const max = Math.max(...items.map((item) => item[1]), 1);
  return <section className="audit-ranking"><h3><i className={`ti ${icon}`} />{title}</h3>{items.length === 0 ? <p>Sem dados.</p> : items.slice(0, 5).map(([name, count]) => <div key={name}><span title={name}>{name}</span><i><b style={{ width: `${(count / max) * 100}%` }} /></i><em>{count}</em></div>)}</section>;
}

function DetailBlock({ title, icon, text, accent = false }: { title: string; icon: string; text: string; accent?: boolean }) {
  return <div className={`audit-block ${accent ? "accent" : ""}`}><h3><i className={`ti ${icon}`} />{title}</h3><p>{text}</p></div>;
}

function TagList({ title, items }: { title: string; items: string[] }) {
  return <div className="audit-list"><h3>{title}</h3><div>{items.length ? items.map((item) => <span key={item}>{item}</span>) : <small>Sem registros.</small>}</div></div>;
}

function EventList({ title, items }: { title: string; items: Array<{ name: string; value: string }> }) {
  return <div className="audit-events"><h3>{title}</h3>{items.length ? items.map((item, index) => <div key={`${item.name}-${index}`}><span>{item.name}</span><em>{item.value}</em></div>) : <small>Sem registros.</small>}</div>;
}

// Rótulos amigáveis das tools (espelha bauer/core/ux/progress.py — as principais).
const TOOL_LABEL: Record<string, string> = {
  run_command: "Executou comando", execute_code: "Executou código", process: "Executou processo",
  read_file: "Leu arquivos", list_dir: "Explorou arquivos", glob_files: "Procurou arquivos",
  search_text: "Procurou no código", write_file: "Escreveu arquivo", append_file: "Escreveu arquivo",
  create_dir: "Criou pasta", patch: "Editou arquivo", move_file: "Moveu arquivo",
  delete_file: "Removeu arquivo", web_search: "Pesquisou na web", web_fetch: "Leu página web",
  image_generate: "Gerou imagem", delegate_task: "Delegou a especialista", calculate: "Calculou",
};

function eventLabel(e: RunAudit["event_details"][number]): { label: string; icon: string; tone: string } {
  const t = e.event_type;
  const d = (e.data || {}) as Record<string, string>;
  if (t === "run.created" || t === "run.started") return { label: "Iniciou a run", icon: "ti-player-play", tone: "" };
  if (t === "run.completed") return { label: "Concluiu", icon: "ti-circle-check", tone: "ok" };
  if (t === "run.failed") return { label: "Falhou", icon: "ti-alert-triangle", tone: "bad" };
  if (t === "run.cancelled") return { label: "Cancelou", icon: "ti-ban", tone: "warn" };
  if (t === "skill.selected") return { label: `Skill: ${e.skill_id || "?"}`, icon: "ti-sparkles", tone: "" };
  if (t === "model.route.selected") return { label: `Modelo: ${d.model || "?"} (${d.tier || "?"})`, icon: "ti-arrows-shuffle", tone: "" };
  if (t.startsWith("tool.call")) return { label: TOOL_LABEL[e.tool_name || ""] || e.tool_name || "tool", icon: "ti-tool", tone: t.endsWith("failed") ? "bad" : "" };
  if (t === "policy.evaluated") return { label: `Policy: ${d.operation || "?"}`, icon: "ti-shield-half", tone: "" };
  if (t.startsWith("approval")) return { label: `Aprovação (${e.tool_name || "?"})`, icon: "ti-shield-question", tone: "warn" };
  if (t.startsWith("budget")) return { label: "Budget", icon: "ti-coin", tone: "warn" };
  return { label: t, icon: "ti-point", tone: "" };
}

function Timeline({ events }: { events: RunAudit["event_details"] }) {
  if (!events.length) return <div className="audit-timeline"><h3><i className="ti ti-timeline" /> Timeline</h3><small>Sem eventos.</small></div>;
  const t0 = new Date(events[0].timestamp).getTime();
  return (
    <div className="audit-timeline">
      <h3><i className="ti ti-timeline" /> Timeline</h3>
      <ol>
        {events.map((e, i) => {
          const dt = (new Date(e.timestamp).getTime() - t0) / 1000;
          const { label, icon, tone } = eventLabel(e);
          return (
            <li key={e.id || i} className={tone}>
              <span className="tl-time">+{isFinite(dt) ? dt.toFixed(1) : "0.0"}s</span>
              <i className={`ti ${icon}`} />
              <span className="tl-label">{label}</span>
              {e.status && <em className="tl-status">{e.status}</em>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function formatDuration(value?: number | null) {
  if (value == null) return "-";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`;
}

function shortId(value: string) {
  return value.length > 22 ? `${value.slice(0, 18)}...` : value;
}
