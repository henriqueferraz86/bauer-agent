# BACKLOG — Sprint 61: fronteiras de execução

## Tarefas

### 0. Reproduzir no código atual

Status: concluído

- T01, T06, T08 e os metadados do lote 6 reproduzem com
  `bauer_audit_tools_probe_extra.py` (valores na SPEC).
- `bauer_audit_tools_probe.py` agora continua depois do caso de cron
  corrigido (`CRON_SHELL_DENIED`).

### 1. cronjob run pelos gates de shell/código (T01)

Status: concluído

- `execute_governed` + `INNER_GOVERNED_CALL` em `tools/base.py`.
- `_cronjob_run_governed` em `tools/cronjob.py`: shell → `run_command`,
  python → `execute_code`, com `approval_id` repassado. O modo shell é
  recusado sem shell.
- Regressões: `test_t01_*`.

### 2. dry-run por efeito declarado (T06)

Status: concluído

- `bauer/tool_effects.py` e `ToolDefinition.side_effects` (default `True`).
- Regressões: `test_t06_*` (matriz mutante, leituras, extensão, override e
  paridade do inventário).

### 3. Segurança efetiva no dispatcher (T08)

Status: concluído

- `_effective_security` usado em `tool_security`/`tool_info`, eventos,
  aprovação por LLM e dry-run. Override não rebaixa.
- A policy `tool.approval` (regra default `ask` e ask quando nenhuma regra
  casa) é aplicada a extensões com aprovação.
- Regressões: `test_t08_*`.

### 4. Metadados de segurança do catálogo (lote 6)

Status: concluído

- `kanban_read`/`kanban_write` com metadados próprios. Removidos os órfãos
  `kanban_*` legados e `video_generate`. `verify_app`, `memory` e `cronjob`
  foram corrigidos.
- Regressões: `test_lot6_*`, com paridade inventário ↔ `_TOOL_SECURITY` e
  níveis válidos.

### 5. Gates e revisão

Status: concluído

- `uv sync --frozen --extra dev`: ok.
- `uv run pytest tests/ -q --tb=short`: exit 0.
- Ruff E9/F63/F7/F82 e E/F/W: ok.
- `uv run mypy bauer/`: "Success: no issues found".
- Autorrevisão de segurança: o flag interno só afeta o contador de budget, o
  `approval_id` repassado continua preso ao digest exato do comando, e um
  override não rebaixa a segurança. Nenhum teste antigo foi relaxado: os
  testes de cron shell ganharam `ShellRunner`.

## Notas de entrega / em aberto

- `lsp_rename` (built-in, `approval=True`, sem operação de policy) só passa
  pelo aprovador por LLM. Ligá-la a `tool.approval` é decisão de contrato de
  built-in, para uma próxima rodada.
- O prompt de worker Kanban em `agent.py` cita nomes `kanban_*` antigos
  (drift funcional de T07).
- `tool.approval` herda o `risk_level` do `RiskClassifier`, que é `low` para
  operação desconhecida. O cartão de aprovação mostra esse risco, e não o
  risco declarado pela extensão. É cosmético e não afeta a decisão.
- Coordenador: atualizar `plans/README.md` e o status do plano 064.
