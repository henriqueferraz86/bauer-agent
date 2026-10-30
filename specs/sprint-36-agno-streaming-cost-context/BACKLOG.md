# BACKLOG — Sprint 36: streaming, custo e contexto no Agno

- [x] Criar SPEC/ARCHITECTURE antes do código.
- [x] Mapear onde `message.delta` é montado em `stream_agent` e confirmar que
  nenhum caminho hoje chama `delta_stream.emit_delta` — só o bridge manual de
  `server.py` (`_on_agno_event`) faz streaming funcionar, e só para `/stream`.
- [x] Instrumentar `stream_agent` para chamar `emit_delta(content)` no mesmo
  ponto onde o evento `message.delta` já era montado — genérico para
  qualquer chamador, não só o bridge do `/stream`.
- [x] Confirmar que `RunCompletedEvent`/`TeamRunCompletedEvent` do Agno
  instalado carregam `metrics: Optional[RunMetrics]` com
  `input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_write_tokens`.
- [x] Implementar `_report_run_cost`: remapeia os nomes de campo do Agno para
  o formato que `normalize_usage`/`estimate_cost_usd` esperam e chama
  `report_llm_cost` uma vez por run top-level completado (não por membro de
  Team).
- [x] Cobrir com testes herméticos: delta chega a um sink instalado sem
  depender do bridge do `/stream`; custo chega ao `cost_sink` com os campos
  remapeados corretamente; ausência de `metrics` não quebra nem inventa
  custo.
- [x] Cobrir que a resposta SSE do Agno não duplica chunks entregues pelo
  adapter event sink e pelo `delta_stream`.
- [x] Cobrir `stream_team`, métricas com cache por provider e custo reportado,
  sem contabilizar eventos de membros ou de equipes aninhadas duas vezes.
- [x] Nenhuma regressão nos 34 testes pré-existentes do adapter (sprint 35
  incluído).
- [x] Gates do `AGENTS.md`: `uv sync --frozen --extra dev`, Ruff bloqueante
  e de estilo, suíte completa, `uv lock --check` e `git diff --check`.
- [ ] `uv run mypy bauer/` — falha em `bauer/agent_tool_protocol.py:69-70`
  (`None` sem `.get`), fora dos arquivos deste sprint.
- [x] Investigar contexto: `agno_adapter.py` não usa `context_manager.py`;
  usa o mecanismo nativo do Agno (`num_history_runs=3` fixo, sem orçamento
  de tokens, sem compressão semântica). Registrado como decisão de produto,
  não como bug — ver SPEC.md "Decisão necessária".
- [ ] Confirmação do operador sobre a recomendação de contexto (manter o
  mecanismo nativo do Agno por enquanto, revisitar com dado do harness).
- [ ] Harness de comparação (bauer_native vs Agno: custo/latência/qualidade
  pareada) — depende da decisão de contexto acima para não refazer a
  baseline; não implementado nesta sprint.
