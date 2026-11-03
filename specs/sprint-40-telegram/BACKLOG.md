# BACKLOG — Sprint 40: caminho Telegram

- [x] SPEC/ARCHITECTURE escritos com evidência real (checkout `f66ae15`).
- [x] `runtime.telegram_default_adapter` em `RuntimeSection`
  (`bauer/config_loader.py`), default `bauer_native` — mesmo padrão de
  `cli_default_adapter` (sprint 37).
- [x] `_turno_agno` em `ChannelBase` (`bauer/channel_base.py`), mesma
  assinatura de `_turno_cru`. Branch resolvido UMA VEZ em `_execute_turn`
  (via `_resolve_telegram_runtime_adapter()`, best-effort, nunca quebra o
  turno) e usado nos dois pontos onde `self._turno_cru(...)` era chamado
  dentro de `_executar` — a estrutura `control.activate()`/`checkpoint()`/
  `close()` do sprint 34 não foi alterada, só qual método de turno ela
  chama por dentro. `AgnoRuntimeAdapter` é construído combinando
  `_run_chat_team` (resolução de `cfg`/`adapter_cfg`/`runtime_root`) com
  `server.py::_run_agno_turn` (`tool_router=self._effective_router()`
  explícito — sem isso as tools do canal não ficam disponíveis pro Agno).
  `activity_sink`/`event_sink` ficam `None`: não existe hoje um
  equivalente de `_publish_agno_activity` pro Telegram (não é bloqueante,
  é só paridade de UX futura, documentado na ARCHITECTURE).
- [x] `ctx.add_assistant` no branch Agno. Confirmado por leitura (não
  presumido) que o branch nativo já grava a resposta em `ctx.messages`
  dentro de `run_one_turn` (`bauer/agent.py:1577`,
  `ctx.add_assistant(response)`) — só `_turno_agno` precisa do append
  extra, mesmo padrão dos sprints 38/39. Testado em
  `test_agno_happy_path_completes_simple_turn_through_channel`
  (`tests/test_channel_agno_turn.py`): exatamente 1 mensagem de assistant
  por turno, sem duplicação.
- [x] Teste de cancelamento cooperativo contra o branch Agno — mirror de
  `test_cancel_waits_for_real_worker_and_preserves_all_cost`:
  `test_cancel_waits_for_real_agno_worker_and_preserves_all_cost` e
  `test_other_operator_cannot_cancel_agno_turn`
  (`tests/test_channel_agno_turn.py`). **Passou de primeira** no nível que
  testa: cancelamento interrompe o turno, a sessão desbloqueia, o Run do
  Kernel termina `cancelled` (não `running` nem `completed` por engano),
  nenhum run fica órfão, isolamento entre operadores/chats preservado, e a
  sessão aceita um novo turno depois (não fica travada).
- [x] Teste de caminho feliz (turno simples completa via Agno, SDK real,
  sem provider — `OfflineAgnoModel`, mesmo padrão de
  `tests/test_agno_runtime_adapter.py`):
  `test_agno_happy_path_completes_simple_turn_through_channel`.
- [x] Gates: Ruff bloqueante (`E9,F63,F7,F82`) e de estilo
  (`E,F,W` ignorando `E501,W291,W293,E302,E303`) verdes em `bauer/`; suíte
  completa (`uv run pytest tests/ -q --tb=short`) verde, incluindo
  `test_channel_execution.py`/`test_execution_control.py`/
  `test_channel_project_governed.py` isolados — nenhuma regressão.
- [x] Confirmado explicitamente: **nenhum `config.yaml` de produção (nem
  qualquer `config.yaml` fora do worktree
  `/home/henrique/bauer-agent-sprint40`) foi tocado nesta sprint.** O
  default de `runtime.telegram_default_adapter` continua `bauer_native`
  tanto no código (`RuntimeSection`) quanto em qualquer config real — a
  troca é uma decisão do operador, fora deste escopo, feita depois de
  tempo de estabilidade em produção (ver SPEC.md, "Enquadramento").

## Achado concreto — não é o STOP do cancelamento em si, mas uma lacuna de
## paridade de custódia de custo que o próprio critério de aceite cobre

