# SPEC — Sprint 36: paridade de streaming, custo e contexto no AgnoRuntimeAdapter

Plano 062. Sprint 35 entregou checkpoints de cancelamento; esta sprint fecha
as duas lacunas de paridade que ficaram documentadas como pendência: streaming
genérico e custo/uso, mais uma decisão explícita sobre compressão de
contexto.

## Problema

Verificado no checkout `eca07b0` (branch `codex/sprint-36-agno-streaming-cost`):

1. **Streaming não é genérico.** `AgnoRuntimeAdapter.stream_agent`
   (`bauer/core/runtime/adapters/agno_adapter.py:320-447`) yield eventos
   `message.delta` com o texto do chunk, mas nunca chama
   `bauer/delta_stream.py`'s `emit_delta`/`emit_round_start`. Hoje o único
   motivo de o chat web (`/stream`) mostrar texto chegando aos poucos é que
   `bauer/server.py:1801` (`_on_agno_event`) é um bridge manual escrito
   especificamente para aquele endpoint, que lê `event["content"]` e empurra
   para a fila SSE própria do `/stream`. Qualquer outro caminho que dispache
   via `kernel.execute(executor=None)` direto para o adapter (CLI do sprint
   37, OpenAI-compat do sprint 39) não tem esse bridge e não vai",
   streamar nada — vai ficar em silêncio até `run.completed`.
2. **Custo/uso não chega ao `cost_sink`.** Zero ocorrências de
   `report_llm_cost`/`cost_sink` em `agno_adapter.py` (confirmado por grep).
   `bauer/server.py:1835` instala um `_TurnCostRecorder` no `cost_sink`
   ANTES de despachar para `_run_agno_turn`, mas nada dentro do caminho Agno
   jamais invoca `report_llm_cost`, então `recorder.total_usd` fica travado
   em `0.0` para todo turno Agno — `BudgetManager`/observabilidade nunca veem
   o gasto real de um turno que, hoje, já está ativo em produção. Essa é uma
   lacuna real que já se aplica ao tráfego atual, não só a canais futuros.
   O evento `RunCompletedEvent` do Agno instalado (biblioteca `agno`, ver
   `.venv/lib/python3.12/site-packages/agno/run/agent.py:276-293`) carrega
   `metrics: Optional[RunMetrics]`, e `RunMetrics`/`BaseMetrics`
   (`agno/metrics.py:33-46`) expõe `input_tokens`, `output_tokens`,
   `cache_read_tokens`, `cache_write_tokens`, `cost` — dado suficiente para
   alimentar `bauer/cost_meter.py::report_llm_cost`, desde que os nomes de
   campo sejam remapeados (Agno usa `cache_read_tokens`/`cache_write_tokens`;
   `bauer/account_usage.py::normalize_usage` espera
   `cache_read_input_tokens`/`cache_creation_input_tokens`).
