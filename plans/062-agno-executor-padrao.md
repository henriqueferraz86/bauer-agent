# Plano 062 — Agno como motor de execução padrão do turno

**Status:** DONE. Código de todas as 6 sprints (35-40) entregue, testado e
mergeado — todo canal (CLI, web, OpenAI-compat, Telegram) roda via Agno,
com `bauer_native` preservado como fallback real (ADR
`plans/adr/001-bauer-native-fallback.md`). **Telegram em produção na
Beelink trocado para Agno em 2026-11-04** — o STOP do sprint 40 (tempo
real de estabilidade antes do flip) foi conscientemente sobreposto pelo
operador, não contornado silenciosamente; decisão explícita, registrada
aqui. Dois achados reais de bugs pré-existentes foram corrigidos ao longo
do caminho (aprovação assíncrona e persistência de histórico em `/chat` e
`/v1/chat/completions`, sprints 38/39); um achado real (custo de LLM
perdido em cancelamentos do `AgnoRuntimeAdapter`) não tem correção limpa
possível sem monkeypatch frágil do SDK — mitigado com um aviso visível em
vez de uma correção que não existe (ver seção do sprint 40, "Mitigação
entregue em 2026-11-04"). Detalhes em cada seção de sprint abaixo e em
`specs/sprint-{35..40}-*/`. Suíte completa e Ruff bloqueante/estilo verdes
em toda sprint, confirmados de forma
independente (implementação + revisão cruzada) desde o sprint 37.

**Prioridade:** P1

**Esforço total estimado:** XXL, dividido em sprints independentes

**Dependência inicial:** nenhuma bloqueante — pode avançar em paralelo ao
plano 057, mas se beneficia dele para o catálogo de ferramentas.
**Referências:** achados da sessão de 2026-10-22 (ver "Evidência e baseline");
[documentação oficial do Agno](https://docs.agno.com/llms.txt); plano 057
(`057-agno-capability-expansion.md`), que trata do catálogo de capacidades,
não do motor de execução — os dois são complementares e não se sobrepõem.

## Problema e objetivo

O operador quer que o Agno seja o executor de verdade para **todo** turno —
CLI, web, OpenAI-compat e Telegram — em vez de coexistir como uma opção que
hoje nunca é de fato selecionada em tempo de execução. Hoje `config.yaml`
já declara `runtime.default_adapter: "agno"` e o comentário do schema diz
"Agno is the governed default" — mas isso é um objetivo documentado que o
código não cumpre: nenhum caminho de chat real o consulta.

Esta sessão (plano 061, sprint 34) deixou pronto um mecanismo robusto de
cancelamento cooperativo, orçamento e custódia do Kernel para o caminho
`bauer_native`. Este plano existe para levar essas mesmas garantias para o
caminho Agno, de forma incremental e sem regressão, até que Agno seja o
executor padrão de verdade — não só no nome do adapter guardado no Run.

## Evidência e baseline

Verificado em 2026-10-22, checkout no commit `3d4d1c7` (branch `master`):

- `bauer/core/kernel/kernel.py::_open_run` só consulta `self.adapter_factory`
  (que resolveria `runtime_adapter` para `AgnoRuntimeAdapter` via
  `get_runtime_adapter`) quando `executor is None` na chamada a
  `kernel.execute()`/`kernel.stream()`. Em **todo** lugar que processa uma
  mensagem de chat real, um `executor` explícito já é passado:
  - `bauer/channel_base.py:969` (`run_governed(kernel, _executar, ...,
    runtime_adapter="bauer_native")`) — Telegram/canais.
  - `bauer/server_chat.py:194` (`deps.kernel.execute(..., executor=executor)`)
    — chat web.
  - `bauer/server_openai.py:216` (`continue_governed(deps.kernel, run.id,
    execute_v1)`) — endpoint OpenAI-compat.
  - `bauer/agent.py:3913` (`kernel.execute(_KReq(...), executor=_turn_executor)`)
    — REPL da CLI.
  - `bauer/server.py:2576` (`continue_governed(_kernel, run.id, _rodar_rodadas)`).
  - Em todos esses pontos, o valor `runtime_adapter="bauer_native"` passado
    (quando passado) é **só metadado** gravado no Run — não seleciona o
    executor. Trocar essa string por `"agno"` sem o resto deste plano
    mentiria no label do Run (exatamente o que o `ARCHITECTURE.md` do
    sprint 34 instruiu a não fazer: "não anunciar cobertura de um
    AgnoAdapter que não está conectado a esse canal").
  - O único consumidor real de `runtime.default_adapter` hoje é
    `bauer/core/runtime/scheduler.py:407` (tarefas autônomas/agendadas) e o
    `bauer/agentos_app.py` (serviço AgentOS separado, porta 7777, que não
    passa pelo `kernel.execute()` dos canais de chat).

  **Correção de 2026-10-29 (achado ao vivo na Beelink, verificado dentro do
  container `bauer-agent` em produção — isto desatualiza a frase anterior
  "nenhum caminho de chat real o consulta" para o caso específico abaixo):**
  existe um **segundo e independente** mecanismo de seleção de executor,
  `_runtime_mode()` (`bauer/server.py:773`), que **não** é
  `runtime.default_adapter` — é um toggle próprio persistido em
  `{runtime_root}/runtime/serve-runtime-mode.json` e exposto por
  `GET/POST /api/runtime/mode` (`bauer/desktop_api.py:1556-1564`), editável
  pelo dropdown "EXECUTOR GLOBAL DO SERVER" do painel Runtime
  (`desktop/src/screens/Runtime.tsx`). O endpoint `/stream` em
  `bauer/server.py` — que é o endpoint que o **chat web do Desktop de fato
  chama** em todo turno (`desktop/src/chatStream.ts:94`, não
  `server_chat.py`'s `/chat`) — consulta esse toggle diretamente no worker
  da run: `if _runtime_mode() == "agno": _run_agno_turn(...) else:
  run_one_turn_with_fallback(...)` (`bauer/server.py:1864`). `_run_agno_turn`
  (`bauer/server.py:799`) instancia o mesmo `AgnoRuntimeAdapter` citado
  abaixo diretamente, fora do `kernel.execute(executor=...)` que o resto
  desta seção analisa.

  Na Beelink, em 2026-10-29, este toggle estava em `"agno"` (confirmado via
  `docker exec bauer-agent cat /app/memory/runtime/serve-runtime-mode.json`)
  enquanto `config.yaml`'s `runtime.default_adapter` estava `bauer_native`
  — os dois valores divergiam, e o painel mostra os dois lado a lado como
  se fossem a mesma coisa (card "adapter ativo" = `default_adapter`;
  dropdown = `runtime_mode`), o que é enganoso. **Consequência prática: o
  chat web real já roda no Agno hoje, em produção, sem a paridade de
  cancelamento que o sprint 35 abaixo ainda vai entregar** — o risco que
  este plano foi escrito para evitar já está acontecendo no caminho mais
  usado. O operador foi consultado em 2026-10-29 e optou por manter o
  Agno ativo nesse toggle, aceitando o risco conscientemente até o sprint
  35 fechar a lacuna — não revertido.
  Implicação para a sequência de sprints: o sprint 35 (checkpoints de
  cancelamento no `AgnoRuntimeAdapter`) deve ser tratado como prioridade
  imediata, não como item de fila — ele fecha uma exposição já ativa, não
  um risco futuro. O sprint 38 ("migrar o caminho web") também precisa
  revisitar `_run_agno_turn`/`/stream` especificamente, não só
  `server_chat.py`, já que é esse código (não o padrão `executor=None` via
  Kernel) que de fato atende o chat web hoje.
- `bauer/core/runtime/adapters/agno_adapter.py` (1193 linhas): zero
  ocorrências de `checkpoint_execution`, `ExecutionControl`,
  `current_control` ou `ProviderRetryInterrupted`. O laço real de
  LLM/tool-call roda dentro de `agent.run(...)`/`team.run(...)` — código da
  biblioteca `agno`, fora do nosso controle direto. Os únicos pontos que o
  adapter expõe ao nosso código são os eventos gerados pelo generator em
  `stream_agent`/`stream_team` (linhas ~266–392) e o wrapper de tools
  (`_bauer_tool_callable`/`_build_tool_router`, linhas ~944–1092) que já
  encaminha chamadas de ferramenta para o `ToolRouter` do Bauer — esse
  segundo ponto já herda os checkpoints do `ToolRouter` **se** o
  `ExecutionControl` da run estiver ativo via `ContextVar` na thread/task
  onde o Agno executa o callable (precisa verificação, não confirmado nesta
  sessão).
- O caminho `bauer_native` (`_turno_cru`/`agent_native_turn.py`/
  `agent_response.py`/`context_manager.py`/`openai_client.py`/
  `chatgpt_backend.py`/`auxiliary_client.py`/`llm_approval.py`/
  `tool_router.py`) tem, hoje, checkpoints cooperativos em toda fronteira de
  LLM/retry/aprovação/compressão/subprocesso (sprint 34), streaming via
  `delta_stream`, contabilização de custo via `cost_sink`/`TurnMetrics`,
  gates de policy/aprovação via `ToolRouter`/`ApprovalManager`, e
  compressão de contexto via `context_manager.py`. Nenhuma dessas garantias
  foi verificada como equivalente no caminho Agno.

## Escopo e decisões

- Este plano **não** substitui o Kernel, o policy engine, o EventBus, o
  orçamento ou o ApprovalManager — mantém exatamente o invariante do plano
  057: "nenhum adapter declara um run concluído fora da custódia do
  Kernel". Agno passa a ser o **executor** que o Kernel despacha sob as
  mesmas regras que `bauer_native` segue hoje; não é uma substituição da
  governança.
- Migração é por **caminho de chat**, um de cada vez, na ordem do mais
  controlado para o mais exposto: CLI → web → OpenAI-compat → Telegram. O
  Telegram vem por último porque foi o caminho recém-testado no plano 061
  (sprint 34) e tem usuário real na Beelink; não pode regredir cancelamento.
- Cada caminho migrado mantém `bauer_native` disponível como fallback
  explícito por config (`runtime.default_adapter` por canal/override), não
  apaga o código do caminho nativo até o Agno provar paridade medida.
- Reaproveitar a infraestrutura de checkpoint (`bauer/execution_control.py`,
  `checkpoint_execution`, `controlled_popen`/`run_process`) — não criar um
  segundo mecanismo de cancelamento paralelo para o Agno.
- O catálogo de ferramentas (quais toolkits Agno ficam disponíveis) é escopo
  do plano 057, não deste. Este plano assume que as tools já expostas hoje
  ao `ToolRouter` continuam sendo o conjunto disponível; não amplia
  catálogo.

## Fora de escopo

- Migrar o armazenamento de memória/sessão para o database nativo do Agno
  (mesma decisão adiada do plano 057 — exige ADR própria).
- Expandir o catálogo de toolkits/integrações Agno (plano 057).
- Desligar ou remover o `bauer_native` como código — ele continua existindo
  como fallback até este plano concluir e uma decisão explícita posterior
  decidir aposentá-lo.
- Mudar o AgentOS (porta 7777) — ele já usa Agno diretamente e não depende
  deste plano.

## Invariantes de segurança e compatibilidade

Os mesmos oito invariantes do plano 057 se aplicam integralmente a este
plano (default deny, governança única pelo Kernel, telemetria mínima
redigida, sem efeito colateral implícito, etc. — ver `057-agno-capability-
expansion.md` para o texto completo). Adicionam-se, específicos deste
plano:

1. **Paridade de cancelamento:** um `/cancelar` (ou interrupção equivalente
   em qualquer canal) durante um turno executado via Agno bloqueia novo
   LLM/tool call com a mesma garantia que o caminho `bauer_native` oferece
   hoje — nunca "sinal ignorado silenciosamente".
2. **Paridade de custódia de custo:** custo e métricas de um turno Agno
   chegam ao Kernel mesmo após timeout de transporte ou cancelamento, igual
   ao que o plano 061 validou para `bauer_native`.
3. **Sem downgrade de aprovação:** chamadas de ferramenta com efeito, vindas
   de um agente Agno, passam pelo mesmo `ApprovalManager`/policy que
   `bauer_native` — nunca um caminho de aprovação próprio e paralelo do
   Agno.
4. **Rollback por canal:** cada canal migrado pode voltar para
   `bauer_native` por config sem código novo, até este plano declarar
   migração definitiva.

## Sequência de sprints

Cada sprint segue o skill `spec-driven-project-setup`: `SPEC.md`,
`ARCHITECTURE.md` e `BACKLOG.md` em `specs/sprint-XX-*/`, branch
`codex/sprint-XX-*`, gates completos do `AGENTS.md` antes de avançar.

### Sprint 35 — Checkpoints de cancelamento no AgnoRuntimeAdapter — DONE (2026-10-29)

**Esforço:** L. **Dependência:** nenhuma.

**Entregue:** `specs/sprint-35-agno-cancellation/` (SPEC/ARCHITECTURE/BACKLOG
completos); checkpoints adicionados em `stream_agent`/`stream_team`;
cancelamento mapeado para `RunCancelledException` preservando `run.cancelled`
em Agent e Team; `tests/test_agno_runtime_adapter.py` (34 testes) cobre
cancelamento entre events, durante tool call e durante espera de aprovação
simulada. Suíte hermética completa e Ruff bloqueante verdes. `arun()` e
execução concorrente de membros ficam explicitamente fora da garantia
(fronteira síncrona é a única inspecionada/coberta). Paridade de custo/uso
(`TurnMetrics`) fica para a sprint 36, conforme já previsto no critério de
aceite abaixo.

- **Spike primeiro (1 dia, custo baixo antes de comprometer a sprint
  inteira):** confirmar isoladamente se `copy_context()`/`ContextVar`
  atravessa a fronteira de thread/task onde `agent.run()`/`team.run()` do
  Agno realmente executa. Esta é a incerteza que já aparece como STOP desta
  sprint — vale confirmar com um teste mínimo antes de investir o resto do
  esforço L, não só ao final.
- Instrumentar `stream_agent`/`stream_team` para chamar
  `checkpoint_execution()` a cada evento emitido pelo generator do Agno,
  antes de processar/propagar o próximo — não interrompe uma chamada de
  LLM/tool já em voo (mesma limitação documentada do `bauer_native`: não
  promete encerrar HTTP bloqueado), mas impede o avanço para o próximo
  passo após sinal de parada.
- Verificar e, se preciso, propagar explicitamente o `ExecutionControl`
  ativo (via `copy_context()`/`ContextVar`) para a thread/task onde
  `agent.run(...)` roda, de forma que `_bauer_tool_callable` (que já
  encaminha para o `ToolRouter`) observe o mesmo `ExecutionControl` da run.
- Garantir que a interrupção por `ProviderRetryInterrupted` propagada pelo
  `ToolRouter` dentro de uma tool call do Agno não vire uma resposta de
  ferramenta normal que o Agno tentaria continuar — mapear para o mesmo
  encerramento terminal que `bauer_native` usa.
- Testes de concorrência: cancelar durante stream de evento Agno, durante
  tool call Agno-para-ToolRouter, e durante espera de aprovação.

**Aceite:** cancelar um run Agno em qualquer um dos três pontos acima produz
o mesmo resultado observável de controle que `bauer_native` já garante:
nenhum novo LLM/tool após o sinal e estado terminal `cancelled` preservado.
O Kernel continua acumulando métricas que o executor reportar, mas o Agno
ainda não conecta seu uso/custo ao contrato `TurnMetrics` nesta sprint; essa
paridade é critério obrigatório da sprint 36, antes de migrar qualquer canal.
Sprint 35 não autoriza migração de canal por si só.

**STOP:** parar se o SDK do Agno não expuser um ponto de interrupção seguro
entre eventos do generator, ou se a propagação do `ExecutionControl` exigir
monkeypatch do código interno do pacote `agno` em vez de uma extensão
suportada pela API pública dele.

**Se o STOP disparar (risco existencial do plano):** este STOP não é um
detalhe de uma sprint isolada — é o teste da premissa de todo o plano 062
("Agno sempre"). Se disparar, as opções são, nesta ordem de preferência:
(a) contribuir o ponto de extensão faltante ao projeto `agno` upstream
antes de prosseguir; (b) restringir o escopo deste plano aos canais onde
cancelamento cooperativo não é crítico (nenhum identificado até agora — CLI,
web, OpenAI-compat e Telegram todos dependem dele) e declarar os demais como
não migráveis até o upstream evoluir; (c) voltar ao operador com a
constatação concreta e a decisão explícita de manter `bauer_native` como
único executor para canais de chat, revertendo a leitura de "Agno sempre"
para "Agno onde o SDK permitir paridade de cancelamento". Não decidir isso
silenciosamente dentro da sprint — é decisão do operador, registrada aqui
quando (se) ocorrer.

### Sprint 36 — Paridade de streaming, custo e contexto

**Esforço:** M-L. **Dependência:** sprint 35.

- Mapear eventos do Agno (`_normalize_agent_event`) para o mesmo protocolo
  de `delta_stream` que os canais já consomem, sem duplicar lógica de
  streaming por canal.
- Conectar o `TurnMetrics`/`cost_sink` existente aos eventos de uso do
  Agno (tokens, custo por chamada) — mesma contabilização que
  `bauer_native` usa hoje, não uma paralela.
- Validar comportamento de compressão de contexto/histórico longo no
  caminho Agno (equivalente ao `context_manager.py`); decidir se o Agno
  reaproveita o compressor do Bauer ou se precisa de um adaptador próprio.
- Harness de comparação: mesmo prompt/mesma tarefa rodando em
  `bauer_native` e em Agno, comparando custo, latência e qualidade de
  resposta — registrar baseline antes de migrar qualquer canal real.
  "Qualidade" precisa de critério concreto antes de começar, não ao
  avaliar os resultados: tokens, custo e latência são métricas duras
  (comparação direta); qualidade de resposta, sem um harness de avaliação
  automatizado já existente no repositório, é **revisão manual pareada**
  (mesma tarefa, duas respostas, humano decide "equivalente/pior/melhor")
  sobre um conjunto fixo e pequeno de tarefas representativas — não um
  score numérico inventado ad-hoc.

**Aceite:** harness de comparação roda para um conjunto de tarefas
representativas e produz métricas lado a lado (tokens/custo/latência
duros; qualidade por revisão manual pareada); divergência de custo não
explicada bloqueia a sprint seguinte.

**Sprint 36 fechada em 2026-10-30.** Harness entregue
(`evals/compare_executors/`, 4 tarefas fixas, reaproveita os mesmos pontos
de entrada do CLI/serve para os dois executores, testado hermeticamente,
não roda na suíte padrão/CI). Rodado de verdade contra o provider real da
Beelink: a primeira rodada expôs um bug do próprio harness (Agno sem
`tools` no `agent_spec`, rodando sem nenhuma ferramenta — por isso a
comparação inicial de custo era inválida, ~65x a favor do Agno só porque
ele não fazia o trabalho); corrigido e re-rodado, o resultado real ficou
com custo/tokens praticamente empatados em 3 das 4 tarefas (~2% de
diferença) e qualidade equivalente por revisão manual pareada — só a
tarefa de geração de código mostrou um gap maior (+53% no Agno), não
investigado a fundo. O operador confirmou "manter Agno como padrão" para a
estratégia de contexto (histórico nativo `num_history_runs=3` do Agno, não
o compressor do `bauer_native`), aceitando que nenhuma das 4 tarefas
testou sessão longa o bastante para medir essa diferença especificamente —
risco residual conhecido, não decidido às cegas.

### Sprint 37 — Migrar o caminho CLI

**Esforço:** M. **Dependência:** sprints 35–36.

- `bauer/agent.py` (REPL): parar de passar `executor=_turn_executor`
  incondicionalmente; com `runtime.cli_default_adapter: agno` (chave própria
  da CLI, independente de `runtime.default_adapter` — correção da revisão
  independente, achado bloqueante nº1: `default_adapter` já tem default
  `"agno"` e é o contrato de outros consumidores; reaproveitá-la ligaria
  Agno na CLI sem opt-in explícito), deixar `executor=None` e permitir que o
  Kernel dispache via `adapter_factory`.
- Manter `_turn_executor`/`bauer_native` como caminho padrão até este
  sprint concluir com testes reais em ambiente de desenvolvimento
  (terminal interativo, não hermético) comprovando paridade de UX (HUD,
  aprovação inline, cancelamento por Ctrl-C).
- Suite de testes herméticos cobrindo o novo branch de decisão
  (`executor=None` vs explícito) sem exercitar o Agno real na suíte
  padrão — usar fake/stub do adapter para o teste automatizado.

**Aceite:** `bauer` CLI com `runtime.cli_default_adapter: agno` funciona de
ponta a ponta (pergunta, tool call, cancelamento, aprovação) em teste manual
registrado; suíte hermética cobre o roteamento sem chamar Agno real;
`bauer_native` continua sendo o default até decisão explícita de trocar.

**STOP:** parar se o HUD/UX interativo da CLI depender de algo que só o
caminho `_turno_cru` fornece (ex.: eventos específicos não mapeáveis pelo
Agno) sem solução identificada.

### Sprint 38 — Migrar o caminho web (chat) — DONE (2026-11-03)

**Esforço:** M. **Dependência:** sprint 37 validado.

**Correção de 2026-11-03 (investigação em `specs/sprint-38-migrate-web/`,
checkout `6b839a1`): o texto original abaixo descrevia um trabalho que já
não reflete o código** — "permitir `executor=None` quando o adapter
configurado for `agno`" é o padrão do sprint 37 (CLI via
`kernel.execute(executor=None)` + `adapter_factory`), mas `/chat`
**nunca** usou esse padrão e não precisava: `run_chat_turn`
(`bauer/server_chat.py:108`, dentro do closure `executor` passado a
`deps.kernel.execute(..., executor=executor)`) já despachava
condicionalmente — `if deps.runtime_mode() == "agno": result =
deps.run_agno_turn(...)` — usando o MESMO toggle bespoke `_runtime_mode()`
(`GET/POST /api/runtime/mode`) que `/stream` já usa (ver a correção de
2026-10-29 na seção "Evidência e baseline" acima). Ou seja: "o Agno roda
de verdade em `/chat`" já estava resolvido antes desta sprint começar, só
que por um mecanismo diferente do planejado — não havia `executor=None`
para migrar.

O trabalho real desta sprint, delimitado por
`specs/sprint-38-migrate-web/SPEC.md`/`ARCHITECTURE.md`, foi então mais
estreito:

- Escrever a cobertura de teste que faltava: `/stream` já tinha um teste
  real (SDK Agno real, sem provider) de aprovação assíncrona via API
  (`test_real_agno_chat_approval_runs_each_command_once`), mas `/chat`
  (POST, síncrono) nunca foi exercitado nesse cenário. Adicionado o helper
  `start_chat(app, session)` e o teste
  `test_real_agno_post_chat_approval_runs_each_command_once` em
  `tests/test_approval_chat_e2e.py`, reaproveitando `environment`/
  `TwoCommands`/`pending` já existentes — cobre caminho feliz (duas tool
  calls aprovadas em sequência, efeitos no workspace, histórico de sessão)
  e um caso de deny (run falha, nenhum efeito).
- Esse teste **revelou dois bugs reais e pontuais, específicos de
  `/chat`** (ambos corrigidos nesta sprint, conforme o STOP só pede parar
  para mudança estrutural maior — não foi o caso):
  1. `run_chat_turn`/`chat_via_kernel` nunca chamavam
     `set_policy_approval_wait(True)` — sem isso,
     `_evaluate_runtime_policy` (`tool_router.py`) levantava
     `ToolError("waiting_approval: ...")` na hora em vez de bloquear a
     requisição HTTP até o operador decidir via
     `POST /api/approvals/{id}/approve`, que é o que `/stream` já faz no
     seu worker (`bauer/server.py` chama `set_policy_approval_wait(True)`
     ali). Corrigido em `bauer/server_chat.py` (nos dois branches, com e
     sem Kernel), ligando/desligando o contextvar ao redor do turno, no
     mesmo padrão de `server.py::_worker`.
  2. O branch Agno de `run_chat_turn` nunca chamava `ctx.add_assistant(...)`
     — diferente do caminho nativo (cujo `run_one_turn` já grava a
     resposta em `ctx.messages`), o adapter Agno mantém seu próprio
     contexto interno e nunca escreve no `ctx` do chamador. Resultado: uma
     sessão `/chat` com `runtime_mode=agno` nunca persistia a resposta do
     assistente no histórico (`/sessions/{id}/messages`) — o próximo
     turno carregaria a conversa sem o que o modelo disse antes. Corrigido
     chamando `ctx.add_assistant(deps.format_response(response))` no
     branch Agno, no mesmo ponto em que `server.py`'s `_worker` já faz
     isso para `/stream`.
- Checado (não implementado, é escopo do sprint 39): `bauer doctor`/painel
  ainda não expõem qual executor está ativo por canal — achado registrado
  em `specs/sprint-38-migrate-web/BACKLOG.md` para o sprint 39 consumir.

**Aceite:** teste real (SDK Agno real, sem mock) comprova que `/chat` com
`runtime_mode: agno` completa o fluxo approve/deny via API corretamente —
mesma garantia que `/stream` já tinha; suíte completa + Ruff
bloqueante/estilo verdes; nenhuma regressão nos testes existentes de
`/stream`/`/chat`/approval.

### Sprint 39 — Migrar OpenAI-compat e consolidar — DONE (2026-11-03)

**Esforço:** M. **Dependência:** sprint 38.

- Mesma mudança em `server_openai.py`.
- Expor no `bauer doctor` e no painel qual executor está ativo por canal/
  config, sem assumir Agno como único caminho possível.
- Decisão documentada (ADR curto): manter `bauer_native` como fallback
  permanente configurável, ou planejar depreciação formal em plano
  separado — não decidir aposentadoria dentro deste plano.

**Aceite:** os três caminhos (CLI, web, OpenAI-compat) suportam
`runtime.default_adapter: agno` com paridade medida contra o baseline do
sprint 36; `bauer_native` segue disponível e testado como fallback.

**Entregue:** diferente de `/chat`/`/stream` (já despachavam para Agno
antes dos sprints 36/38 existirem), `/v1/chat/completions` nunca teve
nenhum dispatch — trabalho genuíno aqui, não só teste faltando.
`OpenAIRouteDependencies` ganhou `runtime_mode`/`run_agno_turn`; `execute_v1`
despacha condicionalmente, replicando o padrão corrigido do sprint 38
(commit `770fe08`). Aplicados proativamente, sem precisar que um teste
revelasse primeiro: `set_policy_approval_wait` (bug pré-existente,
independente do Agno — tool pedindo aprovação levantava erro na hora em
vez de esperar) e `ctx.add_assistant` no branch Agno (resposta não
persistia no histórico). Teste real de aprovação assíncrona via esse
endpoint adicionado e validado por revisão independente que reverteu cada
fix isoladamente e confirmou a falha esperada nos dois casos. `bauer
doctor` agora mostra o executor web ativo (best-effort, lê o mesmo JSON
que `server.py` persiste). ADR (`plans/adr/001-bauer-native-fallback.md`):
manter `bauer_native` como fallback permanente, sem depreciação planejada
— consolidação do que já era verdade na prática desde o sprint 35, não
uma decisão nova. Suíte completa (8487 testes) + Ruff bloqueante/estilo
verdes, confirmados de forma independente duas vezes (implementação +
revisão).

### Sprint 40 — Migrar o caminho Telegram (canal mais exposto)

**Esforço:** L. **Dependência:** sprints 35–39 concluídas e estáveis em
produção real por tempo suficiente para confiança (não definir número de
dias aqui — decisão do operador no momento de iniciar a sprint).

- `channel_base.py`: mesmo padrão de `executor=None` condicional, mas só
  depois de validar em produção real na Beelink com `/cancelar`,
  `/status`, filas e timeout de transporte — os mesmos critérios de
  aceite do plano 061 (sprint 34), agora rodados contra o caminho Agno.
- Reexecutar o roteiro de validação do plano 061 (etapa 2) inteiro contra
  o executor Agno antes de trocar o default de produção.

**Aceite:** todos os critérios de aceite de `specs/sprint-34-telegram-
cancellation/SPEC.md` seguem valendo com o executor Agno ativo no canal
Telegram; nenhuma regressão de cancelamento, custódia ou isolamento entre
sessões.

**STOP:** não trocar o default de produção do Telegram para Agno enquanto
qualquer critério do sprint 34 não estiver comprovadamente equivalente.

**Entregue em 2026-11-03 — código pronto e testado, default de produção
NÃO trocado (STOP respeitado).** `runtime.telegram_default_adapter`
(chave própria, default `bauer_native`, independente de
`runtime.default_adapter`/`cli_default_adapter`) controla o branch.
`_turno_agno` nova em `ChannelBase`, resolvida uma vez em `_execute_turn`
e usada nos dois pontos onde `_turno_cru` era chamado — a estrutura
`control.activate()/checkpoint()/close()` do sprint 34 (plano 061) não foi
tocada. A peça de lógica nova: `AgnoRuntimeAdapter.run_agent` sinaliza
cancelamento como resultado estruturado (`status: "cancelled"`), não
exceção — `_turno_agno` traduz de volta para `ProviderRetryInterrupted`
para o `except` existente reconhecer. Verificado ponta a ponta por
implementação + revisão independente, rastreando a string de `reason`
por todos os módulos envolvidos (`ExecutionControl` →
`ProviderRetryInterrupted` → handler de exceção do `AgnoRuntimeAdapter` →
`_turno_agno` → dict de mensagens de `_executar`), com testes reais (não
mockados na fronteira do problema) provando cada salto. Cancelamento,
custódia de Run, isolamento entre operadores/sessões: todos confirmados
equivalentes ao caminho nativo.

**Achado real descoberto (não corrigido aqui, fora do escopo desta
sprint):** testando com o SDK Agno real, confirmou-se que
`AgnoRuntimeAdapter._report_run_cost` só dispara no evento final
`RunCompleted`/`TeamRunCompleted` — uma execução cancelada nunca emite
esse evento, então custo de chamadas de LLM já completadas antes do
cancelamento nunca chega ao `cost_sink` (o motor nativo reporta cada
chamada assim que completa, não tem esse problema). Documentado com teste
de regressão real
(`tests/test_channel_agno_turn.py::test_agno_cancellation_during_tool_loop_loses_partial_llm_cost_confirmed_gap`)
que falhará se uma sprint futura corrigir o adapter — sinal para
atualizá-lo. Pertence ao sprint 35/36 (dono do `AgnoRuntimeAdapter`), não
a este sprint do canal Telegram; recomendado como trabalho futuro, não
decidido nem agendado aqui.

**Mitigação entregue em 2026-11-04** (branch `codex/agno-cost-on-cancellation`):
investigação direta do SDK Agno 3.0.10 instalado (campos de evento,
mecanismo `pre_hooks`/`post_hooks`, atributo `run_response`) confirmou que
**não há correção limpa sem monkeypatch frágil do SDK** — nenhum evento
intermediário carrega métricas, nenhum hook dispara por chamada de LLM,
nenhum estado acumulado é exposto publicamente. O operador, ciente disso,
escolheu tornar a lacuna visível em vez de silenciosa:
`AgnoRuntimeAdapter.stream_agent` agora loga um `warning` (nunca altera
`status`/fluxo do run) quando um run cancelado/com falha teve pelo menos
um evento real do SDK processado (`sdk_events_seen > 0` — sinal escolhido
deliberadamente no lugar de "pelo menos uma tool completada", que fica
vazio justamente no cenário mais comum de cancelamento). Três testes
novos em `tests/test_channel_agno_turn.py`: o warning dispara no
cancelamento durante tool call, dispara também cancelando uma chamada de
LLM NUA (sem tool — fecha uma janela teórica de corrida entre
`checkpoint_execution()` e `yield` em `_checkpointed_events`, identificada
e confirmada inofensiva na prática pela revisão independente), e nunca
dispara num turno que completa normalmente. Revisão cruzada reproduziu
manualmente o cenário de corrida teórica contra o SDK real antes de
confirmar que não se materializa. `AgnoRuntimeAdapter`/
`execution_control.py` não tiveram nenhuma outra mudança de
comportamento — é só observabilidade.

Suíte completa + Ruff bloqueante/estilo verdes, confirmados
independentemente duas vezes.

## Rollout, reversão e compatibilidade

- Cada sprint é reversível por config (`runtime.default_adapter` volta para
  `bauer_native` sem mudança de código).
- Nenhuma sprint apaga o caminho `bauer_native` — ele só deixa de ser o
  único caminho.
- Rollout real (produção na Beelink) só acontece depois de validação manual
  registrada, igual ao padrão já seguido no plano 061 (etapa 6).

## Métricas de sucesso

- Paridade funcional: cancelamento, orçamento, aprovação e streaming
  idênticos (do ponto de vista do usuário) entre `bauer_native` e Agno, nos
  quatro caminhos.
- Paridade de custo/latência: divergência medida e explicada, não apenas
  assumida equivalente.
- Zero regressão nos testes existentes de `bauer_native` durante toda a
  migração — o plano adiciona cobertura, não remove.
- Decisão final documentada sobre o destino do `bauer_native` (mantido como
  fallback permanente ou depreciado em plano futuro).

## Validação obrigatória por sprint

Mesmo fluxo do `AGENTS.md` já seguido nos planos 057/061: `uv sync --frozen
--extra dev`, suíte completa hermética, Ruff bloqueante e de estilo, mypy
bloqueante. Testes que exercitam o Agno real (não hermético) rodam à parte,
documentados como validação manual — nunca na suíte padrão.
