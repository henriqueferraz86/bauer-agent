# BACKLOG — Sprint 43: entrega de resultado de tarefas em background

- [x] SPEC/ARCHITECTURE escritos com evidência real reconferida (checkout
  `28d5fcb`), consolidando os itens 1-6/sprints 41-44 do plano 063 numa
  sprint só.
- [ ] Decidir e implementar o método de `TelegramBridge` que dá sinal de
  sucesso/falha (`send_text_result` ou equivalente).
- [ ] `register_delivery_channel`/`deliver_to_session` em
  `channel_execution.py`.
- [ ] `TelegramBridge` registra `"tg:"` no ponto de construção/inicialização.
- [ ] Captura de `origin_session_id` via `get_runtime_ids()` em
  `run_command(background=true)` (`execution.py`).
- [ ] Watcher (thread) por processo background, chama o ponto de entrega
  ao terminar.
- [ ] Idempotência (dict/set em memória, chave pid+started_at).
- [ ] Retry curto (2s/5s/10s) distinguindo erro transitório/permanente.
- [ ] Testes herméticos: registro, captura de origem, idempotência,
  retry, ponta a ponta mínimo.
- [ ] Gates: Ruff bloqueante/estilo, suíte completa, nenhuma regressão em
  `test_controlled_processes.py`/`test_telegram_bridge.py`/
  `test_channel_base.py`/`test_channel_execution.py`.
- [ ] Atualizar `plans/063-telegram-entrega-tarefas-background.md`:
  status, renumeração de sprints (41-44 → 43), registro do que foi
  entregue.
- [ ] Atualizar `plans/README.md`.
