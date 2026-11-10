# SPEC — Sprint 71: higiene (plano 066)

Empilhada sobre a sprint 70. São três pendências pequenas, registradas nos
BACKLOGs das sprints 59–68 e no índice de planos.

## Problema

1. **Nomes de tools do Kanban no prompt.** O plano 043 multiplexou as 9 tools
   `kanban_*` em `kanban_read` e `kanban_write`. Três textos do `agent.py`
   continuaram citando tools que não existem mais:
   - o `KANBAN_WORKER_GUIDANCE` citava `kanban_show`, `kanban_complete`,
     `kanban_block`, `kanban_comment` e `kanban_create`;
   - a dica do `/develop` citava `kanban_list` e `kanban_complete`;
   - o bloco do Task Ledger citava `kanban_update_task` e `kanban_mark_done`,
     que nunca existiram.

   O modelo chamava nomes inexistentes e gastava rodadas com erro de tool.
2. **`SqliteSessionStore.delete` por prefixo.** Os vetores das mensagens
   usam `source_id = "{session_id}:{role}:{idx}"`. Apagar a sessão `a`
   removia por `LIKE 'a:%'`, o que também atingia a sessão `a:b`. IDs de
   sessão aceitam `:` (`tg:123`, `agent:x:1`), e o `LIKE` do SQLite ignora
   caixa, então `a` também pegava `A`. A busca vetorial tinha o mesmo defeito
   no parse: `split(":", 2)` lia `tg:42:user:0` como sessão `tg`, e as sessões
   de canal nunca apareciam na busca semântica.
3. **Plano 049.** O documento nunca chegou ao `master`. O índice citava "049"
   como dependência do 050, mas não tinha linha nem arquivo para ele.

## Decisões

- **Kanban:** o prompt cita só `kanban_read(mode=...)` e
  `kanban_write(action=...)`.
  - O worker não pode criar cards: o gate de `kanban_write` nega
    `create`/`unblock`/`link` no contexto `worker`. Por isso, o passo de
    follow-up do protocolo passa a pedir um comentário
    (`action="comment"`) para o supervisor criar o card. Antes, ele mandava o
    worker fazer algo que a política nega.
  - Há um teste de regressão que varre os literais de string do `agent.py`:
    todo nome `kanban_*` citado precisa existir no `ToolRouter`, e toda ação
    do protocolo do worker precisa ser permitida para o worker.
- **Delete exato:**
  - `VectorStore.list_source_ids(prefix=...)` só **estreita** a consulta;
  - a identidade é conferida em Python, com o parse pela direita
    (`rsplit(":", 2)`, `idx` numérico);
  - o mesmo parse (`_parse_vector_source_id`) corrige a busca vetorial;
  - o `except: pass` do delete virou `log_suppressed`.
- **049:** o documento foi procurado:
  - na branch citada, que não existe mais no remoto;
  - em `git log --all -- 'plans/049*'` e em `git rev-list --all --objects`;
  - no checkout principal.

  Ele não foi encontrado. O índice ganha uma linha 049, com o código entregue
  (`13093dd`, merge `c2d3e4e`) e o registro de que o documento não existe
  mais. A nota da Rodada 7 é reescrita para dizer que a pendência foi
  reconciliada.

## Fora de escopo

- Reescrever o protocolo do worker além dos nomes e da ação de follow-up.
- Migrar vetores antigos (o formato do `source_id` não muda).

## Critérios de aceite

- [x] Nenhum nome `kanban_*` inexistente em literais do `agent.py`. O teste
  falha com o código anterior.
- [x] Apagar `a`, `tg:1` ou `agent:x` mantém os vetores de `a:b`, `A`,
  `tg:12`, `tg:1:extra` e `agent:x:1`. O teste falha com o código anterior.
- [x] A busca vetorial encontra sessões com `:` no ID.
- [x] `plans/README.md` sem dependência pendente para o 049.

## Plano de validação

`tests/test_sprint71_hygiene.py` e os gates completos do plano 066.
