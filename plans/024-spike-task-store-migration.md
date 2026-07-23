# Plano 024 — Migração do task-store (2 gerações): estado, riscos e rota

**Auditoria 023, achado #10.** Começou como spike read-only; evoluiu para 5
correções mergeadas. Este documento é a **fonte de verdade** do assunto — quem
retomar o tema começa por aqui, sem precisar do contexto da conversa original.

**Estado em 2026-07-23:** #10-A a #10-E fechados (PRs #74/#75/#76 mergeados) e
a suíte agora **mede os dois backends** (port dos testes, §6).

**O backend SQLite está essencialmente saudável:** suíte completa com
`task_backend: sqlite` → **1 falha** (era 25-30 quando medíamos o
instrumento). Resta **exatamente um** gap arquitetural: **#10-F — isolamento
por projeto** (§6.1).

`agent.task_backend` segue em `markdown`. **Não vire ainda** — quem usa mais
de um projeto veria as tarefas de todos misturadas. #10-F precisa de uma
decisão sua sobre convenção de board.

---

## 1. TL;DR

O Bauer tem **duas gerações de task-store que não compartilham estado**. A
infraestrutura da virada (kernel SQLite + drop-in + `bauer kanban-migrate` +
switch único) **está pronta e ligada**. O que falta não é código de produto:
é tornar a suíte capaz de **validar os dois backends** (§6).

**Rumo decidido pelo usuário:** unificar no `kanban_db` (gen 2) — backend
superior (CAS, WAL, multi-board) e já usado pela superfície nova.

**Critério de aceite objetivo da virada:** suíte completa **verde** com
`agent.task_backend: sqlite`. Hoje esse experimento ainda não é conclusivo.

---

## 2. As duas gerações

| | **Gen 1 (legado, default)** | **Gen 2 (kernel SQLite)** |
|---|---|---|
| Módulo | `workspace_manager.py` → `TASKS.md` | `kanban_db.py` + `workspace_manager_sqlite.py` |
| Formato | Markdown (texto) | SQLite (WAL + CAS, `~/.bauer/kanban/boards/<slug>/kanban.db`) |
| Status | 6 (TODO/READY/IN_PROGRESS/DONE/BLOCKED/FAILED) | 9 (+ triage/review/archived) |
| Atomicidade | reescrita de arquivo | BEGIN IMMEDIATE + compare-and-swap |
| Multi-board | não | sim (isolado por projeto) |
| Sidecar | `kanban_store.py` (SQLite p/ events/runs, sem acoplar o md ao SQLite) | events/runs nativos na própria db |
| `Task` dataclass | `workspace_manager.Task` | **o MESMO** (importado do gen 1) |

`WorkspaceManagerSqlite` é um **drop-in**: mesma API pública e o mesmo `Task`.
Medido: **8 métodos públicos, zero gap** nos dois sentidos
(`init_project`, `add_task`, `list_tasks`, `get_task`, `update_task_status`,
`update_task_metadata`, `add_task_comment`, `get_project_info`).

## 3. Quem usa cada uma (o "split-brain")

- **Gen 1 (TASKS.md)** — o **mainline**: `agent.py`, `channel_base.py`,
  `task_dispatcher.py`, `execution_engine.py`, `kanban_server.py`,
  `desktop_api.py`, `ops_status.py`, `automation_scheduler.py`,
  `spec_wizard.py`, `tools/kanban.py`, e os comandos
  `dispatch`/`orchestrate`/`project`/`task`.
- **Gen 2 (kanban_db)** — a superfície **"Wave 2"**: `cli.py`
  (`kanban-migrate`, `boards`), `boards_cmd.py`, `daemon.py`,
  `kanban_decompose.py`, `kanban_specify.py`, `kanban_swarm.py`.

**Consequência:** uma task criada pelo agente é **invisível** ao swarm, e
vice-versa, até rodar `bauer kanban-migrate`. É o problema que a virada cura.

## 4. Infraestrutura pronta

- ✅ Kernel SQLite completo e testado (`test_kanban_db.py`).
- ✅ Drop-in `WorkspaceManagerSqlite` (`test_workspace_manager_sqlite.py`).
- ✅ Migração idempotente `migrate_tasks_md` + CLI `bauer kanban-migrate`
  (`--dry-run`, `--board`) — `test_kanban_migration.py`.
- ✅ **Switch único** `workspace_manager_factory.get_workspace_manager()`
  guiado por `agent.task_backend` — **36 call sites em 15 arquivos** roteados
  (`test_workspace_manager_factory.py`).
- ✅ Sidecar `kanban_store` p/ history sem acoplar o md ao SQLite.

## 5. Os 5 achados — todos fechados

### ✅ #10-A — Comentários não sobreviviam à migração
`WorkspaceManager.add_task_comment` escreve `comment: <iso> | <autor> | <texto>`,
mas `read_tasks_md` só reconhecia **bullets `- `** — formato que a API real
**nunca** produz. O comentário **vazava para a `description`**.
**Fix:** `read_tasks_md` reconhece a linha `comment:` em qualquer região do
bloco, preservando só o texto (`split("|", 2)` mantém `|` do próprio texto).
Os testes de migração existentes não pegaram isso porque escreviam markdown à
mão no formato bullet — a caracterização ponta-a-ponta (pelas 2 APIs reais) é
que expôs.

