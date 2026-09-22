# Agno: levantamento arquitetural para o Bauer

Data da consulta: 2026-09-22. Fontes: documentação oficial do Agno, incluindo
o índice [llms.txt](https://docs.agno.com/llms.txt), os conceitos do
[AgentOS](https://docs.agno.com/agent-os/introduction), documentação do SDK e
os exemplos de [tools](https://docs.agno.com/examples/tools/overview).

## Escopo consultado

O índice oficial e as áreas centrais de AgentOS, SDK de agentes, teams,
workflows, ferramentas, input/output, memória, storage, observabilidade,
segurança, API, MCP, interfaces, avaliações e scheduling foram consultados.
O arquivo consolidado `llms-full.txt` contém aproximadamente 19 MB e 1,66
milhão de palavras entre guias, exemplos de integrações e referências de
provedores. Este documento registra a análise das áreas que afetam a arquitetura
do Bauer; não afirma uma leitura linear de cada exemplo de cada provedor.

## Modelo do produto Agno

Agno tem três camadas distintas: SDK para construir agentes, times e workflows;
AgentOS para servir componentes por FastAPI; Control Plane para gerir e
monitorar runtimes. AgentOS agrega API, SSE, MCP, sessões, memória, scheduling,
aprovações, traces, evals e autorização. Isso é uma plataforma completa, não
somente uma biblioteca de toolkits. Ver [introdução ao AgentOS](https://docs.agno.com/agent-os/introduction)
e [runtime](https://docs.agno.com/features/runtime).

No Bauer, substituir o servidor atual pelo AgentOS duplicaria runtime, API,
persistência e autorização. A integração atual usa o SDK como executor atrás do
Kernel; essa posição preserva policy, approval, orçamento, ciclo de run e gates
do Bauer. A recomendação é aproveitar capacidades do SDK e interoperar em
fronteiras explícitas, sem deixar que um segundo runtime finalize os runs por
fora do Kernel.

## Agentes, times e workflows

Um agente constrói contexto, chama modelo e tools, processa o resultado e pode
pausar, falhar ou ser cancelado. Eventos em streaming podem incluir tools,
memória e ciclo de vida. Teams encaminham eventos de membros por padrão; o
stream completo exige `stream_events=True`. Workflows cobrem sequências,
ramificações, paralelismo, loops e interação humana em etapas. Fontes: [agents](https://docs.agno.com/agents/overview),
[stream de agentes](https://docs.agno.com/agents/running-agents), [teams](https://docs.agno.com/teams/running-teams)
e [workflows](https://docs.agno.com/workflows/overview).

O adapter Bauer consumia apenas `content` e `tools`, descartando o contexto de
execução dos membros. A sprint 17 usa `stream_events=True`, converte IDs de
agente/time/run em eventos do Bauer, não duplica a resposta final com texto dos
membros e persiste a atividade pelo EventBus do Kernel.

## Catálogo de ferramentas

O catálogo oficial apresenta mais de 100 integrações e exemplos independentes,
incluindo browser, GitHub, MCP, Python, Docker, bases de dados, busca, canais,
documentos, voz, mídia e serviços cloud. Não significa que todas as ferramentas
venham no pacote base nem que sejam seguras para todos os agentes. Os exemplos
instalam dependências e credenciais por integração; os objetos podem trazer
operações de leitura e escrita no mesmo toolkit. Agno permite restringir funções
por `enable_*`, listas de inclusão/exclusão e tool factories por sessão/papel.
Ver o [catálogo oficial](https://docs.agno.com/examples/tools/overview) e [construção de agentes](https://docs.agno.com/agents/building-agents).

Hoje o adapter mapeia strings de tool para sete operações do ToolRouter Bauer:
`read_file`, `write_file`, `list_dir`, `search_text`, `run_command`,
`web_search` e `memory`. Tools presentes no catálogo Bauer, como navegação
Playwright, fetch web, MCP e outras operações, não são automaticamente
materializadas pelo adapter Agno. Isso deixa capacidade aproveitável sem
integração direta.

Ordem recomendada:

1. Mapear primeiro tools existentes do Bauer, incluindo `web_fetch`, browser e
   leitura de contexto, respeitando o allowlist e o ToolRouter.
2. Criar adaptadores Agno opcionais para GitHub e MCP em modo leitura; MCP deve
   usar servidores explicitamente configurados por instalação.
3. Disponibilizar calculadora, CSV/Pandas e visualização para Bauer Data; SQL
   começa read-only com credencial dedicada.
4. Acrescentar Docker/AWS/EKS inicialmente em inventário/leitura. Escritas e
   operações de infraestrutura passam por policy e aprovação.
5. Integrar Slack, e-mail, Jira/Linear e outros canais de escrita apenas com
   scopes explícitos e aprovação antes de efeitos externos.

Não registrar argumentos ou resultados de tools no painel. A telemetria deve
identificar ferramenta, agente, run, horário e estado. Isso melhora
rastreabilidade sem transformar EventBus ou logs em uma cópia de dados de
usuário.

## Traces e privacidade

AgentOS oferece tracing OpenTelemetry de modelos, tools, times e workflows e
Control Plane com árvore de traces. Porém, traces podem conter prompts,
argumentos e respostas, e têm volume, retenção e controle de acesso próprios.
No Bauer, usar eventos de ciclo de vida redigidos e sem conteúdo é um ponto de
partida mais seguro. Se tracing Agno for habilitado futuramente, ele precisa de
opt-in, armazenamento separado ou retenção definida, controle de acesso e
decisão explícita sobre dados sensíveis. Ver [observabilidade Agno](https://docs.agno.com/features/observability).

## Memória, sessões e persistência

Agno usa interface de database compartilhada por sessões, runs, memórias,
knowledge, traces, aprovações, métricas e scheduling, com diferenças de
capacidade entre backends. Knowledge normalmente combina metadados e vector
store. Isso não elimina a separação já existente no Bauer entre histórico de
sessão, memória Markdown automática e memória Runtime JSONL manual; migrar sem
unificar contratos criaria mais uma fonte divergente. Ver [storage](https://docs.agno.com/features/storage),
[memória](https://docs.agno.com/memory/overview) e [database](https://docs.agno.com/database/overview).

## Segurança e operação

AgentOS oferece JWT, scopes, isolamento por usuário, service accounts e cópia
por requisição de componentes em endpoints centrais. Parte dos objetos continua
compartilhada por referência, então tools customizadas precisam ser seguras em
concorrência. Em várias réplicas, execução durável, eventos/cancelamento
compartilhados e transporte MCP stateless precisam ser tratados separadamente.
No Bauer, essas capacidades devem ser comparadas com autenticação, isolamento,
policies, approval, scheduler e controles atuais antes de qualquer migração.
Fontes: [security & auth](https://docs.agno.com/features/security-and-auth),
[API](https://docs.agno.com/features/api), [MCP server](https://docs.agno.com/features/mcp-server)
e [scheduling](https://docs.agno.com/features/scheduling).

Agno também oferece evals de acurácia, juiz, uso esperado de tools,
performance e hooks pós-run. Esse mecanismo pode complementar o harness do
Bauer em cenários específicos de comportamento Agno; não deve substituir gates
do Kernel ou a suíte hermética existente. Ver [evaluation](https://docs.agno.com/features/evaluation).

## Prioridades para o Bauer

1. Completar observabilidade de membros Agno no EventBus e UI (sprint 17).
2. Expandir o adapter para tools Bauer sob policy, começando por leitura,
   browser e web; tornar dependências opcionais.
3. Adicionar integrações Agno selecionadas — GitHub, MCP, dados — como
   capabilities configuráveis por agente, sem habilitar todos os toolkits.
4. Avaliar workflows Agno apenas para processos com etapas determinísticas ou
   revisão humana; manter o Kernel como dono de autorização e estado final.
5. Comparar evals Agno com o harness atual e adicionar casos de regressão de
   delegação, tool use, privacidade e concorrência.
6. Considerar tracing completo ou AgentOS somente após requisito claro de
   escala/Control Plane e desenho de governança, privacidade e persistência.
