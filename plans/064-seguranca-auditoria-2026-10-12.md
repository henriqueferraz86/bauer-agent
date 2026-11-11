# Plano 064 — Remediação de segurança da auditoria de 2026-10-12

**Status:** DONE (sprints 59–64; PRs #164, #165, #166 e #173). Resíduo: travamento intermitente de conexão SQLite no Windows, tratado no plano 068.

**Prioridade:** P1

**Esforço total estimado:** L, dividido em sprints 59–64

**Referências:** `docs/audits/2026-10-12/` (`REPORT.md`, `CORRECTIONS.md`,
`TOOLS-REVIEW.md`, `WEB-REVIEW.md`, `KERNEL.md`, `repro/`). A sprint 27 do
plano 060 concluiu só a parte funcional da auditoria. A parte de segurança
foi excluída de propósito e ficou registrada como "pendente" no
`CORRECTIONS.md`. Este plano fecha essa pendência.

## Problema e objetivo

Os scripts de prova herméticos da auditoria foram rodados no `master` em
2026-11-08 (commit `f5f643e`). Estes achados continuam reproduzindo:

| ID | Achado | Prova em 2026-11-08 |
|---|---|---|
| K08 | `continue` e `replan` chamam o executor com o kill-switch ligado | `continue_ignores_kill_switch` e `replan_ignores_kill_switch`, ambos com status `completed` |
| T01 | Cronjob de shell executa um comando que o `ShellRunner` bloqueia, sem aprovação | `CRON_SHELL_RESULT ... exit: 0` |
| T06 | `dry_run=True` grava memória e cria tarefa real no Kanban | `DRY_MEMORY_FILE True`, `DRY_KANBAN_RESULT Tarefa criada` |
| T08 | Extensão com `requires_approval=True` executa sem consultar o aprovador | `EXTERNAL_SECURITY approval False`, `EXTERNAL_EXECUTE done` |
| WEB-03 | `/models/switch` troca de local para cloud com `profile_set='local'` | `LOCAL_SWITCH 200 ... fake-cloud` |
| K07 | SecretsGate aprova uma chave detectável num repositório Git sem commits | `secrets_empty_git passed: true` |

Não foram confirmados em 2026-11-08, porque os scripts quebraram antes de
chegar a esses casos: WEB-01 (traversal em `session_id`), T02 (deny de
`network.http` ignorado), T03 (`Authorization` reenviado em redirect entre
origens), T04 (mídia fora do workspace), T05 (symlink na memória), WEB-02
(GET de stream de time inicia orquestração sem CSRF), WEB-08 (logout e
storage) e WEB-10 (limite de body aplicado depois do parser). Cada sprint
começa reproduzindo os seus casos e trata como aberto todo achado que não
tiver regressão comprovando a correção.

O contexto aumenta o risco: o Bauer ganhou `/loop` autônomo no Telegram
(sprint 44), aprovação remota (sprint 46) e o catálogo Agno com um conector
de escrita (plano 057). Kill-switch e aprovação precisam valer em todos os
caminhos.

Objetivo: todo achado de segurança da auditoria fica corrigido, com
regressão hermética que falharia sem a correção. Ou, se a correção exigir
mudar um contrato, fica com decisão explícita registrada na SPEC da sprint.

## Fora de escopo

- Novos recursos ou novas tools.
- Reescrever o motor de política. As correções reutilizam `PolicyEngine`,
  `ApprovalManager` e `ToolRouter` existentes.
- Achados funcionais que a sprint 27 já entregou.

## Sprints

Cada sprint segue o fluxo do `AGENTS.md`: SPEC → ARCHITECTURE → BACKLOG →
implementação → testes → revisão, numa branch `codex/sprint-XX-*`, com gates
completos antes do commit.

| Sprint | Escopo | IDs | Base |
|---|---|---|---|
| 59 | Confinamento de sessões, mídia e stores (lote 1) | WEB-01, T04, T05 | master |
| 60 | Kill-switch em continuação/replan e SecretsGate em repo vazio (lotes 3 e 5) | K08, K07 | master |
| 61 | Fronteiras de execução: cron, dry-run, aprovação de extensões e metadados de segurança do catálogo (lotes 2 e 6) | T01, T06, T08 + metadados de T07/T09/T10 | 59 |
| 62 | Fronteiras de rede e web: política HTTP, redirect, CSRF do stream de time, garantia `--local` (lote 2) | T02, T03, WEB-02, WEB-03 | master |
| 63 | Lifecycle web: logout/storage e limite de body antes do parser (lote 8) | WEB-08, WEB-10 | 60 |
| 64 | Estabilidade do CI no Windows: testes sensíveis a tempo e caminhos POSIX | — | 62 |

### Critérios de aceite (de `CORRECTIONS.md`)

- **Lote 1:**
  - O ataque a `.jsonl` externo é rejeitado antes de chegar ao LLM ou à run.
  - Bridges e outbox não recebem path externo.
  - A memória não altera alvo de symlink externo.
- **Lote 2:** todos os cenários T01, T02, T03, T06, T08, WEB-02 e WEB-03 são negados ou simulados antes do handler ou do transporte. Ações autorizadas continuam funcionando.
- **K08:** um kill-switch ligado depois da admissão bloqueia a execução e novas tentativas, inclusive continuação e replan.
- **K07:** um segredo sintético num repositório vazio reprova o gate, e falha do scanner não passa silenciosamente.
- **WEB-10:** o limite de body é aplicado antes do parser, incluindo multipart e chunked.
- **WEB-08:** logout limpa o storage do navegador.
- **Sprint 64:** o job de testes no Windows fica verde de forma estável.

### Condições de parada

Se uma correção exigir ampliar o privilégio de uma tool ou reinterpretar
`--local` ou `dry_run`, a decisão é registrada na SPEC. Nenhuma guarda é
silenciada para fazer o teste passar.

## Validação obrigatória por sprint

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
```

O script de prova correspondente em `docs/audits/2026-10-12/repro/` deve
mostrar o caso corrigido. Os scripts que hoje quebram antes de chegar aos
casos (`bauer_audit_tools_probe_extra.py`, que lê de `/tmp`, e
`bauer_web_repro2.py` e `bauer_web_crossreview_kernel.py`, que esperam uma
thread que não sobe) são consertados na sprint que cobre os casos deles.
