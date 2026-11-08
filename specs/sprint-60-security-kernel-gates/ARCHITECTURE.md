# ARCHITECTURE — Sprint 60

## K08: revalidação de governança por tentativa

```text
admit()/execute()
  └─ _open_run: persiste run.input["_kernel_governance"] = {operation, metadata}
  └─ _preflight: kill-switch → planning → policy_check (policy) → queued

continue_run(run_id)                      (outra thread/processo)
  └─ payload = run.input − _kernel_governance
  └─ _run_to_completion(revalidar_inicio=True)

_run_to_completion
  loop replan:
    start_run → running
    loop tentativas:
      se precisa_revalidar:  _revalidar_governanca  → cancelled | failed | waiting_approval
      precisa_revalidar = True          (toda próxima tentativa revalida)
      executor(...)
      falhou → retry (retrying → queued → running) | fallback → volta ao topo
    evaluating → gate reprovou → planning → policy_check
      └─ _revalidar_governanca            (antes: só a transição)
      └─ precisa_revalidar = False → queued → volta ao topo
```

`_revalidar_governanca(run, request, …)`:

1. `control.kill_switch_enabled()` → `_cancelar_se_nao_terminal` → resultado
   terminal.
2. `request` original (vindo de `execute`) ou `_request_de_governanca(run)`,
   reconstruído do store.
3. `_evaluate_policy` (publica `policy.evaluated`, consulta o BudgetManager
   para `runtime.execute`).
4. `allow` (ou ação desconhecida, igual ao preflight) → segue.
   `ask` + `_aprovacao_vigente` → segue. `ask` sem aprovação → transição para
   `waiting_approval` (legal a partir de `running` e de `policy_check`) +
   `_request_approval`. `deny` → `_fail_se_nao_terminal` (libera a reserva).

`_aprovacao_vigente(run, operation)`: no `ApprovalManager`, procura um
registro `approved` com `run_id` igual, `tool_name="kernel"` e mesma
`operation`. Sem manager, usa o marcador `approved_by_resume`, gravado por
`resume()` quando sai de `waiting_approval`.

Todas as transições usam os helpers existentes, que respeitam terminais
(`_transition` com `expected_status`, `fail_run_se_nao_terminal`). A
revalidação nunca sobrescreve um cancelamento concorrente.

### Front-ends

- `entry.continue_governed` conta as chamadas do executor e expõe
  `blocked_before_start`, `policy_reason` e `approval_id`.
- `telegram_loop._spawn_loop_thread`: com `blocked_before_start` e status
  `cancelled`, define o `stop_reason` como `kill_switch` ou `cancelled`. Assim
  o relatório e o registro (`stopped`) refletem o motivo real.
- `server.py` (`/loop` web) já traduz o run `cancelled` para `stopped`. O
  `/v1` responde 500 quando a continuação não é `ok`, como já fazia para
  falhas. Nenhum dos dois precisou mudar.

## K07: SecretsGate

```text
linhas_adicionadas(ws)
  ├─ sem .git no ws ou acima → None                    (gate passa: não é repo)
  ├─ git rev-parse --git-dir   falha → ScannerIndisponivel
  ├─ git rev-parse --verify --quiet HEAD^{commit}
  │     ok → base = HEAD
  │     não → base = git hash-object -t tree --stdin   (árvore vazia)
  ├─ git diff --cached base   + git diff base           (index + worktree)
  ├─ git ls-files -z --others --exclude-standard        (untracked, lidos)
  └─ qualquer falha (FileNotFoundError, timeout, exit≠0, OSError ao ler)
        → ScannerIndisponivel

SecretsGate.check
  ScannerIndisponivel → GateResult(passed=False, "não foi possível escanear …")
  None                → passa ("workspace não é repo git")
  texto               → secrets_scanner.scan → reprova/aprova como antes
```

Uma exceção do próprio `secrets_scanner.scan` continua chegando ao `Evaluator`,
que bloqueia por `critical=True`. Um teste trava isso.

## Arquivos

- `bauer/core/kernel/kernel.py`: `_GOVERNANCA`, `_open_run`, `continue_run`,
  `resume`, `_run_to_completion`, `_request_de_governanca`,
  `_aprovacao_vigente`, `_revalidar_governanca`.
- `bauer/core/kernel/entry.py`: `continue_governed`.
- `bauer/telegram_loop.py`: tradução de `stop_reason` na continuação barrada.
- `bauer/core/kernel/gates/secrets.py`: reescrita da coleta de diff.
- Testes: `tests/test_kernel_revalidacao_governanca.py`,
  `tests/test_kernel_continuacao_caminhos.py`,
  `tests/test_secrets_gate_repo_sem_commit.py`.
