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
- [x] Confirmação do operador sobre a recomendação de contexto: "manter Agno
  como padrão" (2026-10-30), após ver o resultado real do harness. Risco de
  sessão longa (histórico fixo `num_history_runs=3` vs. compressão dinâmica
  do `bauer_native`) segue sem dado — nenhuma das 4 tarefas testou isso —
  mas foi aceito conscientemente, não decidido às cegas.
- [x] Harness de comparação implementado em `evals/compare_executors/`:
  roda um conjunto fixo de 4 tarefas representativas (Q&A simples, leitura de
  arquivo via tool, raciocínio curto, código curto) nos dois executores,
  reaproveitando `_build_client`/`_build_router`/`run_one_turn_with_fallback`
  (bauer_native) e `AgnoRuntimeAdapter.run_agent` (Agno) — os mesmos pontos
  de entrada que o CLI/serve usam de verdade, não uma reimplementação
  paralela. Mede custo/tokens/latência via o mesmo `cost_sink` desta sprint;
  qualidade fica como campo `quality_verdict` em branco para revisão manual
  pareada, conforme a metodologia já definida. Testado hermeticamente
  (`tests/test_compare_executors.py`, 7 testes, mocka os clientes/adapter —
  não bate em provider real). Rodar de verdade exige `python -m
  evals.compare_executors --config config.yaml` com credenciais reais; isso
  **não roda na suíte padrão nem em CI**.
- [x] Rodado de verdade contra o provider real da Beelink (deepseek/deepseek-chat
  via openrouter) em 2026-10-30. Primeira rodada expôs um bug do harness —
  `agent_spec` do Agno não levava `tools`, então ele rodava sem nenhuma
  ferramenta (custo artificialmente ~65x menor, e a tarefa de leitura de
  arquivo falhava porque não tinha `read_file`). Corrigido (mesmo catálogo
  de tools e mesmo system prompt nos dois lados) e re-rodado: custo/tokens
  praticamente empatados em 3 das 4 tarefas (~2% de diferença), gap maior
  só na tarefa de código (+53% no Agno, não investigado a fundo). Qualidade
  revisada manualmente par a par (ver `SPEC.md`) — equivalente nas 4
  tarefas. JSON completo em `/tmp/comparacao_agno_vs_native.json` (fora do
  repo, não commitado).