### ✅ #10-B — Status lossy: **perda aceita** (decisão do usuário)
`kanban_db` tem 9 status; a API drop-in expõe 6. Os nativos
`triage`/`review`/`archived` colapsam em `TODO`/`IN_PROGRESS`/`DONE`.
Sem código — é o comportamento atual, pinado por
`test_native_db_statuses_collapse_to_md_vocab` como **característica aceita**.
Os 3 nativos são usados só por swarm/specify; o mainline nunca precisou dessa
granularidade. Evoluir para "ensinar os 9 status aos legados" fica para quando
houver necessidade real.

### ✅ #10-C — Default de `add_task` divergia
md = `READY`, sqlite = `TODO`. **Todos** os call sites que omitem status são do
mainline e dependem de `READY`; a superfície gen-2 passa status explícito.
**Alinhado em `READY`** — o sqlite passou a casar com o legado (o inverso
mudaria o comportamento de todo mundo hoje). Pinado em
`test_default_status_is_aligned`.

### ✅ #10-D — Schema não era garantido em 5 dos 8 métodos
`get_task`, `update_task_status`, `update_task_metadata`, `add_task_comment` e
`get_project_info` não chamavam `_ensure_schema` → `no such table: tasks` num
board novo. Não aparecia porque a superfície gen-2 sempre cria tarefa antes.
**Fix no `_connect()`** — um lugar só, idempotente, imune a esquecer num
método futuro. 4 testes de regressão.

### ✅ #10-E — Semântica de `Task.metadata` divergia
O sqlite reconstrói `metadata` replayando eventos: (1) vazava campo **interno**
de evento (`status_to`/`author`/`text`/`title`) para o dict do usuário, e
(2) chaves **nunca sumiam** — mas o dispatcher precisa *remover* `claim_id` no
reclaim.
**Fix sem remodelar nada.** A **escrita já estava certa** (`update_task_metadata`
já grava `None` como `""` num evento `metadata_set`, com o comentário
"Markdown deletion semantics"); o bug era a **leitura** (`_to_task`) não honrar
o contrato. Duas regras reusando o que o markdown já definia:
1. **Filtrar pelo whitelist `_META_KEYS`** — mata o vazamento
   (`claim_id`/`lane`/`last_error`/`orchestration_*` estão no whitelist).
2. **Valor vazio = chave removida** — completa a deleção que a escrita já
   pretendia (o `del` do `_upsert_metadata`).

Não tocou no dispatcher nem no schema.

## 6. ⚠️ O que falta: a suíte não sabe medir o backend SQLite

Rodei a suíte completa com `task_backend: sqlite` e reportei
*"o sqlite quebra o dispatcher — 30 falhas"*. **Essa conclusão estava errada.**
Perseguindo as falhas até a raiz, a maior parte vem do **instrumento de
medição**:

1. **Isolamento.** `tests/conftest.py` define `BAUER_HOME` uma vez por
   **sessão**. O markdown isolava sozinho (cada teste tem seu `tmp_path`), mas
   o sqlite usa board **global** → todos os testes dividiam
   `boards/default/kanban.db` e o estado vazava.
   **Corrigido** (board único por teste, md5 do nodeid — determinístico, pois
   `hash()` varia com `PYTHONHASHSEED`). Efeito medido: **30 → 25**,
   `wave6_tools` 10 → 4, `desktop_api` 4 → 1.
2. **Testes presos ao markdown.** `test_task_dispatcher.py` (e outros)
   importam `WorkspaceManager` **diretamente** e criam tarefas no `TASKS.md`,
   enquanto o código sob teste resolve pela factory e lê o sqlite vazio → daí
   `Tarefa '001' nao encontrada`. É o teste montando cenário num backend e
   exercitando o outro.

### ✅ RESOLVIDO — port feito, medição agora é conclusiva

Portados 9 arquivos para montar cenário via `get_workspace_manager()`:
`test_task_dispatcher`, `test_wave6_tools`, `test_kanban_server`,
`test_execution_engine`, `test_server_extended`, `test_desktop_api`,
`test_agent`, `test_automation_scheduler`, `test_channel_base`.

Deliberadamente **NÃO** portados (usam `WorkspaceManager` de propósito):
`test_workspace_manager.py` (testa o markdown em si),
`test_task_store_parity.py` (compara as duas gerações),
`test_kanban_store.py` (sidecar).

Cuidado especial em `test_desktop_api.py`: havia um
`patch("bauer.workspace_manager.WorkspaceManager")` que viraria **bug
silencioso** — como `desktop_api` passou a importar a factory, o patch não
interceptaria mais nada e o teste exercitaria código real achando-se mockado.
Redirecionado para `patch("bauer.workspace_manager_factory.get_workspace_manager")`.

### 📊 Evolução das medições (o histórico importa)

