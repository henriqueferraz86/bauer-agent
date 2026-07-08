import { useEffect, useState } from "react";
import { api } from "../api/client";

interface AdapterStatus {
  name: string;
  registered: boolean;
  enabled: boolean;
  default: boolean;
  mode: string;
  base_url?: string;
}

interface RuntimeStatus {
  default_adapter: string;
  adapters: AdapterStatus[];
  workers: { id: string; computed_status: string; pid: number; last_seen_at: string }[];
  kill_switch: boolean;
}

export default function Runtime() {
  const [status, setStatus] = useState<RuntimeStatus | null>(null);

  useEffect(() => {
    const load = () => api.get<RuntimeStatus>("/api/runtime/dashboard").then(setStatus).catch(() => {});
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, []);

  const agno = status?.adapters.find((adapter) => adapter.name === "agno");
  const native = status?.adapters.find((adapter) => adapter.name === "bauer_native");

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-server-2 head-icon" />
        <span className="title">Runtime</span>
        <span className="sub">{status?.default_adapter || "-"}</span>
      </div>
      <div className="content">
        {!status ? <div className="empty">Carregando runtime.</div> : (
          <>
            <div className="metric-grid" style={{ marginBottom: 16 }}>
              <div className="metric"><div className="lbl">adapter ativo</div><div className="val" style={{ fontSize: 16 }}>{status.default_adapter}</div></div>
              <div className="metric"><div className="lbl">Agno</div><div className="val" style={{ fontSize: 16 }}>{agno?.enabled ? "enabled" : "off"}</div></div>
              <div className="metric"><div className="lbl">Bauer native</div><div className="val" style={{ fontSize: 16 }}>{native?.registered ? "ready" : "missing"}</div></div>
              <div className="metric"><div className="lbl">kill switch</div><div className="val" style={{ fontSize: 16 }}>{status.kill_switch ? "on" : "off"}</div></div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <div>
                <div className="section-label">Adapters</div>
                {status.adapters.map((adapter) => (
                  <div className="list-item" key={adapter.name} style={{ cursor: "default" }}>
                    <div style={{ flex: 1 }}>
                      <div className="mono" style={{ color: "#e6edf3" }}>{adapter.name}</div>
                      <div className="muted" style={{ fontSize: 11 }}>{adapter.mode}{adapter.base_url ? ` · ${adapter.base_url}` : ""}</div>
                    </div>
                    <span className={"tag" + (adapter.registered ? " green" : "")}>{adapter.registered ? "registered" : "missing"}</span>
                    <span className={"tag" + (adapter.enabled ? " accent" : "")}>{adapter.enabled ? "enabled" : "off"}</span>
                  </div>
                ))}
              </div>
              <div>
                <div className="section-label">Workers</div>
                {status.workers.length === 0 ? <div className="empty">Nenhum worker.</div> : status.workers.map((worker) => (
                  <div className="list-item" key={worker.id} style={{ cursor: "default" }}>
                    <div style={{ flex: 1 }}>
                      <div className="mono" style={{ color: "#e6edf3" }}>{worker.id}</div>
                      <div className="muted" style={{ fontSize: 11 }}>{worker.last_seen_at}</div>
                    </div>
                    <span className={"tag" + (worker.computed_status === "online" ? " green" : "")}>{worker.computed_status}</span>
                    <span className="tag">pid {worker.pid}</span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
