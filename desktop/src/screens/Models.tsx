import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Model {
  id: string;
  provider: string;
  context_window?: number | null;
  cost_in?: number | null;
  cost_out?: number | null;
  capabilities?: string[];
}

export default function Models() {
  const [models, setModels] = useState<Model[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [free, setFree] = useState(false);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState("");

  async function load() {
    setLoading(true);
    try {
      const qs = new URLSearchParams({ q, limit: "100" });
      if (free) qs.set("free", "true");
      const r = await api.get<{ total: number; models: Model[] }>(`/api/models/catalog?${qs}`);
      setModels(r.models);
      setTotal(r.total);
    } catch { /* ignore */ } finally { setLoading(false); }
  }
  useEffect(() => { api.get<{ active: string }>("/models").then((r) => setActive(r.active)).catch(() => {}); }, []);
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); /* eslint-disable-next-line */ }, [q, free]);

  async function switchTo(id: string) {
    try { await api.post("/models/switch", { model: id }); setActive(id); }
    catch (e) { alert(String(e)); }
  }

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-cpu head-icon" />
        <span className="title">Modelos</span>
        <span className="sub">{total} no catálogo</span>
      </div>
      <div className="content">
        <div className="row" style={{ marginBottom: 12 }}>
          <input className="in" placeholder="Buscar modelos…" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className={"btn" + (free ? " primary" : "")} onClick={() => setFree((v) => !v)}>
            <i className="ti ti-coin" /> grátis
          </button>
          {loading && <i className="ti ti-loader-2 spin" />}
        </div>
        {models.length === 0 ? (
          <div className="empty">{loading ? "Carregando catálogo…" : "Nenhum modelo encontrado."}</div>
        ) : (
          models.map((m) => (
            <div className={"list-item" + (m.id === active ? " active" : "")} key={`${m.provider}/${m.id}`}
              onClick={() => switchTo(m.id)}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <span className="mono" style={{ color: "#e6edf3" }}>{m.id}</span>
                {m.id === active && <span className="tag accent" style={{ marginLeft: 8 }}>ativo</span>}
              </div>
              <span className="tag">{m.provider}</span>
              {m.context_window ? <span className="muted mono" style={{ fontSize: 11 }}>{Math.round(m.context_window / 1000)}k</span> : null}
              <span className="mono" style={{ fontSize: 11, color: m.cost_in ? "var(--purple)" : "var(--green)" }}>
                {m.cost_in ? `$${m.cost_in}/M` : "free"}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
