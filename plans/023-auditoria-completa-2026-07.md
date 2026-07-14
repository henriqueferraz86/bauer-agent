# Plano 023 — Auditoria completa 2026-07 (semana de testes & melhorias)

**Gerado por `/improve` (deep, 7 auditores paralelos) em 2026-07-14, commit base `e0d9078`.**

Este é o plano-mestre ÚNICO com os 20 achados vetados da auditoria completa
do Bauer (Kernel, runtime, orquestração, tool router, canais, serve HTTP,
voz, App Factory, /loop, skills, especialistas). Semana dedicada a
**testes e melhorias — nenhuma feature nova**.

## Regras de execução

- **Uma branch por melhoria**, nomeada `fix/NN-slug` / `chore/NN-slug` /
  `perf/NN-slug` / `test/NN-slug`, tirada de `master` limpo.
- Cada branch: implementar → rodar suíte alvo + `pytest -q` → commit → push.
- Verificação padrão: `python -m pytest tests/ -q` (via `.venv`), lint
  bloqueante `ruff check bauer/ --select E9,F63,F7,F82`.
- Marcar o status na tabela ao concluir.
- **STOP e reportar** se um fix revelar que o achado estava errado ou o
  blast radius for maior que o estimado.

## Nota sobre o subsistema de voz

Os achados de voz do master (temp-leak em `audio_capture.py`, stream duplo,
extra `voice` incompleto) **já estão resolvidos no branch
`feat/confirm-commands-allowlist-learning`** (reescrito para captura por
ENTER + extra completo). Ação: **fazer merge desse branch** em vez de
re-planejar no master. Rastreado como item M0.

## Ordem de execução e status

| # | Branch | Achado | Cat | Esf | Arquivos | Status |
|---|--------|--------|-----|-----|----------|--------|
| M0 | (merge) | Merge do branch de voz (zera achados de voz do master) | — | S | — | TODO |
| 01 | fix/01-recovery-waiting-approval | Recovery mata runs em `waiting_approval`/`paused` | Bug | S | core/runtime/resilience.py | TODO |
| 02 | chore/02-untrack-tmp | `tmp/` (35) + `test_request.json` versionados | Debt | S | .gitignore | TODO |
| 03 | chore/03-remove-escalation-dead | `escalation.py` (388L) é dead code | Debt | S | bauer/escalation.py, tests/ | TODO |
| 06 | fix/06-scheduled-task-reschedule | Tarefas bloqueadas re-disparam a cada tick | Bug | S | core/runtime/scheduler.py | TODO |
| 07 | fix/07-run-cost-guardrail | `bauer run --max-cost` inerte (guardrail morto) | Bug | S | commands/run_cmd.py | TODO |
| 12 | fix/12-loop-limits-4xx | `/loop` retorna 500 (não 4xx) com limites inválidos | Bug | S | server.py | TODO |
| 13 | chore/13-commit-uv-lock | `uv.lock` gitignorado + pins só `>=` | DX | S | .gitignore, ci.yml, pyproject.toml | TODO |
| 19 | docs/19-agents-md | `AGENTS.md` stub; sem contrato p/ agentes | DX | S | AGENTS.md, CLAUDE.md | TODO |
| DOCS | docs/docs-nova-pasta | `docs/Nova pasta/` + deleções + links quebrados | Docs | S | docs/, README.md, CHANGELOG.md | TODO |
| 04 | fix/04-path-coercion-guard | `Path(MagicMock())` polui a raiz (bug de coerção) | Test/Bug | M | memory_context.py, logging_config.py, conftest.py | TODO |
| 05 | fix/05-transcribe-body-limit | `/transcribe` lê upload inteiro sem limite (DoS) | Sec | S | server.py | TODO |
| 09 | perf/09-decision-embed-cache | DecisionMemory re-embeda a base a cada turno | Perf | M | decision_memory.py | TODO |
| 17 | perf/17-prefetch-instance-cache | prefetch reconstrói stores por turno | Perf | S | memory_context.py | TODO |
| 20 | perf/20-async-blocking-io | I/O bloqueante em rotas async do FastAPI | Perf | S | server.py | TODO |
| 15 | fix/15-ssrf-redirect-revalidate | SSRF: redirects não revalidados | Sec | M | tools/web.py | TODO |
| 14 | test/14-ci-windows-matrix | CI só Ubuntu; produto mira Windows | Test | S | ci.yml | TODO |
| 16 | fix/16-max-runtime-enforce | `max_runtime_s` não interrompe run travado | Bug | M | core/runtime/scheduler.py | TODO |
| 08 | fix/08-state-store-lock | State store JSONL sem lock (lost-update) | Bug | L | core/runtime/state_store.py | TODO |
| 11 | fix/11-budget-toctou | Budget TOCTOU + `max_parallel_runs` morto | Bug | M | core/runtime/autonomy.py | TODO |
| 18 | refactor/18-runtime-registry-rename | Dois `AgentRegistry` com mesmo nome | Debt | M | core/runtime/agent_registry.py | TODO |
| 10 | (spike) | Migração de task-store parada (2 gerações) | Debt | L | (characterization tests primeiro) | TODO |

