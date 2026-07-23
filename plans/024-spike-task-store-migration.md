# Spike #10 — Migração do task-store (2 gerações): estado, riscos e rota

**Auditoria 023, achado #10.** Read-only + characterization tests. **Nada foi
migrado nem alterado em produção.** Objetivo: dar fatos para você decidir o
rumo da virada (qual geração vira canônica e como cortar sem perder dados).

Branch: `spike/10-task-store-migration` · Teste novo:
`tests/test_task_store_parity.py` (10 pass + 1 xfail que documenta um defeito).

---

## 1. TL;DR / Recomendação

O sistema está **meio-migrado, e as duas gerações NÃO compartilham estado**.
A infraestrutura de virada (kernel SQLite + drop-in + `bauer kanban-migrate`)
**já existe e está ligada** — mas há **3 bloqueadores concretos** antes de
poder virar o default com segurança. **Não vire o default ainda.**

**Rota recomendada:** virada **faseada** para o `kanban_db` (gen 2) como
canônico — é o backend superior (CAS, WAL, multi-board, 9 status, já usado
pela superfície nova) — **depois** de fechar os 3 bloqueadores abaixo. Estimo
os bloqueadores em ~1 branch pequena cada.

---

## 2. As duas gerações

| | **Gen 1 (legado, default)** | **Gen 2 (kernel SQLite)** |
|---|---|---|
| Módulo | `workspace_manager.py` → `TASKS.md` | `kanban_db.py` + `workspace_manager_sqlite.py` |
| Formato | Markdown (texto) | SQLite (WAL + CAS, `~/.bauer/kanban/boards/<slug>/kanban.db`) |
| Status | 6 (TODO/READY/IN_PROGRESS/DONE/BLOCKED/FAILED) | 9 (+ triage/review/archived) |
| Atomicidade | reescrita de arquivo | BEGIN IMMEDIATE + compare-and-swap |
| Multi-board | não | sim (isolado por projeto) |
| Sidecar | `kanban_store.py` (SQLite p/ events/runs, sem tornar o md dependente de SQLite) | events/runs nativos na própria db |
| `Task` dataclass | `workspace_manager.Task` | **o MESMO** (importado do gen 1) |

O `WorkspaceManagerSqlite` foi desenhado como **drop-in**: mesma API pública
(`add_task`/`list_tasks`/`get_task`/`update_task_status`/`update_task_metadata`
/`add_task_comment`) e o **mesmo** `Task`. A paridade direta da API está
**verde** nos testes (`TestDirectApiParity`).

## 3. Quem usa cada uma (o "split-brain")

- **Gen 1 (TASKS.md)** — o **mainline**: `agent.py`, `channel_base.py`
  (gateways), `task_dispatcher.py`, `execution_engine.py`, `kanban_server.py`,
  `desktop_api.py`, `ops_status.py`, `automation_scheduler.py`, `spec_wizard.py`,
  `tools/kanban.py`, e os comandos `dispatch`/`orchestrate`/`project`/`task`.
- **Gen 2 (kanban_db)** — a **superfície nova "Wave 2"**: `cli.py`
  (`kanban-migrate`, `boards`), `boards_cmd.py`, `daemon.py`,
  `kanban_decompose.py`, `kanban_specify.py`, `kanban_swarm.py`.

**Consequência:** uma task criada pelo agente (TASKS.md) é **invisível** ao
swarm (kanban_db), e vice-versa, até rodar `bauer kanban-migrate`. Não há um
switch único que aponte todo mundo para o mesmo store.

## 4. O que já está pronto (não precisa construir)

- ✅ Kernel SQLite completo e testado (`test_kanban_db.py`).
- ✅ Drop-in `WorkspaceManagerSqlite` com API idêntica (`test_workspace_manager_sqlite.py`).
- ✅ Migração idempotente `migrate_tasks_md` + CLI `bauer kanban-migrate`
  (com `--dry-run`, `--board`) — `test_kanban_migration.py`.
- ✅ Sidecar `kanban_store` p/ history sem acoplar o md ao SQLite.

## 5. Bloqueadores concretos (achados deste spike)

