import { useEffect, useState } from "react";
import { api } from "../api/client";

interface GwStatus {
  telegram: boolean;
  discord: boolean;
  running: boolean;
  pid: number | null;
  uptime_s: number | null;
}

function uptime(s: number | null): string {
  if (!s) return "—";
  const m = Math.floor(s / 60), h = Math.floor(m / 60);
  if (h) return `${h}h ${m % 60}m`;
  return `${m}m`;
}

export default function Gateway() {
  const [st, setSt] = useState<GwStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function load() {
    try { setSt(await api.get<GwStatus>("/api/gateway/status")); } catch { /* ignore */ }
  }
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);

  async function control(action: "start" | "stop") {
    if (action === "stop" && !confirm("Parar o gateway? Os canais ficarão offline.")) return;
    setBusy(true); setMsg("");
    try {
      const r = await api.post<{ detail: string }>(`/api/gateway/${action}`);
      setMsg(r.detail);
      await load();
    } catch (e) { setMsg(String(e)); } finally { setBusy(false); }
  }

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-router head-icon" />
        <span className="title">Gateway</span>
        <div className="spacer" />
        {st?.running ? (
          <button className="btn" disabled={busy} onClick={() => control("stop")}><i className="ti ti-player-stop" /> Parar</button>
        ) : (
          <button className="btn primary" disabled={busy} onClick={() => control("start")}><i className="ti ti-player-play" /> Iniciar</button>
        )}
      </div>
      <div className="content">
        {!st ? <div className="empty">Carregando status…</div> : (
          <>
            <div className="card" style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 10 }}>
              <i className="ti ti-send" style={{ color: "var(--accent)", fontSize: 20 }} />
              <div style={{ flex: 1 }}>
                <div style={{ color: "#e6edf3" }}>Telegram</div>
                <div className="muted" style={{ fontSize: 11 }}>{st.telegram ? "habilitado no config" : "desabilitado"}</div>
              </div>
              <span className={"tag" + (st.telegram && st.running ? " green" : "")}>
                {st.telegram && st.running ? "● online" : "○ offline"}
              </span>
            </div>
            <div className="card" style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 10 }}>
              <i className="ti ti-brand-discord" style={{ color: "#7289da", fontSize: 20 }} />
              <div style={{ flex: 1 }}>
                <div style={{ color: "#e6edf3" }}>Discord</div>
                <div className="muted" style={{ fontSize: 11 }}>{st.discord ? "habilitado no config" : "desabilitado"}</div>
              </div>
              <span className={"tag" + (st.discord && st.running ? " green" : "")}>
                {st.discord && st.running ? "● online" : "○ offline"}
              </span>
            </div>

            <div className="metric-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)", marginTop: 12 }}>
              <div className="metric"><div className="lbl">processo</div><div className="val" style={{ color: st.running ? "var(--green)" : "var(--text-4)" }}>{st.running ? "ativo" : "parado"}</div></div>
              <div className="metric"><div className="lbl">PID</div><div className="val">{st.pid ?? "—"}</div></div>
              <div className="metric"><div className="lbl">uptime</div><div className="val">{uptime(st.uptime_s)}</div></div>
            </div>

            {msg && <div className="card" style={{ marginTop: 12, color: "var(--text-2)" }}>{msg}</div>}
          </>
        )}
      </div>
    </div>
  );
}
