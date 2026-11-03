# SPEC — Sprint 40: caminho Telegram (plano 062)

## Enquadramento: o STOP do próprio plano se aplica aqui

`plans/062-agno-executor-padrao.md`, sprint 40, tem um STOP explícito:
"não trocar o default de produção do Telegram para Agno enquanto qualquer
critério do sprint 34 não estiver comprovadamente equivalente" e uma
dependência "sprints 35-39 concluídas e **estáveis em produção real por
tempo suficiente para confiança**". Essa segunda condição não pode ser
satisfeita numa sessão só — exige tempo de calendário com uso real em
produção, que os sprints 35-39 (todos entregues em 2026-10-30/2026-11-03)
ainda não tiveram.

**Esta sprint implementa e testa tudo que dá pra implementar e testar sem
violar essa condição — mas não troca o default de produção.** O operador
decide quando (e se) trocar `runtime.telegram_default_adapter` pra `agno`
na Beelink, depois de tempo de estabilidade — essa decisão fica
explicitamente fora do escopo desta sprint, documentada como pendência,
não tomada aqui.

## Evidência (checkout `f66ae15`, branch `codex/sprint-40-telegram`)

- `bauer/channel_base.py` nunca teve nenhum dispatch pra Agno — zero
  ocorrências de `runtime_mode`/`run_agno_turn`/`_run_agno_turn` no
  arquivo (confirmado por grep). Trabalho genuíno, como o sprint 39, não
  "só teste faltando" como foram 38/36.
- `_execute_turn` → `_executar` (closure interna, `channel_base.py:915`)
  → `run_governed(kernel, _executar, ..., runtime_adapter="bauer_native")`
  (`channel_base.py:969`) — o `runtime_adapter="bauer_native"` aqui é só
  metadado gravado no Run (mesmo padrão documentado em toda a
  "Evidência e baseline" do plano 062); não seleciona o executor.
- Dentro de `_executar`, o turno de verdade roda em
  `self._turno_cru(ctx, key, client, model, text, on_delta, metrics=metrics)`
  (`channel_base.py:941`/`947`), envolvido por
  `with control.activate(): ... control.checkpoint() ... control.close()`
  — **este é o mecanismo de cancelamento cooperativo do plano 061 sprint
  34** (`/cancelar`). `checkpoint_execution()` (chamado dentro de
  `_turno_cru` via `current_control().checkpoint()`, linha ~977, e já
  instrumentado dentro de `AgnoRuntimeAdapter.stream_agent` desde o sprint
  35) lê o MESMO `ExecutionControl` ativo via `ContextVar` — ou seja, a
  propagação de cancelamento para um branch Agno aqui deveria "só
  funcionar" por reaproveitar a mesma infraestrutura, mas **isso precisa
  ser comprovado por teste real, não presumido** (é exatamente o critério
  de aceite desta sprint).
- `_turno_cru` já instala `delta_stream.set_sink(on_delta)` (linha ~992)
  — `AgnoRuntimeAdapter.stream_agent` já emite por esse canal desde o
  sprint 36, então um branch Agno aqui herda streaming de graça, sem
  wiring extra.
- `_executar` já envolve TUDO (inclusive o branch nativo e o branch que
  será Agno) com `with metrics.context(key, _payload.get("run_id")):`
  (`channel_base.py:937`), e `TurnMetrics.context()`
  (`bauer/channel_governance.py:96-103`) já faz
  `cost_token = cost_sink.set(self)` — **custo já está corretamente
  instalado para qualquer coisa que rode dentro desse `with`**, incluindo
  um branch Agno novo. Diferente de `/chat`/`/v1/chat/completions`
  (sprints 38/39), aqui não existe o bug de custo zerado — não precisa
  replicar aquele fix.
- Aprovação assíncrona (`policy_enabled=True`/`ApprovalManager`) **não
  está ligada para o Telegram**, igual ao achado do sprint 37 pro CLI:
  `self._channel_project_router`/`_build_router` (mesma função que o CLI
  usa) nunca passa `policy_enabled=True`. Confirmado por grep: zero
  ocorrências de `policy_enabled` em `channel_base.py`/`telegram_bridge.py`.
  Isso é consistente com o próprio texto do sprint 40 do plano 062, que
  nunca menciona aprovação — só cancelamento, status, filas e timeout de
  transporte. **Não há bug de aprovação a corrigir aqui** (ao contrário
  dos sprints 38/39) — fora de escopo, não é regressão.
- `_run_chat_team` (`channel_base.py:1035`) já constrói um
  `AgnoRuntimeAdapter` dentro desta mesma classe — é o template de como
  resolver `cfg`/`adapter_cfg`/`runtime_root` aqui:
  ```python
  cfg = load_config(self.config_path)
  adapter_cfg = AgnoRuntimeAdapter._adapter_config_from(cfg)
  adapter_cfg.setdefault("tool_context", "chat")
  adapter_cfg["workspace"] = str(self._effective_router().workspace)
  run_store = getattr(getattr(kernel, "runs", None), "store", None)
  runtime_root = getattr(run_store, "root", self.sessions_dir.parent / "runtime")
  ```
  Mas o construtor do `AgnoRuntimeAdapter` usado por `_run_chat_team` NÃO
  passa `tool_router=`/`activity_sink=`/`event_sink=` — é específico pra
  orquestração de time formal. Para um turno de agente único (o que esta
  sprint precisa), o template certo é `server.py::_run_agno_turn`
  (linhas ~799-835, já referenciado nas specs dos sprints 38/39), que
  passa `tool_router=active_router` explicitamente — sem isso, as tools
  do canal não seriam oferecidas ao Agno.