### ✅ #10-A — Comentários NÃO sobreviviam à migração (CORRIGIDO neste PR)
`WorkspaceManager.add_task_comment` escreve a linha
`comment: <iso> | <autor> | <texto>` dentro do bloco. Mas
`kanban_migration.read_tasks_md` só reconhecia comentários como **bullets
Markdown `- `** (formato que a API real **nunca** produz). Resultado: o
comentário **vazava para a `description`** da task migrada.
**Corrigido:** `read_tasks_md` agora reconhece a linha `comment:` em qualquer
região do bloco (antes do parse de metadata/prosa), preservando só o texto
(`split("|", 2)` mantém `|` que exista no próprio comentário).
`test_comment_survives_migration` agora passa de verdade (o `xfail` foi
removido). Os testes de migração existentes não pegaram isso porque escreviam
markdown à mão no formato bullet.

### ✅ #10-B — Mapeamento de status é lossy (DECIDIDO: perda aceita)
**Decisão do usuário: aceitar a perda (opção b).** Não exige código — é o
comportamento atual, e fica pinado pelo teste
`test_native_db_statuses_collapse_to_md_vocab` como característica aceita, não
bug. Os 3 status nativos são usados só pela superfície swarm/specify; o
mainline nunca precisou dessa granularidade. Evoluir para "ensinar os 9
status aos consumidores legados" fica para quando houver necessidade real.
Detalhe técnico abaixo.


`kanban_db` tem 9 status; a API drop-in expõe 6. Os nativos
`triage`/`review`/`archived` — que `swarm`/`specify` SETAM — **colapsam** ao
serem lidos pela API compatível: `triage→TODO`, `review→IN_PROGRESS`,
`archived→DONE` (pinado em `test_native_db_statuses_collapse_to_md_vocab`).
Um consumidor legado não os distingue. Ao virar, é preciso decidir: ou os
consumidores legados aprendem os 9 status, ou aceita-se a perda de resolução.

### ✅ #10-C — Default de status divergia (CORRIGIDO neste PR)
`WorkspaceManager.add_task` default = `READY`; `WorkspaceManagerSqlite.add_task`
default era `TODO`. Todos os call sites que **omitem** status são do mainline
(`agent.py`, `task_cmd`, `execution_engine`, `kanban_server`, `spec_wizard`,
`automation_scheduler`, `tools/kanban`, `migrate`, `orchestrate`) e dependem
de `READY`; a superfície gen-2 passa status explícito. **Alinhado em `READY`**
(o sqlite passou a casar com o legado — não o contrário, que mudaria o
comportamento de todo mundo hoje). A troca por call site agora é drop-in de
verdade. Pinado em `test_default_status_is_aligned`.

## 6. Rota recomendada (faseada)

1. ~~**Fechar #10-A** — ensinar `read_tasks_md` a parsear a linha `comment:`.~~
   ✅ **FEITO neste PR** (a migração agora preserva comentários).
2. ~~**Decidir #10-B**~~ ✅ **DECIDIDO: perda aceita** (sem código).
3. ~~**Alinhar #10-C**~~ ✅ **FEITO neste PR** (default unificado em `READY`).
4. **Cutover faseado por call site** — trocar `WorkspaceManager` →
   `WorkspaceManagerSqlite` começando pelos pontos de baixo risco
   (`ops_status`, `desktop_api` read-only) e terminando no mainline
   (`agent`/`dispatcher`). O `Task` idêntico torna cada troca mecânica.
5. **Congelar o gen 1** — depois que todo mundo lê/escreve no kanban_db,
   `TASKS.md` vira projeção read-only (o `WorkspaceManagerSqlite` já
   regenera o md como snapshot humano via `_regenerate_view`).

## 7. Alternativa (se NÃO virar)

Se a decisão for **manter o gen 1** como canônico: então `kanban_db` +
`swarm`/`specify`/`decompose`/`daemon` viram a superfície "órfã" — precisam ou
ser aposentados, ou passar a escrever também no TASKS.md. Isso é mais trabalho
e joga fora o backend superior; **não recomendo**, mas é uma saída coerente se
a superfície Wave 2 não estiver em uso real.

## 8. O que este spike entregou

- `tests/test_task_store_parity.py` — 11 characterization tests (10 pass +
  1 xfail documentando #10-A). Cobrem: paridade direta da API, fidelidade da
  migração ponta-a-ponta (via as 2 APIs reais), round-trip lossless dos 6
  status do md, e a assimetria lossy dos status nativos.
- Este documento. **Nenhuma mudança de produção.**

**Decisão pendente com você:** aprovar a rota faseada (seção 6) — e, em
particular, o rumo do #10-B (alinhar status vs. aceitar perda). Com o "vai",
o primeiro passo executável é o #10-A (defeito de fidelidade), numa branch
pequena própria.
