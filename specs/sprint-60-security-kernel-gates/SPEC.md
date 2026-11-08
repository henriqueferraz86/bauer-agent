# SPEC — Sprint 60: kill-switch em continuação/replan e SecretsGate em repo vazio

Plano: [`plans/064-seguranca-auditoria-2026-10-12.md`](../../plans/064-seguranca-auditoria-2026-10-12.md),
lotes 3 (K08) e 5 (K07) de
[`docs/audits/2026-10-12/CORRECTIONS.md`](../../docs/audits/2026-10-12/CORRECTIONS.md).
Evidência original em [`KERNEL.md`](../../docs/audits/2026-10-12/KERNEL.md).
Base: `origin/master` em `ddd608c`.

## Reprodução no código atual (primeiro passo)

`uv run python docs/audits/2026-10-12/repro/bauer-audit-kernel-repro.py` em
`ddd608c`, antes de qualquer mudança:

```text
{"case": "continue_ignores_kill_switch", "kill_switch": true, "status": "completed", "executor_calls": 1}
{"case": "replan_ignores_kill_switch", "kill_switch": true, "status": "completed", "executor_calls": 2}
{"case": "secrets_empty_git", "passed": true, "reason": "workspace não é repo git — sem diff a escanear"}
```

Os dois achados reproduziam. Depois da correção:

```text
{"case": "continue_ignores_kill_switch", "kill_switch": true, "status": "cancelled", "executor_calls": 0}
{"case": "replan_ignores_kill_switch", "kill_switch": true, "status": "cancelled", "executor_calls": 1}
{"case": "secrets_empty_git", "passed": false, "reason": "3 possível(is) segredo(s) nas linhas adicionadas por este run: ..."}
```

## Problema

**K08.** O kill-switch e a policy só eram consultados no preflight
(`_preflight`, chamado por `execute`/`stream`/`admit`). Três caminhos voltavam
a chamar o executor sem perguntar nada:

1. `continue_run` — usado por `continue_governed` (`/loop` web, `/v1`,
   `/loop` do Telegram) e por `approve(..., executor=...)` (`bauer kernel
   approve`). Admissão e continuação são momentos diferentes, muitas vezes em
   threads diferentes.
2. retry e fallback de executor dentro de `_run_to_completion`.
3. replan: a transição `policy_check` existia só como estado; `_evaluate_policy`
   nunca era chamada.

**K07.** `SecretsGate` rodava `git diff HEAD`. Num `git init` sem primeiro
commit não há HEAD, o diff falha, a função devolvia `None` e o gate lia isso
como "não é repo git" e aprovava, sem nem listar os arquivos não versionados.
Qualquer erro operacional do git (binário ausente, timeout, falha de
`ls-files`) caía no mesmo lugar ou era engolido em silêncio, embora o gate
seja `critical=True`.

## Objetivo

- Kill-switch ligado depois da admissão bloqueia a execução e novas tentativas,
  incluindo continuação e replan. A policy mutável (regras, modo `locked`,
  orçamento zero) é reavaliada antes de cada tentativa, e aprovações ainda
  válidas são preservadas.
- Um segredo sintético num repositório vazio reprova o gate, e a falha do
  scanner não passa em silêncio.

## Escopo

- `BauerKernel._revalidar_governanca`: kill-switch e depois policy, na mesma
  ordem do preflight, chamado:
  - antes da primeira tentativa de `continue_run`;
  - antes de cada retry e de cada fallback;
  - no estado `policy_check` de cada replan, que deixa de ser decorativo.
- Persistência do contexto de governança da admissão (`operation` e
  `metadata` saneado) em `run.input["_kernel_governance"]`, para reconstruir o
  request de policy em `continue_run`, inclusive em outro processo. A chave
  nunca é repassada ao executor.
- Preservação de aprovação: `ask` na revalidação passa quando o run já tem um
  `ApprovalRecord` `approved` do Kernel para a mesma operação. Sem
  `ApprovalManager`, vale a retomada explícita (`resume`) de
  `waiting_approval`, registrada no run.
- `continue_governed` passa a informar `blocked_before_start`,
  `policy_reason` e `approval_id`. O `/loop` do Telegram usa isso para relatar
  `kill_switch`/`cancelled` em vez de `error`.
- `SecretsGate`: distingue não-repo, repo sem HEAD e erro operacional. Escaneia
  o index (`--cached`) e a árvore de trabalho contra HEAD, ou contra a árvore
  vazia quando não há HEAD, e também os untracked. Erro vira reprovação
  explícita.

## Caminhos que continuam um run (verificados)

| Caminho | Entrada no Kernel | Coberto por |
|---|---|---|
| `bauer run` (CLI) | `run_governed` → `execute` (retry/replan) | testes de retry/replan/fallback |
| `/loop` web | `admit` + `continue_governed` → `continue_run` | `test_web_loop_kill_switch_entre_admissao_e_worker` |
| `/loop` Telegram (`bauer/telegram_loop.py`) | `admit` + `continue_governed` | `test_telegram_loop_kill_switch_entre_admissao_e_worker` |
| `/v1/chat/completions` | `admit` + `continue_governed` | revalidação em `continue_run` (mesma porta) |
| `bauer kernel approve` / `approve(executor=…)` | `continue_run` | testes de aprovação preservada |
| Executor Agno (`agno_team_orchestrator`, `agno_workflows`) | `run_governed` → `execute` | mesma revalidação de retry/replan |
| scheduler, `agent_cmd`, `channel_base` | `run_governed` → `execute` | idem |