| Medição | Falhas | Media o quê |
|---|---|---|
| 1ª — sqlite cru | 25 | instrumento |
| 2ª — + #10-D (schema) | 30 | instrumento |
| 3ª — + #10-E (metadata) | 30 | instrumento |
| 4ª — + isolamento de board | 25 | instrumento (parcial) |
| **5ª — + port dos testes** | **1** | **o BACKEND** |

**Conclusão: o backend SQLite está essencialmente saudável.** Das ~30 falhas
originais, 5 eram defeitos reais (#10-A a #10-E, corrigidos), o resto era o
instrumento — e resta **exatamente um** gap arquitetural: #10-F.

## 6.1 🔴 #10-F — Isolamento por projeto se perde no SQLite (ABERTO)

Única falha remanescente:
`test_server_extended::test_kanban_endpoint_reads_active_project_board`.

O endpoint kanban deve mostrar as tarefas do **projeto ativo**; sob sqlite
mostra **as de todos os projetos misturadas**:
```
assert 'tarefa da raiz do serve' not in ['tarefa do projeto bauerinvest',
                                          'tarefa da raiz do serve']
```

**Causa:** no markdown o isolamento vem de graça do **caminho**
(`<projeto>/TASKS.md`). No sqlite vem do **board** — mas
`get_workspace_manager(workspace)` recebe só o workspace e **não deriva
board**, então todo projeto cai no board ativo/default.

**Complicação que impede o fix óbvio:** derivar o board do workspace conflita
com o `bauer kanban-migrate`, que grava no board **ativo**. Se a factory
passar a ler um board derivado, o usuário migra os dados para um lugar e o
sistema lê de outro — as tarefas **somem da vista**. Corrigir exige alinhar
migrate e factory na MESMA convenção de board.

**É decisão sobre onde os dados moram — precisa de aprovação, não de código
apressado.** Duas rotas:
  a) **Board derivado do workspace** (isolamento igual ao markdown). Exige que
     `kanban-migrate` use a mesma derivação por default.
  b) **Manter board ativo** e aceitar que projetos compartilham board —
     regressão de comportamento frente ao markdown; só aceitável se
     multi-projeto não for usado de verdade.

**Enquanto #10-F estiver aberto, não vire o default** — quem usa mais de um
projeto veria as tarefas misturadas.

## 7. Como virar (quando chegar a hora)

```bash
bauer kanban-migrate --dry-run     # confere o que seria migrado
bauer kanban-migrate               # idempotente
# então: agent.task_backend: sqlite no config.yaml
```

Voltar é trivial: reverter a flag — o `TASKS.md` permanece intacto.

**Ordem obrigatória:** migrar **antes** de virar a flag. Sem isso o store novo
está vazio e as tarefas *parecem* sumir.

**Critério de aceite:** suíte completa verde com `sqlite` (repetir o
experimento da §6 depois do port dos testes).

### E os usuários NOVOS?
Tentador dar `sqlite` a quem instala agora ("não tem dados a perder"). **Não
faça ainda** — o risco não é de dados, é de **funcionamento**, e ainda não
medimos. Usuário novo segue em `markdown` até a suíte fechar verde com sqlite;
aí o default passa a `sqlite` para novos (existentes migram quando quiserem).

## 8. Alternativa (se NÃO virar)

Manter o gen 1 como canônico torna `kanban_db` + `swarm`/`specify`/`decompose`/
`daemon` uma superfície **órfã** — teriam de ser aposentados ou passar a
escrever também no `TASKS.md`. É mais trabalho e joga fora o backend superior;
**não recomendado**, mas coerente se a superfície Wave 2 não estiver em uso
real.

## 9. Entregue (PRs mergeados)

| PR | Conteúdo |
|---|---|
| #74 | Spike + characterization (`test_task_store_parity.py`) + #10-A/B/C |
| #75 | Switch único (factory + `agent.task_backend`) + 36 call sites + #10-D |
| #76 | #10-E + isolamento de board por teste |

**Testes criados:** `test_task_store_parity.py` (paridade direta da API,
fidelidade da migração ponta-a-ponta pelas 2 APIs reais, round-trip lossless
dos 6 status do md, assimetria lossy dos nativos) e
`test_workspace_manager_factory.py` (default conservador, override, valor
inválido, kwargs, paridade de API).

## 10. Lições registradas (erros meus, corrigidos)

Duas conclusões que afirmei e depois desmenti — ficam aqui para não se
repetirem:

1. **"Cutover faseado por call site"** — estava **errado**. As gerações leem de
   fontes diferentes; apontar um consumidor isolado para o sqlite o faria ler
   base vazia enquanto os outros seguem no markdown: **cria** split-brain em
   vez de curar. Rota correta: **switch único**.
2. **"O sqlite quebra o dispatcher (30 falhas)"** — **não estava demonstrado**.
   A maior parte era isolamento de teste + testes presos ao markdown (§6).

Moral prático: ao medir um backend alternativo, **valide primeiro se a suíte
consegue medi-lo** — senão você mede o instrumento, não o sistema.