Status: `TODO` | `IN PROGRESS` | `DONE` | `BLOCKED (motivo)` | `MERGED`

## Dependências e agrupamentos

- **#05 e #20 tocam `transcribe` em server.py** — #05 primeiro (limite de body),
  #20 depois (mover para executor) OU juntos numa branch. Escolha: sequencial,
  #05 antes.
- **#04 e #17 tocam memory_context.py** — #04 (guard de tipo) antes, #17
  (cache de instância) depois; ou coordenar rebase.
- **#08 destrava #11 e #16** conceitualmente (atomicidade do store), mas cada
  um pode ser corrigido isolado; fazer #08 por último dos três (é L/risco MED).
- **#10 é o item estratégico** — NÃO implementar direto; exige characterization
  tests dos dois backends antes. Vira spike/design, último da fila.

## Ordem recomendada (leva 1 → leva 3)

**Leva 1 — quick wins (S, LOW risco, independentes):** M0, 01, 02, 03, 06, 07,
12, 13, 19, DOCS.
**Leva 2 — S/M focados:** 04, 05, 17, 20, 09, 15, 14.
**Leva 3 — M/L estruturais (rede de segurança = suíte verde):** 16, 11, 08, 18.
**Leva 4 — estratégico:** 10 (spike).

## Detalhamento por item

### M0 — Merge do branch de voz
Branch `feat/confirm-commands-allowlist-learning` já reescreveu
`audio_capture.py` (captura por ENTER, sem stream duplo, sem temp-leak),
completou o extra `voice` no `pyproject.toml` e adicionou `tts_local.py` +
testes. Fazer merge para o master zera CORR-03/06, TEST-02 e DEPS-03 da
auditoria. **Verificar suíte verde no merge.**

### 01 — Recovery mata runs em waiting_approval/paused
`bauer/core/runtime/resilience.py:86` — `recover_stuck_runs` só pula
`TERMINAL_RUN_STATUSES = {completed, failed, cancelled}`. `waiting_approval`
e `paused` são não-terminais → um run esperando aprovação humana >15min vira
`failed`, e o `approve()` posterior quebra com `KernelStateError`.
**Fix:** adicionar um conjunto `RECOVERABLE_RUN_STATUSES` (ou pular
explicitamente `waiting_approval`/`paused`) — recuperar só
`running`/`queued`/`retrying`/`policy_check`/`planning`/`evaluating`.
**Teste:** run em `waiting_approval` velho NÃO é recuperado; run `running`
velho É.

### 02 — Untrack tmp/ + test_request.json
`git ls-files tmp/` → 35 arquivos versionados (logs de sprint, .jsonl, .db).
`test_request.json` na raiz também. **Fix:** adicionar `tmp/` e
`test_request.json` ao `.gitignore`, `git rm --cached` os 36, manter no disco.

