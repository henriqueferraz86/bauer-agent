# Plano 065 — Build da consolidação de memória + loop de feedback

**Status:** IN PROGRESS

**Prioridade:** P2

**Esforço total estimado:** L, dividido em sprints 65–68 (fatias 0–3 do spike)

**Referências:** plano [017](017-spike-memory-consolidation-feedback-loop.md)
(spike, DONE) e o design em
[`docs/architecture/memory-consolidation-loop.yaml`](../docs/architecture/memory-consolidation-loop.yaml).

## Problema e objetivo

O spike 017 mostrou que o Bauer coleta dois sinais que hoje morrem sem
consequência: o `/thumbsup`/`/thumbsdown` (grava um texto truncado em
`FEEDBACK.md` que nada consome) e o histórico episódico do
`SqliteSessionStore` (conhecimento durável só vira memória se o modelo lembrar
de escrever). O spike parou numa condição de STOP: as sessões não têm
identidade de escopo, então consolidar para "a memória de quem?" era ambíguo.

Objetivo: fechar os dois loops com segurança — um consolidador que destila
sessões do workspace em memória semântica com proveniência, e feedback
repetido virando diretivas que **só entram no prompt depois de aprovadas
pelo usuário**.

## Decisões tomadas (coordenador, por delegação do usuário)

Registradas também no YAML de arquitetura (`decision:`) e nas SPECs.

| Tema | Decisão |
|---|---|
| Escopo | `workspace_only`: consolida só para a memória Markdown do workspace atual (`<workspace>/memory/`, a que o `LocalMemoryProvider` injeta). Escopo `company` **rejeitado** até existir contrato de identidade; escopo `user` adiado (sessões não têm `user_id`). |
| Proveniência | Sessão sem proveniência de workspace verificável (linhas históricas) **nunca** é consolidada (fail closed). A fatia 0 grava a proveniência em sessões e feedback novos. |
| Opt-in | Por workspace, desligado por padrão. |
| O que nunca consolidar | Segredos, credenciais, tokens e payloads de provider: redigidos com `privacy.redact_text` antes da destilação; registros que ainda parecem sensíveis (`secrets_scanner.has_secrets`) são descartados. |
| Retenção | Consolidar não apaga nem altera as sessões episódicas. |
| Falhas | Run falha/parcial re-tenta na próxima execução, dentro do budget. Idempotente por ledger com chave `session_id + scope`; os registros são gravados atomicamente antes de a sessão ser marcada como consolidada. |
| Diretivas | Cada diretiva derivada de feedback é aprovada individualmente; nunca é ativada automaticamente; tem expiração e revogação; aponta para o feedback que a sustenta. Promoção: ≥3 sinais do mesmo tipo em ≥2 sessões distintas, mesmo fingerprint, mesmo workspace, janela de 30 dias. |
| `machine_id` | Não particiona (o escopo é o workspace). Continua gravado como metadado. |
| Fatos | Carregam proveniência ("consolidado de sessão X em DATA"), `confidence` e `valid_until`; ficam em `CONSOLIDATED.md`, editável/removível pelo `bauer memory md`. |
| LLM | `auxiliary.compression_model` (slot existente). Nenhum campo novo de config. |

## Fora de escopo

- Escopo `company`/`user` e qualquer migração de identidade multiempresa.
- Consolidar para o `RuntimeMemoryManager` (JSONL): continua manual.
- Apagar/compactar sessões episódicas depois de consolidar.
- Ativação automática de qualquer diretiva.
- Canais (Telegram/Discord) e web: as sessões deles ainda não carregam
  proveniência de workspace e por isso ficam inelegíveis (fail closed).

## Sprints

Cada sprint segue o fluxo do `AGENTS.md`: SPEC → ARCHITECTURE → BACKLOG →
implementação → testes → revisão, numa branch `codex/sprint-XX-*` empilhada
sobre a anterior, com gates completos antes do commit.

| Sprint | Fatia | Escopo | Branch | Status |
|---|---|---|---|---|
| 65 | 0 `enrich_feedback_and_scope_contract` | Contrato de escopo de workspace, proveniência nas sessões novas, feedback estruturado e redigido | `codex/sprint-65-memory-scope-feedback` | DONE |
| 66 | 1 `manual_batch_consolidator` | `bauer memory consolidate` com `--dry-run`, budget, ledger determinístico, saída de revisão, sem ativação de prompt | `codex/sprint-66-memory-consolidate` | PENDING |
| 67 | 2 `opt_in_scheduled_consolidation` | Execução agendada pelo cron durável, sob o Kernel (kill-switch, policy, budget), com budget diário de tokens, métricas e retry/backoff | `codex/sprint-67-memory-consolidation-schedule` | PENDING |
| 68 | 3 `feedback_directives` | Fingerprint, rascunhos revisáveis, aprovação explícita, expiração/revogação, injeção só depois de aprovar | `codex/sprint-68-memory-feedback-directives` | PENDING |

### Critérios de aceite (todas as sprints)

- Nenhuma leitura ou escrita atravessa workspaces (teste).
- Sessões históricas sem proveniência são puladas (teste).
- Segredos nunca chegam ao payload do LLM nem à memória (teste).
- Retry é idempotente: nenhum fato duplicado (teste).
- Diretivas nunca são injetadas antes da aprovação; revogadas/expiradas somem do
  prompt (teste).

### Condições de parada

- Se alguma fatia exigir escopo `company` ou `user`, pare: a decisão vigente
  rejeita esses escopos.
- Se a execução agendada precisar contornar o Kernel (kill-switch, policy,
  budget), pare: trabalho autônomo passa pelo Kernel.

## Validação obrigatória por sprint

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
```
