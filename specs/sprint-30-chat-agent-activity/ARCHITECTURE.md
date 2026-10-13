# ARCHITECTURE

server_activity projeta runs/eventos da sessão selecionada em envelope público.
register_observability_routes registra GET /chat/activity com verify_key.
Snapshot inicial SSE activity vai após context, antes da execução/erro de admissão;
frontend consome metadata separado do corpo do assistente. Polling mesmo endpoint
mantém atualizações reais enquanto backend roda, inclusive após fim da conexão.
Panel inicia aguardando snapshot; frontend valida payload, ordena/limita e
invalida consultas antigas em sessão/reset/SSE, ignora polling com tela oculta.
Eventos oficiais task.delegated/agent.started/paused/resumed/finished/completed/
failed e tool.call.* atualizam membros. Não se declara conclusão por texto do LLM
nem por 'done' SSE. Estados sem evidência são unknown. Sem modificar custódia Kernel.

Identidade de card: AgentActivity.agent_id usa data.delegation_id quando existe;
name mantém o agente real. Assim várias chamadas ao mesmo especialista exibem
tarefas distintas. Delegação nativa publica lifecycle após resolução do agente,
sem criar novos runs nem alterar custódia; conclusão acompanha o resultado final,
inclusive após fallback. Agno run_agent encaminha lifecycle/task e tools de
membros explicitamente identificados ao bus do servidor (sem repetir tools da
raiz). TaskCreated/Updated oficiais preservam tarefa/assignee/status, sem result.
Snapshot SSE indisponível é null: frontend sinaliza desconhecido e preserva texto.