### 03 — Remover escalation.py (dead code)
`bauer/escalation.py` (388L). Único `import escalation` real está na própria
docstring do módulo (linha 19); os demais hits são a palavra "escalation" em
comentários/params. Só `tests/test_decision_memory.py` importa. **Fix:**
confirmar que `decision_memory.py` o substituiu, remover módulo + o bloco de
teste que o importa.

### 06 — Tarefas agendadas bloqueadas re-disparam
`bauer/core/runtime/scheduler.py:132` (kill_switch) e `:159` (budget) fazem
early-return sem chamar `_after_run` (que recalcula `next_run_at`). Tarefa
`active` com budget estourado continua "due" e reprocessa a cada tick.
**Fix:** avançar `next_run_at` (ou aplicar backoff) também nos caminhos de
skip por budget/kill-switch. **Teste:** tarefa bloqueada por budget não
aparece em `due_tasks` no tick seguinte imediato.

### 07 — bauer run --max-cost inerte
`bauer/commands/run_cmd.py` cria `AutonomousBudget(max_cost_usd=...)` mas
nunca monta um cost sink nem chama `budget.consume_cost()`. O `/loop` da web
(`server.py`) faz isso via `cost_sink.set` + `consume_cost`. **Fix:**
replicar o wiring do servidor no run_cmd (recorder/sink por rodada).
**Teste:** turno devolvendo custo → `--max-cost` dispara; snapshot mostra
custo != 0.

### 12 — /loop 500 → 4xx
`bauer/server.py:1729` (`_loop_limits`) — `resolve_loop_limits` (que levanta
ValueError p/ `max_minutes<=0` etc.) fica fora do try. **Fix:** envolver em
try/except convertendo ValueError → `HTTPException(422)`. **Teste:** POST
`/loop` com `max_minutes:0` retorna 422, não 500.

### 13 — Commitar uv.lock + pins
`.gitignore` ignora `uv.lock`; pins são só `>=`. **Fix:** remover `uv.lock`
do gitignore, commitar, trocar CI para `uv sync --frozen`. Considerar teto
nas 3-4 deps de maior blast radius (pydantic, typer, httpx). Regenerar lock
(hoje referencia extra `agno` inexistente — declarar `agno` extra ou remover).

### 19 — AGENTS.md real
`AGENTS.md` é stub de 1 linha; sem `CLAUDE.md`. **Fix:** popular com layout
do pacote, comandos de verificação (pytest/ruff/uv), modelo mental do
kernel/runtime adapters e do `bauer run`. (Corresponde ao plano 012 antigo,
nunca executado — reaproveitar/atualizar.)

### DOCS — docs/Nova pasta + links quebrados
Working tree deletou 6 docs de `docs/` e recriou em `docs/Nova pasta/`
(untracked, nome default do Windows). README:40 e CHANGELOG linkam os alvos
deletados. **Fix:** decidir — restaurar em `docs/` ou aceitar deleção e
corrigir links; remover a "Nova pasta". (Stash `audit-2026-07` guarda o
estado atual do working tree.)

### 04 — Guard de coerção de path + isolamento de teste
`memory_context.py:73` e `logging_config.py:42` aceitam qualquer objeto
truthy como path e escrevem em disco → `Path(MagicMock())` cria `MagicMock/`
na raiz. **Fix:** validar `isinstance(x, (str, os.PathLike))` antes de
escrever (falha rápido em vez de silencioso); em `tests/conftest.py`,
fixture `autouse` que faz `monkeypatch.chdir(tmp_path)`. Corrigir os ~4
testes que usam `cfg = MagicMock()` para `MagicMock(spec=BauerConfig)`.
**Teste:** passar não-path levanta; suíte não escreve na raiz.

