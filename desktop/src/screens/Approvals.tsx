import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Approval {
  id: string;
  operation: string;
  tool_name: string;
  status: string;
  reason: string;
  risk_level: string;
  run_id?: string | null;
  session_id?: string | null;
  payload?: Record<string, unknown>;
}

export default function Approvals() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [status, setStatus] = useState("pending");

  const load = () => {
    api.get<{ approvals: Approval[] }>(`/api/obs/approvals?status=${status}`).then((r) => setApprovals(r.approvals)).catch(() => {});
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, [status]);

  async function decide(id: string, action: "approve" | "deny", scope = "once") {
    await api.post(`/api/approvals/${id}/${action}`, action === "approve" ? { scope } : undefined);
    await load();
  }

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-shield-question head-icon" />
        <span className="title">Approvals</span>
        <div className="spacer" />
        <select className="in" style={{ width: 140 }} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="pending">pending</option>
          <option value="approved">approved</option>
          <option value="denied">denied</option>
          <option value="">all</option>
        </select>
      </div>
      <div className="content">
        {approvals.length === 0 ? <div className="empty">Nenhuma aprovação.</div> : approvals.map((approval) => (
          <div className="list-item" key={approval.id} style={{ cursor: "default", alignItems: "flex-start" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="row" style={{ marginBottom: 4 }}>
                <span className="mono" style={{ color: "#e6edf3" }}>{approval.operation}</span>
                <span className="tag">{approval.tool_name}</span>
                <span className="tag accent">{approval.risk_level}</span>
                <span className="tag">{approval.status}</span>
              </div>
              <div className="muted" style={{ marginBottom: 6 }}>{approval.reason}</div>
              <div className="mono muted" style={{ fontSize: 11 }}>run: {approval.run_id || "-"} · session: {approval.session_id || "-"}</div>
            </div>
            {approval.status === "pending" && (
              <div className="row">
                <button className="btn primary" onClick={() => decide(approval.id, "approve")}><i className="ti ti-check" /> Aprovar</button>
                {approval.operation === "shell.execute" && approval.tool_name === "run_command" && approval.run_id && approval.session_id && (
                  <button className="btn" onClick={() => decide(approval.id, "approve", "always")}>Permitir sempre este comando (allowlist)</button>
                )}
                <button className="btn" onClick={() => decide(approval.id, "deny")}><i className="ti ti-x" /> Negar</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
