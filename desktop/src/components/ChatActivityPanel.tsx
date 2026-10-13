import {
  AgentActivity, ChatActivityState, RunActivity, activityStatusDisplay,
} from "../chatActivity";

function updatedTime(value: string): string {
  return new Date(value).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function AgentRow({ agent, confirmed, primary = false }: { agent: AgentActivity; confirmed: boolean; primary?: boolean }) {
  const display = activityStatusDisplay(agent.status, confirmed);
  return (
    <div className={"chat-agent-row" + (primary ? " primary" : "")} data-agent-id={agent.agent_id}>
      <i className={"ti " + (primary ? "ti-bolt" : "ti-user")} aria-hidden="true" />
      <div className="chat-agent-info">
        <div className="chat-agent-heading">
          <strong>{agent.name}</strong>
          <span className={`tag ${display.tone}`}>
            {display.working && <i className="ti ti-loader-2 spin" aria-hidden="true" />}{display.label}
          </span>
        </div>
        {agent.task && <div className="chat-agent-task">{agent.task}</div>}
        {confirmed && agent.activity && <div className="chat-agent-current">{agent.activity}</div>}
        {confirmed && agent.tool_name && display.working && (
          <div className="chat-agent-tool"><i className="ti ti-tool" aria-hidden="true" /> Ferramenta: <span className="mono">{agent.tool_name}</span></div>
        )}
        <div className="chat-agent-time">Atualizado {updatedTime(agent.updated_at)}</div>
      </div>
    </div>
  );
}

function RunCard({ run, confirmed }: { run: RunActivity; confirmed: boolean }) {
  const display = activityStatusDisplay(run.status, confirmed);
  const main = run.agents.find((agent) => agent.agent_id === run.agent_id);
  const primary: AgentActivity = main ?? {
    agent_id: run.agent_id, name: run.agent_name, task: "", status: run.status,
    activity: "", tool_name: null, updated_at: run.updated_at,
  };
  const delegated = run.agents.filter((agent) => agent.agent_id !== run.agent_id);
  return (
    <article className="chat-run-card" data-run-id={run.run_id}>
      <div className="chat-run-heading">
        <span className="chat-run-label">Tarefa</span><span className={`tag ${display.tone}`}>{display.label}</span>
      </div>
      <div className="chat-run-task">{run.task || "Tarefa sem descrição"}</div>
      <AgentRow agent={primary.task === run.task ? { ...primary, task: "" } : primary} confirmed={confirmed} primary />
      {delegated.length > 0 && (
        <div className="chat-delegated-agents" aria-label="Agentes delegados">
          <div className="chat-delegated-heading">Agentes delegados · {delegated.length}</div>
          {delegated.map((agent) => <AgentRow key={agent.agent_id} agent={agent} confirmed={confirmed} />)}
        </div>
      )}
    </article>
  );
}

export default function ChatActivityPanel({ state }: { state: ChatActivityState }) {
  const runs = state.snapshot?.runs ?? [];
  const confirmed = state.status === "ready";
  return (
    <section className="chat-activity-panel" aria-label="Atividade dos agentes">
      <div className="chat-activity-header"><i className="ti ti-users-group" aria-hidden="true" /><strong>Atividade dos agentes</strong></div>
      {state.status !== "ready" && (
        <div className="chat-activity-notice" role="status">
          {state.status === "awaiting" ? "Aguardando uma tarefa nesta conversa."
            : state.status === "loading" ? "Consultando atividade dos agentes…"
            : "Atualização indisponível. Estado dos agentes desconhecido; tentando reconectar…"}
        </div>
      )}
      {confirmed && runs.length === 0 && <div className="chat-activity-notice">Nenhuma tarefa em execução.</div>}
      {runs.length > 0 && (
        <div className="chat-activity-runs">
          {runs.map((run) => <RunCard key={run.run_id} run={run} confirmed={confirmed} />)}
        </div>
      )}
    </section>
  );
}
