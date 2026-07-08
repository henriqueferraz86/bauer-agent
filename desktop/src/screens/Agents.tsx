import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Agent {
  name: string;
  description: string;
  tools?: string[];
  capabilities?: string[];
  provider?: string;
  model?: string;
  source?: string;
}

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api.get<{ agents: Agent[] }>("/api/agents").then((r) => setAgents(r.agents)).catch(() => {});
  }, []);

  const filtered = agents.filter((agent) => `${agent.name} ${agent.description}`.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-users head-icon" />
        <span className="title">Agents</span>
        <span className="sub">{filtered.length}</span>
        <div className="spacer" />
        <input className="in" style={{ width: 240 }} placeholder="Buscar agent" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <div className="content">
        {filtered.length === 0 ? <div className="empty">Nenhum agent encontrado.</div> : filtered.map((agent) => (
          <div className="list-item" key={`${agent.source}/${agent.name}`} style={{ cursor: "default", alignItems: "flex-start" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="row" style={{ marginBottom: 4 }}>
                <span className="mono" style={{ color: "#e6edf3" }}>{agent.name}</span>
                <span className="tag">{agent.source || "local"}</span>
                {(agent.provider || agent.model) && <span className="tag accent">{agent.provider || "provider"} / {agent.model || "default"}</span>}
              </div>
              <div className="muted" style={{ marginBottom: 8 }}>{agent.description}</div>
              <div className="row" style={{ flexWrap: "wrap" }}>{(agent.capabilities || agent.tools || []).slice(0, 10).map((item) => <span className="tag" key={item}>{item}</span>)}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
