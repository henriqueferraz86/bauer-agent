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

## 5.1 ⛔ O backend SQLite NÃO está pronto para ser default (medido)

Depois de construir o switch único, rodei o experimento decisivo: **a suíte
completa com `task_backend: sqlite`**. Resultado: **falha**. Isso invalida
qualquer plano de "ligar o sqlite" — inclusive para usuários novos, que não
têm dados a perder mas herdariam um backend que **quebra o dispatcher**.

| Experimento | Falhas |
|---|---|
| default `sqlite` (como estava) | **25** |
| default `sqlite` + fix #10-D | **30** (o fix removeu os crashes e expôs falhas mais fundas) |

Concentradas no **mainline**: `task_dispatcher` (10), ferramentas kanban
(10), `desktop_api` (4), `kanban_server` (2), `execution_engine` (2),
`migrate`, `server_extended`.

### ✅ #10-D — Schema não era garantido em 5 dos 8 métodos (CORRIGIDO)
`get_task`, `update_task_status`, `update_task_metadata`, `add_task_comment`
e `get_project_info` não chamavam `_ensure_schema` → `sqlite3.OperationalError:
no such table: tasks` num board novo. Nunca apareceu porque a superfície gen-2
sempre cria tarefa antes (só `add_task` garantia). **Corrigido no `_connect()`**
— um lugar só, idempotente, imune a esquecer num método futuro. 4 testes de
regressão em `test_workspace_manager_sqlite.py`.

### ⚠️ CORREÇÃO DA ANÁLISE: "30 falhas" NÃO media o backend

A leitura inicial deste documento ("o sqlite quebra o dispatcher") **não estava
demonstrada**. Perseguindo as falhas até a raiz, a maior parte vem do
**instrumento de medição**, não do backend:

1. **Isolamento de teste.** `tests/conftest.py` define `BAUER_HOME` uma vez
   por SESSÃO. O markdown isolava sozinho (cada teste tem seu `tmp_path`), mas
   o sqlite guarda tarefas num **board global** → todos os testes dividiam
   `boards/default/kanban.db` e o estado vazava. Corrigido com board único por
   teste (md5 do nodeid). Efeito: **30 → 25**, `wave6_tools` 10 → 4.
2. **Testes presos ao markdown.** `test_task_dispatcher.py` (e outros)
   importam `WorkspaceManager` **diretamente** e criam tarefas no `TASKS.md`,
   enquanto o código sob teste resolve o backend pela factory → lê o sqlite
   vazio. Daí `Tarefa '001' nao encontrada`. É o teste montando cenário num
   backend e exercitando o outro.

**Conclusão honesta: ainda NÃO sabemos a saúde real do backend sqlite.** Para
saber, falta portar os testes backend-agnósticos (dispatcher, wave6_tools,
desktop_api) para montar cenário via `get_workspace_manager()`. É mecânico e
seguro (com default `markdown` seguem passando igual), e transforma a suíte
num instrumento capaz de validar **os dois** backends. **Sem isso, qualquer
decisão de virar é chute** — inclusive para usuários novos.

### ✅ #10-E — Semântica de `Task.metadata` divergia (CORRIGIDO)
O sqlite reconstrói `metadata` **replayando eventos**. Duas consequências que
quebram o dispatcher:
1. **Vaza campo interno de evento** para o dict do usuário: `status_to`,
   `author`, `text`, `title`, `last_error` aparecem em `Task.metadata`.
2. **Chaves nunca somem.** O dispatcher precisa *remover* `claim_id` ao fazer
   reclaim; um log append-only não expressa remoção. Falha real observada:
   `assert "claim_id" not in ready.metadata`.

**Corrigido — e sem remodelar nada.** Eu temia um gap de design (metadata como
estado vs. projeção de eventos), mas o lado da **escrita já estava certo**:
`update_task_metadata` já grava `None` como `""` num evento `metadata_set`,
com o comentário explícito "Markdown deletion semantics". O bug era só na
**leitura** (`_to_task`), que não honrava esse contrato. Duas regras
espelhando o markdown, ambas reusando o que ele já definia:

1. **Filtrar pelo whitelist `_META_KEYS`** — o mesmo do markdown. Mata o
   vazamento (`status_to`/`author`/`text`/`title` não estão nele;
   `claim_id`/`lane`/`last_error`/`orchestration_*` estão).
2. **Valor vazio = chave removida** — completa a deleção que a escrita já
   pretendia (o `del` que `_upsert_metadata` faz no markdown). É isso que
   permite ao dispatcher SOLTAR o `claim_id` no reclaim.

Não precisou tocar no dispatcher nem no schema.

## 6. Rota recomendada (faseada)

1. ~~**Fechar #10-A** — ensinar `read_tasks_md` a parsear a linha `comment:`.~~
   ✅ **FEITO neste PR** (a migração agora preserva comentários).
2. ~~**Decidir #10-B**~~ ✅ **DECIDIDO: perda aceita** (sem código).
3. ~~**Alinhar #10-C**~~ ✅ **FEITO neste PR** (default unificado em `READY`).
4. ~~**Cutover faseado por call site**~~ ⚠️ **CORRIGIDO — essa rota estava
   ERRADA.** As duas gerações leem de fontes diferentes (arquivo vs. board
   SQLite): apontar um consumidor isolado para o sqlite faria ele ler uma base
   **vazia** enquanto os outros seguem no markdown — isso **cria** split-brain
   em vez de curar.
   **Rota correta (implementada): switch ÚNICO.** Todos os call sites (36 em
   15 arquivos) resolvem o backend por `workspace_manager_factory.
   get_workspace_manager()`, guiado por `agent.task_backend`
   (`markdown` default | `sqlite`). A virada move **todos de uma vez**,
   e só depois de `bauer kanban-migrate`. As APIs públicas das duas classes
   são **idênticas** (8 métodos, zero gap medido), então a troca é fiel.
5. 🔴 **Fechar #10-E (semântica de metadata)** — PRÉ-REQUISITO DURO. Enquanto
   `Task.metadata` do sqlite vazar campos de evento e não permitir remoção de
   chave, o dispatcher não funciona nele. Só depois disso a suíte pode fechar
   verde com `task_backend: sqlite`.
6. **Só então virar o default** — e o critério de aceite é objetivo: **suíte
   completa verde com `sqlite`**. Repetir o experimento da seção 5.1.
7. **Congelar o gen 1** — depois que todo mundo lê/escreve no kanban_db,
   `TASKS.md` vira projeção read-only (o `WorkspaceManagerSqlite` já
   regenera o md como snapshot humano via `_regenerate_view`).

### E os usuários NOVOS?
Tentador dar `sqlite` a quem instala agora ("não tem dados a perder"). **Não
faça** enquanto #10-E estiver aberto: o risco não é de dados, é de
**funcionamento** — o novato herdaria o dispatcher quebrado. Usuário novo
segue em `markdown` até a suíte fechar verde com sqlite; aí o default passa a
ser `sqlite` para todos os novos (existentes migram quando quiserem).

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
