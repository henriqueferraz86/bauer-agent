# BACKLOG — Sprint 60

## Tarefas

### 1. Reproduzir K08 e K07 no código atual

Status: concluído

- `bauer-audit-kernel-repro.py` em `ddd608c`: `continue_ignores_kill_switch`
  e `replan_ignores_kill_switch` terminavam `completed`, e `secrets_empty_git`
  dava `passed: true`. Saída registrada no SPEC.

### 2. Persistir o contexto de governança da admissão

Status: concluído

- `run.input["_kernel_governance"] = {operation, metadata}` em `_open_run`.
  O valor sobrescreve qualquer chave homônima vinda do input do caller.
- `continue_run` remove a chave do payload do executor.

### 3. Revalidar kill-switch e policy por tentativa (K08)

Status: concluído

- `_revalidar_governanca` antes da continuação, de cada retry, de cada
  fallback e no `policy_check` do replan.
- `ask` com aprovação vigente segue. Sem aprovação, vai para
  `waiting_approval` + `ApprovalRecord`. `deny` → `failed`. Kill-switch →
  `cancelled`.
- `resume()` sem ApprovalManager registra a decisão humana.

### 4. Propagar o bloqueio aos front-ends

Status: concluído

- `continue_governed`: `blocked_before_start`, `policy_reason` e
  `approval_id`.
- `/loop` Telegram: o relatório diz `kill_switch`/`cancelled`, não `error`.

### 5. SecretsGate em repo sem HEAD e com falha (K07)

Status: concluído

- Não-repo × sem HEAD × erro operacional. Index + worktree + untracked contra
  HEAD ou árvore vazia. Erro vira reprovação explícita.

### 6. Regressões

Status: concluído

- `tests/test_kernel_revalidacao_governanca.py` (16 testes): kill-switch em
  continuação/replan/retry/fallback, policy deny/ask/locked entre tentativas,
  operação/metadata persistidos, aprovação preservada e o deny que vence a
  aprovação.
- `tests/test_kernel_continuacao_caminhos.py`: `/loop` web e `/loop` Telegram
  com kill-switch ligado entre a admissão e a thread. Os dois exigem que o
  laço `run_loop_rounds` nunca seja chamado: o laço tem o próprio
  `should_stop`, então contar só as rodadas passaria também sem a correção.
- `tests/test_secrets_gate_repo_sem_commit.py` (14 testes): untracked, staged,
  staged-e-apagado (com e sem HEAD), repo vazio limpo, fora de repo sem git,
  git ausente, timeout, erro de `diff`/`ls-files`/`rev-parse` e scanner
  quebrado no Evaluator.
- Conferido: 11 testes de K08, os 2 de caminho e 12 de K07 falham com `bauer/`
  revertido para `ddd608c`. Os demais travam o comportamento que precisa ser
  preservado (aprovação válida, fora-de-repo, evento único no `execute`).

## Notas de entrega

- Base `origin/master` `ddd608c`; branch `codex/sprint-60-security-kernel-gates`.
- Prova pós-correção (`bauer-audit-kernel-repro.py`): continuação `cancelled`
  com 0 chamadas, replan `cancelled` com 1 chamada, `secrets_empty_git`
  `passed: false`. Os demais casos do script ficam inalterados (já corrigidos
  na sprint 27).
- Primeira rodada da suíte completa: 1 falha em
  `tests/test_telegram_loop.py::test_happy_path…`. O `FakeKernel` do teste
  devolve um `SimpleNamespace` sem `policy_reason`, e
  `continue_governed` passou a ler esses campos com `getattr`. Depois disso, a
  suíte completa passou (ver gates abaixo).
- Fica em aberto, por decisão registrada no SPEC: gasto cumulativo durante o
  run não é recalculado na revalidação, porque a reserva do ledger é
  idempotente por `run_id`. Interromper uma tentativa já em curso continua a
  cargo do `should_stop` de cada laço.

## Gates

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
```
