# SPEC — Agentes e tarefas no chat

## Objetivo
Mostrar visualmente qual agente trabalha em qual tarefa no chat, com estados e
atividade sustentados pelo runtime, para turnos simples, delegação e /loop.

## Escopo
Painel abaixo do projeto com tarefa, agente, estado, atividade e agentes delegados.
Snapshot autenticado de sessão via GET /chat/activity?session_id=; primeiro
snapshot SSE internal de atividade; polling2s quando tela visível, reconciliação depois de
reload/navegação/timeout, reset sessão e proteção contra resposta atrasada.
Backend projeta RunManager e EventBus com dados públicos redigidos/limitados;
sem expor argumentos, outputs de tools, prompts system ou configuração secreta.
UI segue tema atual, responsiva e acessível. Preservar chat e contexto existentes.

## Fora de escopo
Inventar agentes/status/progresso; mudar execução, planejamento ou aprovações;
criar scheduler novo, editar tarefa de projeto, ferramentas de observabilidade.

## Skills obrigatórias
spec-driven-project-setup (já aplicada nesta conversa).

## Sub-agents
project_backend: projeção/backend/tests endpoint.
project_frontend: modelos/painel/chat streaming/styles/tests SPA.
project_diagnosis: revisão independente e testes de navegador.
root: specs, SSE servidor, contrato HTTP, integração/gates/entrega.

## Contrato
{session_id: string|null, runs: RunActivity[]}.
RunActivity = {run_id, task, status, agent_id, agent_name, runtime,
started_at, updated_at, agents: AgentActivity[]}.
AgentActivity = {agent_id, name, task, status, activity, tool_name: string|null,
updated_at: string}. Todos campos texto exceto agents/runs. Estados são nomes
runtime (created/planning/policy_check/queued/running/waiting_approval/evaluating/
retrying/paused/completed/failed/cancelled) mais delegated/unknown para membros.
Endpoint devolve ativos mais última execução da sessão, teto5, nenhuma execução
quando session_id vazio. SSE internal com kind=chat.activity e payload devolve mesmo envelope.

## Critérios de aceite
1. Agente/tarefa aparecem durante execução, confirmados por servidor.
2. Agentes delegados com suas tarefas aparecem sem misturar sessões.
3. Tool atual e estados terminal/approval/paused/refeito são reais e claros.
4. Navegar/reload/timeout não marca completed pela conexão nem mantém run velho trabalhando.
5. Nova sessão limpa painel; erros sinalizam informação indisponível.
6. Texto de eventos não entra na resposta; sem argumentos/outputs/segredos.
7. Responsivo390px e desktop; regressões backend/SPA e gates passam.

## Validação
uv sync frozen/dev; regressões projector/auth/SSE/isolation; pytest completo;
Ruff0.17.0 dois gates + mypy; npm test/build; navegador com API controlada e
smoke local sem provider real. Capturas visual desktop/móvel na pasta validation.