3. **Contexto usa um mecanismo diferente, não necessariamente pior, mas não
   verificado.** `agno_adapter.py` nunca importa `context_manager.py`. Em vez
   disso usa o mecanismo nativo do Agno: `add_history_to_context=True,
   num_history_runs=3` (linhas 634-635, 766-767, 798-799) — uma janela fixa
   de 3 runs, sem orçamento de tokens, sem compressão semântica, sem
   `SUMMARY_PREFIX`. `bauer_native` usa `context_manager.py`: corte por
   orçamento de tokens real por provider, compressão semântica via LLM com
   fallback rule-based, proteção de cauda dinâmica. São estratégias
   genuinamente diferentes, não uma lacuna óbvia de "faltou implementar algo"
   — por isso vira decisão, não código direto (ver seção "Decisão
   necessária").
4. **Harness de comparação não existe ainda.** Não há script/teste que rode a
   mesma tarefa nos dois executores e compare custo/latência/qualidade.

## Objetivo e escopo desta sprint

- Tornar o streaming do `AgnoRuntimeAdapter` genérico: emitir via
  `bauer/delta_stream.py` diretamente no adapter, para que qualquer chamador
  (não só o bridge manual do `/stream`) receba deltas.
- Conectar o caminho Agno ao `cost_sink`/`report_llm_cost` existente,
  reaproveitando `usage_pricing.estimate_cost_usd` — mesma contabilização
  que `bauer_native`, não uma paralela.
- Registrar a decisão sobre contexto/compressão como constatação documentada
  com recomendação, não como código nesta sprint — risco de regressão em
  sessões longas é real o bastante para pedir confirmação do operador antes
  de mudar o comportamento do caminho que já está em produção.
- Harness de comparação: registrado como pendência explícita de
  continuação, não implementado nesta sprint — depende de item 3 estar
  resolvido para que a baseline não precise ser refeita.

Fora de escopo: mudar o executor padrão de qualquer canal, migrar
`context_manager.py` para dentro do Agno sem decisão explícita, implementar
compressão própria para o Agno, mexer no SDK `agno`.

## Skills obrigatórias

- `spec-driven-project-setup`
- `test-strategy` (suíte hermética, sem provider real)

## Requisitos

1. `emit_delta`/`emit_round_start` são chamados pelo adapter diretamente, não
   só pelo bridge de `server.py` — o bridge de `server.py` pode continuar
   existindo para eventos estruturados, mas não pode enfileirar o mesmo
   `message.delta` novamente quando o adapter já enviou o chunk ao sink SSE.
2. `report_llm_cost` é chamado uma vez por run Agno completado com sucesso,
   com os nomes de campo remapeados corretamente (`input_tokens`→
   `input_tokens`, `output_tokens`→`output_tokens`, `cache_read_tokens`→
   `cache_read_input_tokens`, `cache_write_tokens`→
   `cache_creation_input_tokens`).
3. Ausência de `metrics` no evento (SDK não populou, ou run cancelado/falho)
   não gera erro — vira custo zero reportado, igual ao comportamento de
   `report_llm_cost` quando `usage` é vazio.
4. Nenhuma regressão nos testes de sprint 35 (cancelamento continua
   funcionando; o novo código de custo/streaming não pode interferir nos
   checkpoints).
5. Testes herméticos cobrem: delta chega ao sink ativo durante stream_agent;
   custo chega ao cost_sink ativo após RunCompleted; ausência de metrics não
   quebra nem reporta custo incorreto.

## Decisão necessária (não é STOP de SDK, é decisão de produto)

Contexto/histórico do Agno usa `num_history_runs=3` fixo; `bauer_native` usa
orçamento de tokens dinâmico com compressão semântica. Recomendação: manter
o mecanismo nativo do Agno por enquanto (não é um bug, é uma troca de
filosofia deliberada da biblioteca) e tratar isso como um risco conhecido e
documentado para sessões muito longas, revisitado quando o harness de
comparação (item 4) rodar tarefas longas o bastante para medir a diferença
na prática — decidir às cegas, sem medição, seria pior que esperar o dado.
Esta recomendação precisa de confirmação do operador antes de fechar a
sprint como concluída; não é um STOP que bloqueia os outros dois itens.

## Critérios de aceite

- Um teste hermético comprova que texto chega a um sink de `delta_stream`
  instalado ANTES de chamar `stream_agent`/`run_agent`, sem depender do
  bridge de `server.py`.
- Um teste hermético comprova que, com um `cost_sink` instalado, completar um
  run Agno invoca o sink com `provider`, `model` e um `usage` dict não-vazio
  quando o evento de conclusão carrega `metrics`.
- Um teste hermético comprova que a ausência de `metrics` não derruba o run
  nem inventa custo.
- Suíte completa e Ruff bloqueante seguem verdes.
- A decisão sobre contexto está registrada no plano 062 e aguardando
  confirmação explícita do operador antes de marcar o item 3 como fechado.
