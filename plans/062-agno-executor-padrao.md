# Plano 062 — Agno como motor de execução padrão do turno

**Status:** IN PROGRESS — sprint 35 (checkpoints de cancelamento no
AgnoRuntimeAdapter) entregue e validado em 2026-10-29: backlog completo,
suíte hermética e Ruff bloqueante verdes; sprints 36-40 ainda não
iniciadas.

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

### Sprint 37 — Migrar o caminho CLI

**Esforço:** M. **Dependência:** sprints 35–36.

- `bauer/agent.py` (REPL): parar de passar `executor=_turn_executor`
  incondicionalmente; com `runtime.default_adapter: agno` (ou uma flag
  explícita `--runtime agno`), deixar `executor=None` e permitir que o
  Kernel dispache via `adapter_factory`.
- Manter `_turn_executor`/`bauer_native` como caminho padrão até este
  sprint concluir com testes reais em ambiente de desenvolvimento
  (terminal interativo, não hermético) comprovando paridade de UX (HUD,
  aprovação inline, cancelamento por Ctrl-C).
- Suite de testes herméticos cobrindo o novo branch de decisão
  (`executor=None` vs explícito) sem exercitar o Agno real na suíte
  padrão — usar fake/stub do adapter para o teste automatizado.

**Aceite:** `bauer` CLI com `runtime.default_adapter: agno` funciona de
ponta a ponta (pergunta, tool call, cancelamento, aprovação) em teste manual
registrado; suíte hermética cobre o roteamento sem chamar Agno real;
`bauer_native` continua sendo o default até decisão explícita de trocar.

**STOP:** parar se o HUD/UX interativo da CLI depender de algo que só o
caminho `_turno_cru` fornece (ex.: eventos específicos não mapeáveis pelo
Agno) sem solução identificada.

### Sprint 38 — Migrar o caminho web (chat)

**Esforço:** M. **Dependência:** sprint 37 validado.

- Mesma mudança de padrão em `server_chat.py`: permitir `executor=None`
  quando o adapter configurado for `agno`, preservando o `executor`
  explícito como fallback.
- Validar fluxo de aprovação assíncrona (approve/deny via API) com um run
  Agno — o plano 061 (etapa 4, futura) depende de aprovação funcionando
  igual em qualquer executor.
- Testes de regressão do Desktop/web chat cobrindo o novo branch.

**Aceite:** chat web com `runtime.default_adapter: agno` preserva streaming
SSE, aprovação e cancelamento observados pelo usuário; suíte herdada do
`test_server_*` continua verde.

### Sprint 39 — Migrar OpenAI-compat e consolidar

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
