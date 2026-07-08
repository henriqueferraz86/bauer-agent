# Plan 016 (SPIKE): Policy router por tarefa + ledger de custo real

> **Executor instructions**: Plano de DESIGN/SPIKE — entregável é spec de
> arquitetura, não código. Responda às perguntas com `file:line`, escreva o
> design, pare nos STOP conditions. Ao concluir, atualize `plans/README.md`.
>
> **Drift check (run first)**: `git log --oneline -5 -- bauer/auxiliary_client.py bauer/config_loader.py bauer/model_switcher.py`

## Status

- **Priority**: P2
- **Effort**: M para o spike (build L — grosseiro)
- **Risk**: LOW (spike)
- **Depends on**: 015 recomendado antes (o router consome os perfis aprendidos)
- **Category**: direction
- **Planned at**: commit `2c9d86f`, 2026-07-07

## Why this matters (a visão 20/10)

O multi-provider do Bauer hoje roteia por LISTA: um modelo primário e
`fallback_models` percorridos em erro 429/5xx. O config real do mantenedor tem
50+ modelos free nessa lista — sinal claro de que a intenção é arbitragem de
free tiers, feita manualmente hoje. Mas o repo JÁ tem o embrião do roteamento
por tarefa: os **auxiliary slots** (`bauer/config_loader.py:688-709` —
`kanban_decomposer`, `triage_specifier`, `compression_model`,
`background_reviewer`, `approval_model`, `vision_model`), cada um apontando uma
subtarefa para um modelo barato. 20/10 é generalizar: um **policy router** que
decide por requisição (tipo de tarefa × custo × latência × histórico de erro)
qual provider/modelo usar — resumo vai pro free, código vai pro forte — com um
**ledger de custo real** (hoje `/status` mostra tokens; dinheiro, não).

## Current state (verificado)

- `bauer/config_loader.py:688-709` — `AuxiliarySection` com 6 slots; "All slots
  default to empty → the main model.name is used... users opt-in to per-slot
  routing as they tune for cost". O conceito de roteamento por função EXISTE,
  mas é estático e por-subsistema, não por-requisição.
- `bauer/auxiliary_client.py` — resolve slot → client (`get_text_auxiliary_client`).
- `bauer/agent.py:1943` — `run_one_turn_with_fallback`: fallback por lista em
  erro (comportamento atual a preservar como fallback do router).
- `bauer/commands/_runtime.py:669` — `build_fallback_clients(cfg)`: monta a
  lista de fallback (dedup contra primário).
- `bauer/model_switcher.py` (817 linhas) — troca de modelo ao vivo.
- `bauer/models_dev.py` — catálogo com **preços** (memória do projeto: campo
  `cost`, não `pricing`; `cost==0 ≠ grátis) — a fonte para o ledger converter
  tokens→dinheiro.
- Contagem de tokens por turno: procure onde tokens são contados/logados hoje
  (`grep -rn "usage\|tokens" bauer/agent.py | grep -i cost` e
  `bauer/context_manager.py`) — o spike confirma o ponto de medição.

## Investigation steps

1. **Taxonomia de tarefas**: enumere os pontos onde o Bauer já sabe "que tipo
   de trabalho é" (auxiliary slots; delegate_task por especialista;
   compressão; tool calls vs chat puro). Proponha as 4-6 classes de rota
   iniciais (ex.: `compress`, `summarize`, `code`, `chat`, `vision`, `judge`).
2. **Ponto de interceptação**: onde UMA função central escolhe (client, model)
   por chamada? (Candidatos: `_build_client_fn`/`run_one_turn_with_fallback`
   em agent.py; `auxiliary_client`). O router entra aí sem tocar os call sites?
3. **Custo real**: o response dos providers OpenAI-compat traz `usage`
   (prompt/completion tokens)? Onde isso é capturado hoje (se é)? Cruzar com
   `models_dev` cost — qual a granularidade possível (por turno? por sessão)?
4. **Persistência do ledger**: formato (sqlite em `~/.bauer/ledger.sqlite3`?
   jsonl append-only?), rotação, e exposição (`/status` ganha linha de custo
   do dia; `bauer cost` novo comando?).
5. **Arbitragem free-tier**: com o 015 (contadores de RATE_LIMIT por modelo),
   a rotação do pool free pode ser informada por erro recente — desenhe a
   heurística (cooldown por modelo rate-limitado em vez de ordem fixa).

## Design deliverable

`docs/architecture/policy-router-cost-ledger.yaml` (status draft) definindo:
interface `RoutePolicy` (input: classe da tarefa, tamanho estimado, orçamento;
output: client+model ranqueados), config nova (`router:` — já existe uma
`RouterSection`? verifique `grep -n "RouterSection" bauer/config_loader.py` e
integre em vez de duplicar), schema do ledger, pontos de medição de usage,
compat com fallback atual (router indisponível → lista velha), e plano de
build em 3 fatias (1ª: ledger passivo medindo custo real sem rotear nada;
2ª: router para os auxiliary slots existentes; 3ª: router no caminho principal
com arbitragem free). ≥4 open questions (orçamento diário hard-stop? rota
manual por sessão via /model continua soberana?).

## Done criteria

- [ ] `docs/architecture/policy-router-cost-ledger.yaml` existe (draft)
- [ ] 5 perguntas respondidas com `file:line` (incl. o que `RouterSection` já faz)
- [ ] Plano de build em 3 fatias, ledger-primeiro
- [ ] ≥4 open questions
- [ ] Nenhum código de produção alterado
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

- `RouterSection` em config_loader já implementar roteamento por tarefa
  (não só config morta) — mapeie o que existe e reporte antes de desenhar.
- Não existir captura de `usage` em lugar nenhum do caminho de chat — o ledger
  precisa dela; reporte como pré-requisito de build em vez de inventar medição
  por estimativa de chars.