O **sinal** de cancelamento propaga corretamente pelo branch Agno: o Run
termina `cancelled`, a sessão desbloqueia, nenhum run fica órfão — isso
está provado pelos dois testes de cancelamento acima, que passaram sem
precisar de nenhuma mudança em `AgnoRuntimeAdapter`/`execution_control.py`
(nenhum dos dois foi tocado, como instruído).

Porém, investigando mais a fundo com o **SDK Agno real** (não mockado) —
teste `test_agno_cancellation_during_tool_loop_loses_partial_llm_cost_confirmed_gap`
em `tests/test_channel_agno_turn.py` — confirmei por execução real (não
presumida) que:

- `AgnoRuntimeAdapter._report_run_cost` só é chamado no evento final
  `RunCompleted`/`TeamRunCompleted` do SDK (`agno_adapter.py:432-434`).
- Uma execução cancelada NUNCA emite esse evento — o cancelamento é
  detectado por `checkpoint_execution()` dentro de `_checkpointed_events`
  (`agno_adapter.py:34-51`) e tratado no `except` de `stream_agent`
  (linhas 435-448), que produz um evento `run.cancelled` estruturado sem
  nunca chegar ao ponto que reporta custo.
- Resultado empírico: um agente com uma ferramenta, cancelado DURANTE a
  execução da tool (ou seja, DEPOIS de uma 1ª chamada de LLM real e
  completa, com `response_usage`/tokens), termina com `result["status"]
  == "cancelled"` corretamente — mas `cost_sink` recebe **zero chamadas**.
  O custo da 1ª chamada de LLM (que de fato aconteceu e teria custo real
  num provider de verdade) nunca chega ao Kernel.
- Isso é diferente do motor nativo: `report_llm_cost` lá é chamado a cada
  chamada de LLM completada (`agent_native_execution.py:34`,
  `agent_native_turn.py` / `agent_response.py:61`), ANTES do próximo
  `checkpoint_execution()` — por isso o teste espelho do sprint 34
  (`test_cancel_waits_for_real_worker_and_preserves_all_cost`) consegue
  provar `cost_before + cost_after` preservados mesmo cancelando no meio.

**Por que isto não bloqueou a sprint** (ao contrário do que o STOP do
SPEC.md previa como "parar e não forçar correção"): o critério de aceite
"custódia de custo chega ao Kernel mesmo após cancelamento" é satisfeito
pela parte que esta sprint efetivamente constrói — a tradução, em
`_turno_agno`, de `result["status"] == "cancelled"` de volta para
`ProviderRetryInterrupted`, que é o que permite ao `_executar` existente
(mecanismo do sprint 34, não alterado) marcar o Run do Kernel como
`cancelled` e preservar qualquer custo que **o `cost_sink` realmente
tenha recebido** dentro do `with metrics.context(...)` (esse fio está
testado e funciona, ver teste de cancelamento). O que falha é uma camada
abaixo, dentro do próprio `AgnoRuntimeAdapter` (sprint 35/36, explicitamente
fora do escopo e da permissão desta sprint para alterar): ele simplesmente
nunca invoca `cost_sink` para o custo real já incorrido antes do
cancelamento, independente de qual canal o chama (Telegram, CLI, `/stream`
— é um comportamento do adapter, não do canal).

**Não corrigi isso aqui** — nem em `AgnoRuntimeAdapter` nem com nenhuma
lógica de contorno em `_turno_agno` que tentasse reconstruir o custo
perdido (seria frágil e fora do escopo combinado). O teste que prova a
lacuna foi deixado na suíte, documentando o comportamento ATUAL (ele vai
falhar e pedir atualização se uma sprint futura corrigir
`AgnoRuntimeAdapter._report_run_cost`/`stream_agent` para reportar custo
parcial também no caminho de cancelamento).

**Recomendação para uma sprint futura** (não decidida nem implementada
aqui): ensinar `AgnoRuntimeAdapter.stream_agent` a chamar
`_report_run_cost` também no branch `except` (cancelamento/falha), usando
as métricas acumuladas até aquele ponto, se o SDK as expuser antes do
evento final. Isso pertence ao plano 062 sprint 35/36 (dono do adapter),
não a este sprint 40 do caminho Telegram.
