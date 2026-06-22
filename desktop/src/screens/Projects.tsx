import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Project {
  id: string;
  name: string;
  path: string;
  active: boolean;
  model?: string | null;
  provider?: string | null;
  telegram?: boolean;
  gateway_running?: boolean;
}
interface Stats { sessions: number; cost_usd: number; total_tokens: number; }

export default function Projects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [adding, setAdding] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    try {
      const r = await api.get<{ projects: Project[] }>("/api/projects");
      setProjects(r.projects);
      const sel = selected ? r.projects.find((p) => p.id === selected.id) : r.projects.find((p) => p.active);
      setSelected(sel || r.projects[0] || null);
    } catch (e) {
      setErr(String(e));
    }
  }
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  useEffect(() => {
    if (!selected) { setStats(null); return; }
    api.get<Stats>(`/api/projects/${selected.id}/stats`).then(setStats).catch(() => setStats(null));
  }, [selected]);

  async function add() {
    setErr("");
    try {
      await api.post("/api/projects", { path: newPath.trim() });
      setNewPath(""); setAdding(false);
      await load();
    } catch (e) {
      setErr(String(e));
    }
  }
  async function activate(id: string) {
    await api.post(`/api/projects/${id}/activate`);
    await load();
  }
  async function remove(id: string) {
    await api.del(`/api/projects/${id}`);
    setSelected(null);
    await load();
  }

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-folders head-icon" />
        <span className="title">Projetos</span>
        <span className="sub">{projects.length} workspaces</span>
        <div className="spacer" />
        <button className="btn primary" onClick={() => setAdding((v) => !v)}>
          <i className="ti ti-plus" /> Novo projeto
        </button>
      </div>

      <div className="content" style={{ display: "flex", gap: 16, padding: 0 }}>
        {/* lista */}
        <div style={{ width: 280, flexShrink: 0, borderRight: "1px solid var(--border)", padding: 14, overflowY: "auto" }}>
          {adding && (
            <div className="card" style={{ marginBottom: 8 }}>
              <input className="in" placeholder="C:\caminho\do\projeto" value={newPath}
                onChange={(e) => setNewPath(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn primary" onClick={add}>Adicionar</button>
                <button className="btn" onClick={() => setAdding(false)}>Cancelar</button>
              </div>
            </div>
          )}
          {err && <div className="card" style={{ color: "var(--red)", marginBottom: 8 }}>{err}</div>}
          {projects.length === 0 && !adding && <div className="empty">Nenhum projeto registrado.</div>}
          {projects.map((p) => (
            <div key={p.id} className={"list-item" + (selected?.id === p.id ? " active" : "")} onClick={() => setSelected(p)}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ color: "#e6edf3", fontWeight: 500 }}>{p.name}</div>
                <div className="muted mono" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.path}</div>
              </div>
              {p.active && <span className="dot on" />}
            </div>
          ))}
        </div>

        {/* detalhe */}
        <div style={{ flex: 1, padding: 16, overflowY: "auto" }}>
          {!selected ? (
            <div className="empty">Selecione um projeto.</div>
          ) : (
            <>
              <div className="row" style={{ marginBottom: 14 }}>
                <div className="sb-logo" style={{ marginBottom: 0 }}><i className="ti ti-bolt" /></div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 15, color: "#e6edf3", fontWeight: 500 }}>{selected.name}</div>
                  <div className="muted mono" style={{ fontSize: 11 }}>{selected.path}</div>
                </div>
                {!selected.active && <button className="btn primary" onClick={() => activate(selected.id)}>Ativar</button>}
                <button className="btn" onClick={() => remove(selected.id)}><i className="ti ti-trash" /></button>
              </div>

              <div className="row" style={{ gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
                {selected.provider && <span className="tag accent">{selected.provider}</span>}
                {selected.model && <span className="tag">{selected.model}</span>}
                {selected.telegram && <span className="tag green">Telegram</span>}
                <span className={"tag" + (selected.gateway_running ? " green" : "")}>
                  Gateway {selected.gateway_running ? "●" : "○"}
                </span>
              </div>

              {stats && (
                <div className="metric-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
                  <div className="metric"><div className="lbl">sessões</div><div className="val">{stats.sessions}</div></div>
                  <div className="metric"><div className="lbl">custo total</div><div className="val">${stats.cost_usd.toFixed(3)}</div></div>
                  <div className="metric"><div className="lbl">tokens</div><div className="val">{stats.total_tokens.toLocaleString()}</div></div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