### 05 — Limite de body no /transcribe
`server.py:1367` faz `await file.read()` (corpo inteiro em RAM) antes de
qualquer checagem; o guard de 25MB só roda em `transcribe_audio`. **Fix:**
rejeitar cedo por `Content-Length` (>limite → 413) e ler em streaming com
corte; reusar `MAX_AUDIO_BYTES` como fonte única. Validar content-type/ext
antes de escrever (SEC-03 fold-in). **Teste:** upload > limite → 413 sem
materializar tudo.

### 09 — Cache de embedding de decisão
`decision_memory.py:346` embeda cada registro a cada `search()`. **Fix:**
persistir o vetor no `record()` (coluna ou reusar vector_store), marcado com
assinatura do backend/dim; no `search()` embedar só a query. **Teste:**
2 buscas seguidas → `embed` do registro chamado 1×, não por busca.

### 17 — Cache de instância no prefetch
`memory_context.py:74/85` instancia `DecisionMemory` + `SqliteSessionStore`
a cada turno (DDL + probe FTS5 + migração JSONL). **Fix:** cache
`{workspace: (dm, store)}` módulo-level; `_migrate_jsonl` só 1×. **Teste:**
2 prefetches p/ mesmo workspace → 1 init.

### 20 — I/O bloqueante em rota async
`server.py:1998` (`oai_chat_completions`) e `:1357` (`transcribe`) são
`async def` chamando I/O síncrono sem executor. **Fix:** `anyio.to_thread`/
`run_in_executor`, ou trocar p/ `def` (Starlette threadpool, como `/chat`).

### 15 — Revalidar redirects (SSRF)
`tools/web.py` valida só a URL inicial e segue `follow_redirects=True`.
`url_safety.validate_redirect_chain()` existe mas não é chamada. **Fix:**
`follow_redirects=False` + seguir manualmente cada `Location` por
`check_url` com teto de hops, nos dois call sites (`web_fetch`,
`http_request`). **Risco:** pode bloquear redirects legítimos — testar.

### 14 — Matriz Windows no CI
`ci.yml` job `test` roda só `ubuntu-latest`. **Fix:** `os: [ubuntu-latest,
windows-latest]`. Fazer DEPOIS de #04 (senão o run inicial no Windows falha
por poluição). Começar `continue-on-error` no Windows se houver falhas
Windows-específicas a triar.

### 16 — Enforce max_runtime_s
`scheduler.py:207` checa `max_runtime_s` só após o adapter retornar (pós-fato)
e não reinicia `started` por tentativa. **Fix:** rodar adapter em worker
cancelável com deadline (thread + stop_run) e reiniciar `started` por
tentativa; enquanto adapters forem síncronos não-canceláveis, documentar como
best-effort. **Risco MED** — exige execução cancelável.

### 11 — Budget TOCTOU
`autonomy.py:91` — `ensure_can_start` lê `used` sem reserva; custo só grava no
fim. N runs concorrentes furam o limite. `max_parallel_runs` não é enforçado.
**Fix:** reserva de custo estimado no início, conciliada no fim; contar runs
não-terminais p/ `max_parallel_runs`. Depende de #08 p/ atomicidade real.

### 08 — Lock no state store
`core/runtime/state_store.py` — `append`/`list`/`latest` sem lock; RMW
concorrente (thread do /stream + scheduler) causa lost-update/stale read.
**Fix:** `threading.Lock` por-collection + write+flush atômico; a médio prazo
índice `id→registro` em memória. **Risco MED** — camada central, exige
characterization tests antes. Fazer por último dos bugs.

### 10 — Migração de task-store (SPIKE)
`kanban_store`+markdown (9 imports) vs `kanban_db`+SQLite (9 imports);
`workspace_manager_sqlite` é "drop-in" com 1 adoção. **NÃO implementar
direto.** Entregável: spec de migração (backend vencedor = SQLite,
characterization tests dos dois, shim de compat, plano de virada faseado).
