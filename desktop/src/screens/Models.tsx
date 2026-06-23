import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

interface Model {
  id: string;
  provider: string;
  context_window?: number | null;
  cost_in?: number | null;
  cost_out?: number | null;
  is_free?: boolean;
  capabilities?: string[];
}

const PAGE_SIZE = 50;

function fmtCtx(n?: number | null): string {
  if (!n) return "";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  return `${Math.round(n / 1000)}k`;
}

function fmtCost(m: Model): string {
  if (m.is_free) return "free";
  if (m.cost_in == null) return "—";
  return `$${m.cost_in}/M`;
}

export default function Models() {
  const [models, setModels] = useState<Model[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [provider, setProvider] = useState("");
  const [providers, setProviders] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [freeTotal, setFreeTotal] = useState(0);
  const [active, setActive] = useState("");
  const offsetRef = useRef(0);

  async function loadPage(off: number, append: boolean) {
    if (append) setLoadingMore(true);
    else setLoading(true);
    try {
      const qs = new URLSearchParams({ q, limit: String(PAGE_SIZE), offset: String(off) });
      if (freeOnly) qs.set("free", "true");
      if (provider) qs.set("provider", provider);
      const r = await api.get<{ total: number; free_count: number; models: Model[] }>(`/api/models/catalog?${qs}`);
      setTotal(r.total);
      if (!append) setFreeTotal(r.free_count ?? 0);
      setModels((prev) => (append ? [...prev, ...r.models] : r.models));
      offsetRef.current = off + r.models.length;
      setOffset(off + r.models.length);
    } catch { /* ignore */ } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    api.get<{ active: string }>("/models").then((r) => setActive(r.active)).catch(() => {});
    api.get<{ providers: string[] }>("/api/models/providers")
      .then((r) => setProviders(r.providers))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      setModels([]);
      setOffset(0);
      offsetRef.current = 0;
      loadPage(0, false);
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, freeOnly, provider]);

  async function switchTo(m: Model) {
    try {
      await api.post("/models/switch", { model: m.id, provider: m.provider });
      setActive(m.id);
    } catch (e) { alert(String(e)); }
  }

  const hasMore = models.length < total;

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-cpu head-icon" />
        <span className="title">Modelos</span>
        <span className="sub">
          {total} no catálogo
          {freeTotal > 0 && !freeOnly ? ` · ${freeTotal} grátis` : ""}
        </span>
      </div>

      <div className="content">
        <div className="row" style={{ marginBottom: 12 }}>
          <input
            className="in"
            placeholder="Buscar modelos…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          {providers.length > 0 && (
            <select
              className="in"
              style={{ width: "auto", minWidth: 120, flex: "0 0 auto" }}
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
            >
              <option value="">todos providers</option>
              {providers.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          )}
          <button
            className={"btn" + (freeOnly ? " primary" : "")}
            onClick={() => setFreeOnly((v) => !v)}
            title="Mostrar apenas modelos sem custo"
          >
            <i className="ti ti-coin" /> só grátis
          </button>
          {loading && <i className="ti ti-loader-2 spin" />}
        </div>

        {models.length === 0 ? (
          <div className="empty">
            {loading ? "Carregando catálogo…" : "Nenhum modelo encontrado."}
          </div>
        ) : (
          <>
            {models.map((m, idx) => {
              const prevFree = idx > 0 ? models[idx - 1].is_free : true;
              const isFirstItem = idx === 0;
              const isPaidSection = !m.is_free && prevFree;

              return (
                <div key={`${m.provider}/${m.id}`}>
                  {!freeOnly && isFirstItem && m.is_free && (
                    <div className="section-label">Modelos Gratuitos</div>
                  )}
                  {!freeOnly && isPaidSection && (
                    <div className="section-label" style={{ marginTop: 8 }}>Modelos Pagos</div>
                  )}
                  <div
                    className={"list-item" + (m.id === active ? " active" : "")}
                    onClick={() => switchTo(m)}
                  >
                    <div style={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
                      <span className="mono" style={{ color: "#e6edf3", fontSize: 12 }}>{m.id}</span>
                      {m.id === active && (
                        <span className="tag accent" style={{ marginLeft: 8 }}>ativo</span>
                      )}
                    </div>
                    <span className="tag">{m.provider}</span>
                    {fmtCtx(m.context_window) && (
                      <span className="muted mono" style={{ fontSize: 11 }}>
                        {fmtCtx(m.context_window)}
                      </span>
                    )}
                    <span
                      className="mono"
                      style={{
                        fontSize: 11,
                        color: m.is_free ? "var(--green)" : "var(--purple)",
                        minWidth: 52,
                        textAlign: "right",
                      }}
                    >
                      {fmtCost(m)}
                    </span>
                  </div>
                </div>
              );
            })}

            {hasMore && (
              <div style={{ textAlign: "center", paddingTop: 12 }}>
                <button
                  className="btn"
                  onClick={() => loadPage(offset, true)}
                  disabled={loadingMore}
                >
                  {loadingMore ? (
                    <><i className="ti ti-loader-2 spin" /> Carregando…</>
                  ) : (
                    <><i className="ti ti-chevrons-down" /> Carregar mais ({total - models.length} restantes)</>
                  )}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
