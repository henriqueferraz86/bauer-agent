# Plan 017 (SPIKE): Memória com loop fechado — consolidação episódica→semântica + feedback que age

> **Executor instructions**: Plano de DESIGN/SPIKE — entregável é spec, não
> código. Responda às perguntas com `file:line`, pare nos STOP conditions.
> Ao concluir, atualize `plans/README.md`.
>
> **Drift check (run first)**: `git log --oneline -5 -- bauer/memory_provider.py bauer/sqlite_session_store.py bauer/agent.py`

## Status

- **Priority**: P2
- **Effort**: M para o spike (build M-L — grosseiro)
- **Risk**: LOW (spike)
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `2c9d86f`, 2026-07-07

## Why this matters (a visão 20/10)

O Bauer coleta dois sinais valiosos que hoje morrem sem consequência:

1. **Feedback explícito**: `/thumbsup` e `/thumbsdown` existem no agente
   (README: "vira sinal de qualidade na memória"; handler "L7: feedback" em
   `bauer/agent.py:~4659`). O sinal é GRAVADO — mas nada o CONSOME. Nenhum
   comportamento muda depois de 10 👎 no mesmo padrão.
2. **Histórico episódico**: sessões inteiras persistem no
   `SqliteSessionStore` com índice vetorial (FTS5 + vector store), mas
   conhecimento durável ("o usuário prefere X", "nesse repo o padrão é Y")
   só vira memória se o modelo lembrar de escrever (existe até um "nudge" —
   `memory_provider.py` `_nudge_state` — implorando pra ele salvar).

20/10 é fechar os dois loops: um **consolidador** que destila sessões antigas
em memória semântica automaticamente (episódico→semântico, rodando no idle ou
via scheduler), e o **feedback moldando comportamento** (padrões com 👎
recorrente entram no system prompt como "evite X"; padrões com 👍 viram
preferência). É a diferença entre um agente com banco de dados e um agente com
experiência.

## Current state (verificado)

- `bauer/agent.py:~4659` — comentário "L7: feedback de usuário — /thumbsup /
  /thumbsdown" no loop principal (localize o handler exato:
  `grep -n "thumbsup\|thumbsdown" bauer/agent.py`).
- `bauer/memory_provider.py` (1137 linhas) — `LocalMemoryProvider` com
  `_nudge_state` (last_write_turn, nudge_sent_at) — o mecanismo que LEMBRA o
  modelo de salvar memória; evidência de que a captura espontânea é fraca.
- `bauer/sqlite_session_store.py` — sessões persistidas; `_index_in_background`
  indexa user/assistant no vector store (`store_if_absent`,
  `sqlite_session_store.py:578-603`); `search_sessions` com
  vector→FTS5→LIKE fallback (:330).
- `bauer/vector_store.py` — store vetorial (`get_default_store`).
- Scheduler durável existe (`docs/architecture/automation-scheduler.yaml`,
  implemented) — candidato a disparar a consolidação periódica.
- Skill de referência conceitual: o ecossistema Claude tem "consolidate-memory"
  (reflective pass: merge duplicatas, fix stale, prune) — o design pode se
  inspirar, mas a implementação é do Bauer.

## Investigation steps

1. **Onde o thumbs down grava**: siga o handler de `/thumbsup|/thumbsdown` —
   o que exatamente é persistido, onde, com que contexto (última resposta?
   últimas N mensagens? tool calls do turno)? Esse payload é suficiente para
   minerar "padrão que desagrada"?
2. **O que a memória durável é hoje**: `memory_provider.py` — formato dos
   arquivos (`memory/*.md`? `.bauer_memory.json`?), como entram no contexto
   do agente (system prompt? tool memory?), e qual o budget de tokens que
   memória pode ocupar.
3. **Matéria-prima da consolidação**: quantas sessões/mensagens uma instalação
   típica acumula (olhe o schema do sqlite store)? O consolidador processa
   por sessão encerrada ou por janela (semanal)?
4. **Custo**: consolidar exige LLM (resumir/destilar). Qual slot auxiliary
   usar (`compression_model`? um novo `consolidation_model`)? Estimativa de
   tokens por rodada.
5. **Segurança do loop de feedback**: como evitar que 👎 enviesado (usuário
   frustrado com o provider, não com o padrão) envenene o comportamento?
   (Ex.: exigir N ocorrências, decay temporal, revisão humana da regra gerada.)

## Design deliverable

`docs/architecture/memory-consolidation-loop.yaml` (status draft) definindo:
pipeline do consolidador (trigger via scheduler → seleção de sessões não
consolidadas → destilação via auxiliary LLM → escrita em memória durável com
proveniência "consolidado de sessão X em DATA" → marcação de consolidado),
schema do sinal de feedback enriquecido (turno completo + fingerprint do
padrão), regra de promoção feedback→diretiva (N 👎 no mesmo fingerprint →
rascunho de regra "evite X" que o USUÁRIO aprova antes de entrar no prompt —
human-in-the-loop), orçamento (tokens/dia de consolidação), e plano de build
em 3 fatias (1ª: consolidador batch manual `bauer memory consolidate`;
2ª: agendado via automation-scheduler; 3ª: loop de feedback com aprovação).
≥5 open questions (opt-in? o que NUNCA consolidar — secrets, dados de
empresa multi-tenant? retenção do episódico pós-consolidação?).

## Done criteria

- [ ] `docs/architecture/memory-consolidation-loop.yaml` existe (draft)
- [ ] 5 perguntas respondidas com `file:line`
- [ ] Regra de promoção de feedback desenhada com human-in-the-loop explícito
- [ ] Plano de build em 3 fatias
- [ ] ≥5 open questions
- [ ] Nenhum código de produção alterado
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

- Já existir um consolidador (procure `grep -rn "consolidat" bauer/`) —
  mapeie e reporte.
- O sinal de /thumbsdown se revelar pobre demais (ex.: grava só um bool sem
  contexto) — o spike então inclui "enriquecer o sinal" como fatia 0 do build
  e reporta a limitação.
- Multi-tenancy (companies) tornar a consolidação ambígua (memória de qual
  empresa?) — pare e liste as opções de escopo.