`stream()` não tem retry nem replan (escopo reduzido documentado). Seu
preflight já confere o kill-switch, e não há outra tentativa a revalidar.

## Decisões

1. **Onde revalidar.** A revalidação roda no estado `running`, imediatamente
   antes de chamar o executor, para continuação, retry e fallback. No replan,
   roda no estado `policy_check`, onde a trilha de auditoria sempre disse que
   ela acontecia. A primeira tentativa de `execute()` não revalida, porque
   acabou de passar pelo preflight. Revalidar ali só duplicaria o evento
   `policy.evaluated`, e um teste trava isso.
2. **Desfechos.** Kill-switch → `cancelled` (`runtime kill switch ativo`),
   igual ao preflight. `deny` → `failed` (`policy deny: …`) com a reserva de
   orçamento liberada. `ask` sem aprovação vigente → `waiting_approval` com um
   `ApprovalRecord` novo, como no preflight. O run não fica terminal, e o
   operador decide pelo mesmo `bauer kernel approve`.
3. **Aprovação preservada.** Uma aprovação humana já concedida a este run, para
   esta operação, continua valendo na continuação e nos replans. Se a policy
   passar a `deny`, o deny vence: a aprovação só é consultada para `ask`.
4. **Contrato não é reavaliado.** `TaskContract.requires_approval` é um snapshot
   proposital, lido uma vez para o agente não poder desligar a exigência no
   meio do run. Ele já foi conferido na admissão. A revalidação cobre o que é
   *mutável*: kill-switch, regras de policy e perfil de autonomia/orçamento.
5. **Orçamento.** A revalidação passa pelo mesmo `PolicyEngine.evaluate(
   "runtime.execute")` do preflight. Isso pega modo `locked`, orçamento diário
   zero e mudança de `max_parallel_runs`. A reserva do ledger é idempotente
   por `run_id` (contrato de K03/K04), então gasto cumulativo durante o run não
   é recalculado aqui. Ele continua limitado pelo `AutonomousBudget` do laço e
   pela liquidação por tentativa. Mudar isso exigiria redefinir o contrato do
   ledger, o que fica fora de escopo.
6. **Runs antigos.** Um run criado antes desta sprint não tem
   `_kernel_governance`. A reconstrução usa `operation="runtime.execute"` (o
   default do preflight), nunca uma operação mais branda.
7. **K07: "não é repo".** A decisão vem da presença de `.git` (diretório ou
   arquivo de worktree) no workspace ou acima, e não de mensagem de erro do
   git, que é localizada e depende do binário existir. Sem `.git`, o gate
   passa como antes. Com `.git`, toda falha do git é `ScannerIndisponivel` e
   o gate reprova.
8. **K07: base sem HEAD.** A árvore vazia é obtida por `git hash-object -t tree
   --stdin`, que funciona em repos sha1 e sha256. Um HEAD ilegível tratado
   como "sem HEAD" escaneia mais, nunca menos.
9. **K07: index e worktree.** O gate escaneia os dois. Um segredo staged e
   depois apagado só da árvore de trabalho ainda iria no próximo commit. Isso
   também vale com HEAD. As linhas repetidas entre os dois são deduplicadas.
10. **K07: untracked.** São listados com `-z`, para preservar caminhos
    especiais. Symlinks são pulados, porque o Git versiona o texto do link, e
    arquivos acima de 1 MB continuam pulados. Um arquivo novo ilegível
    reprova, em vez de ser ignorado em silêncio.

## Fora de escopo

- Findings de outros tracks: WEB-01/T04/T05, T01/T06/T08 e metadados de
  catálogo (track A); T02/T03/WEB-02/WEB-03 e CI Windows (track C).
- Contabilidade (K03/K04/K05/K10) e admissão paralela (K01/K09), já entregues
  na sprint 27.
- Interromper uma tentativa que já está em curso. O kill-switch dentro do laço
  continua sendo o `should_stop` de cada front-end, entre rodadas.

## Critérios de aceite

- [x] `continue_ignores_kill_switch` → `cancelled`, 0 chamadas ao executor.
- [x] `replan_ignores_kill_switch` → `cancelled`, 1 chamada (nenhum replan).
- [x] Retry e fallback não chamam o executor com o kill-switch ligado.
- [x] Policy alterada para `deny`/`ask`/`locked` entre tentativas é respeitada.
- [x] Aprovação já concedida não é pedida de novo na continuação nem no replan.
- [x] `/loop` web e `/loop` Telegram não entregam custódia ao laço quando o
      kill-switch é ligado entre a admissão e a thread.
- [x] `secrets_empty_git` → `passed: false`. Staged, worktree e untracked são
      escaneados sem HEAD.
- [x] Git ausente num repo, timeout e erro de `diff`/`ls-files`/`rev-parse`
      reprovam com motivo explícito. Fora de repo continua passando.
- [x] As regressões falham sem a correção (conferido revertendo `bauer/`).

## Plano de validação

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
uv run python docs/audits/2026-10-12/repro/bauer-audit-kernel-repro.py
```
