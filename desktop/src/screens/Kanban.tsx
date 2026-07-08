import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Card { id: string; title: string; status: string; priority: string; assignee: string; }
interface Board { columns: Record<string, Card[]>; total: number; }

const ORDER = ["TODO", "READY", "IN_PROGRESS", "BLOCKED", "DONE", "FAILED"];
const COLORS: Record<string, string> = {
  TODO: "var(--text-3)", READY: "var(--accent)", IN_PROGRESS: "var(--amber)",
  BLOCKED: "var(--red)", DONE: "var(--green)", FAILED: "var(--red)",
};

export default function Kanban() {
  const [board, setBoard] = useState<Board>({ columns: {}, total: 0 });

  async function load() {
    try { setBoard(await api.get<Board>("/api/kanban")); } catch { /* ignore */ }
  }
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);

  const cols = Object.keys(board.columns).sort(
    (a, b) => (ORDER.indexOf(a) + 99) % 100 - ((ORDER.indexOf(b) + 99) % 100)
  );

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-layout-kanban head-icon" />
        <span className="title">Kanban</span>
        <span className="sub">{board.total} tarefas</span>
        <div className="spacer" />
        <button className="btn" onClick={load}><i className="ti ti-refresh" /> Atualizar</button>
      </div>
      <div className="content">
        {board.total === 0 ? (
          <div className="empty">Nenhuma tarefa no workspace ativo.</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.max(cols.length, 1)}, minmax(180px, 1fr))`, gap: 10 }}>
            {cols.map((col) => (
              <div key={col}>
                <div className="row" style={{ marginBottom: 8, color: COLORS[col] || "var(--text-2)" }}>
                  <span style={{ fontWeight: 500, fontSize: 12 }}>{col}</span>
                  <span className="muted">{board.columns[col].length}</span>
                </div>
                {board.columns[col].map((c) => (
                  <div className="card" key={c.id} style={{ marginBottom: 6, padding: 8 }}>
                    <div style={{ fontSize: 12, color: "var(--text)", marginBottom: 4 }}>{c.title}</div>
                    <div className="row" style={{ gap: 6 }}>
                      <span className="muted mono" style={{ fontSize: 11 }}>#{c.id}</span>
                      {c.assignee && <span className="tag" style={{ fontSize: 11 }}>{c.assignee}</span>}
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
