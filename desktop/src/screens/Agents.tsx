import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Agent {
  id?: string;
  name: string;
  description: string;
  tools?: string[];
  capabilities?: string[];
  provider?: string;
  model?: string;
  source?: string;
}

interface AgentActivity {
  agent_id: string;
  status: "idle" | "running" | "failed";
  run_count: number;
  last_used_at?: string | null;
  current_run_id?: string | null;
  team_id?: string | null;
  last_event?: string | null;
  last_tool?: string | null;
}

interface Capability {
  id: string;
  name: string;
  category: string;
  description: string;
  actions: string[];
  risk: string;
  source: string;
  state: string;
  installed: boolean;
  package?: string | null;
}

function lastUsed(value?: string | null): string {
  if (!value) return "Nunca utilizado";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Último uso desconhecido" : `Último uso: ${date.toLocaleString()}`;
}

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activity, setActivity] = useState<Record<string, AgentActivity>>({});
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api.get<{ agents: Agent[] }>("/api/agents").then((r) => setAgents(r.agents)).catch(() => {});
    api.get<{ capabilities: Capability[] }>("/api/agno/catalog")
      .then((r) => setCapabilities(r.capabilities || [])).catch(() => {});
    const loadActivity = () => api.get<{ agents: Record<string, AgentActivity> }>("/api/obs/agent-activity")
      .then((r) => setActivity(r.agents || {})).catch(() => {});
    loadActivity();
    const timer = window.setInterval(loadActivity, 2500);
    return () => window.clearInterval(timer);
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
        {filtered.length === 0 ? <div className="empty">Nenhum agent encontrado.</div> : filtered.map((agent) => {
          const status = activity[agent.id || ""] || activity[agent.name] || { status: "idle", run_count: 0 };
          return <div className="list-item" key={`${agent.source}/${agent.id || agent.name}`} style={{ cursor: "default", alignItems: "flex-start" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="row" style={{ marginBottom: 4 }}>
                <span className="mono" style={{ color: "#e6edf3" }}>{agent.name}</span>
                <span className="tag">{agent.source || "local"}</span>
                <span className={"tag" + (status.status === "running" ? " accent" : status.status === "failed" ? " red" : " green")}>
                  {status.status === "running" ? "trabalhando" : status.status === "failed" ? "falhou" : "ocioso"}
                </span>
                {(agent.provider || agent.model) && <span className="tag accent">{agent.provider || "provider"} / {agent.model || "default"}</span>}
              </div>
              <div className="muted" style={{ marginBottom: 8 }}>{agent.description}</div>
              <div className="row" style={{ flexWrap: "wrap" }}>{(agent.capabilities || agent.tools || []).slice(0, 10).map((item) => <span className="tag" key={item}>{item}</span>)}</div>
              <div className="muted" style={{ marginTop: 8, fontSize: 11 }}>
                {status.run_count} execuções · {lastUsed(status.last_used_at)}
                {status.current_run_id && ` · run ${status.current_run_id.slice(0, 18)}`}
                {status.team_id && ` · time ${status.team_id}`}
                {status.last_tool && ` · ferramenta: ${status.last_tool}`}
              </div>
            </div>
          </div>;
        })}
        <div style={{ marginTop: 24 }}>
          <div className="section-label">Catálogo de capabilities Agno ({capabilities.length})</div>
          <div className="muted" style={{ marginBottom: 10 }}>
            A disponibilidade não concede acesso: cada agente recebe somente as ferramentas declaradas em sua configuração.
          </div>
          {capabilities.map((capability) => {
            const simpleId = capability.id.replace(/^bauer\./, "");
            const enabledAgents = agents
              .filter((agent) => (agent.tools || agent.capabilities || []).some((tool) => tool === capability.id || tool === simpleId))
              .map((agent) => agent.name);
            const stateLabel = capability.state === "adapter_required"
              ? "adapter necessário"
              : capability.state === "dependency_missing"
                ? "dependência ausente"
                : capability.state === "needs_configuration"
                  ? "precisa configurar"
                  : enabledAgents.length ? "em uso" : "disponível · desativada";
            return <div className="list-item" key={capability.id} style={{ cursor: "default", alignItems: "flex-start" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row" style={{ marginBottom: 4 }}>
                  <span className="mono" style={{ color: "#e6edf3" }}>{capability.name}</span>
                  <span className="tag">{capability.category}</span>
                  <span className={"tag" + (enabledAgents.length ? " green" : "")}>{stateLabel}</span>
                  <span className="tag">risco: {capability.risk}</span>
                </div>
                <div className="muted">{capability.description}</div>
                <div className="muted" style={{ marginTop: 5, fontSize: 11 }}>
                  {capability.id} · ações: {capability.actions.join(", ") || "a classificar"}
                  {capability.state === "dependency_missing" && capability.package && ` · dependência: ${capability.package}`}
                  {enabledAgents.length > 0 && ` · agentes: ${enabledAgents.join(", ")}`}
                </div>
              </div>
            </div>;
          })}
        </div>
      </div>
    </div>
  );
}