## Objetivo e escopo

1. **Chave de ativação própria do Telegram**, seguindo a lição do sprint
   37 (não reaproveitar uma chave cujo default já é "agno" em outro
   contexto): `runtime.telegram_default_adapter: str = "bauer_native"`
   em `RuntimeSection` (`bauer/config_loader.py`), ao lado de
   `cli_default_adapter` já existente. Não reaproveitar `_runtime_mode()`
   (o toggle do painel web `/api/runtime/mode`) — Telegram roda como
   processo de gateway próprio, nem sempre o mesmo processo do `bauer
   serve`; herdar silenciosamente o toggle do painel web seria o mesmo
   erro que o sprint 37 corrigiu para a CLI.
2. Novo método `_turno_agno` em `ChannelBase`, mesma assinatura de
   `_turno_cru` (`ctx, key, client, model, text, on_delta=None, *,
   metrics=None`), construindo o `AgnoRuntimeAdapter` no padrão de
   `_run_chat_team` + os parâmetros extras de `server.py::_run_agno_turn`
   (`tool_router`, `activity_sink`/`event_sink` se fizerem sentido nesse
   contexto — avaliar se o canal já tem um sink de atividade equivalente
   a `_publish_agno_activity` do `server.py`, ou se não é necessário
   aqui).
3. Em `_executar`, resolver o executor ANTES de chamar
   `self._turno_cru(...)`/`self._turno_agno(...)` (dentro do
   `with control.activate():` e do `with metrics.context(...):`
   existentes — não mover esses `with` de lugar), condicionado à nova
   chave de config.
4. `ctx.add_assistant(...)` no branch Agno — mesma razão dos sprints
   38/39 (o adapter Agno não escreve em `ctx`); confirmar primeiro que
   `_turno_cru`/`run_one_turn_with_fallback` já faz isso no branch nativo
   (prática já estabelecida: não presumir, ler o código).
5. **Reexecutar a suíte de validação do sprint 34** (plano 061) contra o
   branch Agno novo: `specs/sprint-34-telegram-cancellation/SPEC.md`
   lista os critérios de aceite originais — adaptar/estender os testes
   herméticos equivalentes (`tests/test_channel_execution.py`,
   `tests/test_execution_control.py`, etc. — ver quais já existem) para
   rodar também com o branch Agno ativo via a nova chave de config, sem
   provider real. Isto é o item mais importante desta sprint: provar que
   cancelamento cooperativo, custódia e isolamento entre sessões
   continuam valendo com Agno.
6. Teste real (SDK Agno real, sem provider, mesmo padrão dos sprints
   38/39) cobrindo pelo menos: um turno simples completando via Agno no
   canal, e um cancelamento cooperativo (control.cancel()) interrompendo
   um turno Agno em andamento sem deixar a sessão travada.

## Fora de escopo (explicitamente)

- **Trocar o default de produção na Beelink.** `runtime.
  telegram_default_adapter` fica `bauer_native` no `config.yaml` real —
  esta sprint só entrega a capacidade, testada, atrás de opt-in explícito.
- Aprovação assíncrona para Telegram (não existe hoje, não é desta
  sprint criar — é o plano 061 etapa 4, futura, explicitamente fora do
  MVP do plano 056 segundo contexto já registrado em sessões anteriores).
- Validação manual contra o bot real do Telegram em produção — exigiria
  trocar o default, que está fora de escopo (item acima).

## Skills obrigatórias

- `spec-driven-project-setup`
- `test-strategy`

## Critérios de aceite

- Chave `runtime.telegram_default_adapter` (default `bauer_native`)
  controla o branch, independente de `runtime.default_adapter`/
  `runtime_mode()` do painel web.
- Suíte hermética (sem provider real) reexecutando os critérios do
  sprint 34 contra o branch Agno: cancelamento cooperativo via
  `/cancelar` (ou o equivalente programático, `control.cancel()`)
  interrompe um turno Agno em andamento sem matar a sessão nem deixar
  run órfão; custódia de custo chega ao Kernel mesmo após cancelamento;
  isolamento entre sessões concorrentes preservado.
- Teste real (SDK Agno real) comprova um turno simples completando pelo
  canal com o branch Agno.
- `ctx.add_assistant` não duplica no branch nativo.
- Suíte completa + Ruff bloqueante/estilo verdes; nenhuma regressão nos
  testes existentes de `channel_base.py`/`telegram_bridge.py`/
  `execution_control.py`.
- `plans/062-agno-executor-padrao.md` documenta claramente: código pronto
  e testado, default de produção **não** trocado, decisão de quando
  trocar fica com o operador após tempo de estabilidade.

## STOP

- Se o teste de cancelamento cooperativo contra o branch Agno REVELAR que
  a propagação não funciona como o sprint 35 presumia (ex.: o SDK do Agno
  engole o sinal de cancelamento em algum caminho não coberto pelos
  testes do sprint 35) — parar, documentar o achado concreto, e voltar ao
  operador. Não forçar uma solução frágil num canal que lida com dados
  reais de usuários.
- Não, em nenhuma circunstância nesta sprint, trocar o valor padrão de
  `runtime.telegram_default_adapter` para `"agno"` no `config.yaml` da
  Beelink nem recomendar fazê-lo — isso é uma decisão do operador, fora
  desta sprint, depois de tempo de produção estável.
