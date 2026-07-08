# Plan 015 (SPIKE): Runtime que aprende — perfis de provider persistidos por telemetria real

> **Executor instructions**: Plano de DESIGN/SPIKE — o entregável é um spec de
> arquitetura, não código de produção. Responda às perguntas de investigação
> com `file:line`, escreva o design, pare nos STOP conditions. Ao concluir,
> atualize a linha deste plano em `plans/README.md`.
>
> **Drift check (run first)**: `git log --oneline -5 -- bauer/context_manager.py bauer/provider_profile.py bauer/error_classifier.py bauer/runtime_state.py`

## Status

- **Priority**: P2
- **Effort**: S para o spike (build M — estimativa grosseira)
- **Risk**: LOW (spike não toca produção)
- **Depends on**: none (o build se beneficia de 011 — testes de budget verdes)
- **Category**: direction
- **Planned at**: commit `2c9d86f`, 2026-07-07

## Why this matters (a visão 20/10)

O "adaptativo" do Bauer hoje é **reativo e amnésico**: `shrink_budget()`
(`bauer/context_manager.py:118`) descobre que o endpoint free do OpenRouter
corta em 65536 tokens PARSEANDO o erro 400 — e esquece isso ao fim do processo.
Na próxima sessão, paga o mesmo erro de novo. Os limites de contexto vêm de
tabelas estáticas (`PROVIDER_CONTEXT_WINDOWS` no context_manager;
`bauer/provider_profile.py`; catálogo `bauer/models_dev.py` com cache de 1h).
20/10 é o runtime **lembrar o que aprendeu**: caps reais observados, taxas de
erro por provider/modelo, latência típica — persistidos e consultados no boot,
para que cada falha só aconteça uma vez por instalação. "Roda com o que tem"
vira "aprende com o que roda".

## Current state (verificado)

- `bauer/context_manager.py:118-137` — `shrink_budget(provider_cap_tokens)`:
  reduz budget quando o provider reporta janela menor. O valor aprendido morre
  com o processo; nada persiste.
- `bauer/context_manager.py:105` — `PROVIDER_CONTEXT_WINDOWS.get(provider, 32768)`:
  mapa estático por provider.
- `bauer/provider_profile.py` — perfis estáticos de provider (contexto default,
  fetch de modelos, is_free).
- `bauer/error_classifier.py` — classifica erros de API em `FailReason`
  (RATE_LIMIT, QUOTA, AUTH, CONTEXT_OVERFLOW, PROVIDER_DOWN...) — é o ponto
  natural de COLETA de telemetria de falha (já vê todos os erros).
- `bauer/runtime_state.py` — estado de runtime persistido
  (`~/.bauer/.runtime_state.json` — `read_state`/`write_state`); candidato a
  local do store aprendido (ou um arquivo próprio ao lado).
- `bauer/models_dev.py` — catálogo models.dev com cache 1h (limites NOMINAIS —
  o aprendido corrige o nominal com o REAL observado).
- Quem chama `shrink_budget`: procure em `bauer/agent.py`
  (`grep -n "shrink_budget" bauer/agent.py`) — é o ponto de captura do cap real.

## Investigation steps

1. **Onde o cap real é descoberto**: ache todos os call sites de
   `shrink_budget` e de `_parse_provider_context_cap` (`agent.py:1156`).
   Responda: que informação está disponível nesse momento (provider, modelo,
   cap)? É suficiente para uma chave `(provider, model) → observed_cap`?
2. **Onde falhas são classificadas**: em `error_classifier.py`, o
   `classify_api_error` retorna reason — quem chama e com que frequência?
   Dá para acrescentar um hook de contagem sem custo perceptível?
3. **Formato do store**: `runtime_state.py` serve (json simples,
   read/write atômico?) ou precisa de arquivo próprio
   (`~/.bauer/learned_profiles.json`)? Como evitar corrupção com escrita
   concorrente (CLI + gateway no mesmo home)?
4. **Pontos de consumo**: onde o boot decide contexto/provider hoje
   (`_build_client` em `bauer/commands/_runtime.py`, `preflight.run_doctor`,
   `ContextManager.__post_init__`)? Qual é a ordem de precedência desejada:
   `learned_cap < nominal` → usa learned; learned com TTL? decaimento?
5. **UX**: como o usuário vê/reseta o aprendido (`bauer doctor` mostra?
   `bauer profile reset`?).

## Design deliverable

`docs/architecture/learned-provider-profiles.yaml` (formato dos specs vizinhos,
status `draft`) definindo: schema do store (chave provider+model; campos:
observed_context_cap, error_counts por FailReason com janela deslizante,
last_seen, ttl), pontos de escrita (shrink_budget, error_classifier hook),
pontos de leitura (ContextManager boot, preflight, seleção de fallback),
política de expiração/decay, comando de inspeção/reset, e plano de build em
2-3 fatias (1ª: persistir e reusar SÓ o observed_cap — o caso 2026-07-02;
2ª: contadores de erro informando ordem de fallback; 3ª: exposição no doctor).
Inclua ≥4 open questions (ex.: TTL do cap aprendido? decay por versão do
modelo? opt-out?).

## Done criteria

- [ ] `docs/architecture/learned-provider-profiles.yaml` existe (status draft)
- [ ] 5 perguntas de Investigation respondidas com `file:line`
- [ ] Plano de build fatiado (2-3 fatias)
- [ ] ≥4 open questions
- [ ] Nenhum código de produção alterado (`git status`)
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

- Já existir persistência de caps aprendidos (procure
  `grep -rn "learned\|observed_cap" bauer/`) — mapeie e reporte.
- `runtime_state.py` se revelar inadequado E um arquivo novo criar problema de
  concorrência não trivial — apresente as opções em vez de escolher sozinho.
