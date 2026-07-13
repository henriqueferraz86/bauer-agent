# Planos de Implementação — BauerAgent

Gerado pelo skill `/improve` em 2026-06-27 (commit `820322b`).
Plano 006 adicionado em 2026-06-29 (commit `1f6292e`).
**Rodada 2 (planos 007–013) adicionada em 2026-07-06 (commit `2c9d86f`)** —
auditoria organizada pelos 7 pilares do projeto.
Execute na ordem abaixo, salvo dependências indicadas.
Cada executor: leia o plano completo antes de iniciar, respeite as condições
de STOP, e atualize sua linha de status ao concluir.

## Ordem de execução e status

| Plano | Título | Pilar | Prioridade | Esforço | Depende de | Status |
|-------|--------|-------|------------|---------|------------|--------|
| [001](001-fix-http-exception-detail.md) | Ocultar detalhes de exceção nas respostas HTTP 500 | P7 | P1 | S | — | DONE |
| [002](002-fix-api-key-timing-attack.md) | Substituir comparação de API key por hmac.compare_digest | P5 | P1 | S | — | DONE |
| [003](003-fix-info-endpoints-auth.md) | Adicionar guarda de auth nos endpoints informativos | P7 | P1 | S | — | DONE |
| [004](004-fix-xor-fallback.md) | Eliminar o fallback XOR silencioso em auth.py | P5 | P1 | S | — | DONE |
| [005](005-commands-integration-tests.md) | Testes de integração para os módulos de bauer/commands/ | P3 | P1 | M | — | DONE |
| [006](006-distributed-agents-mvp.md) | Agentes Distribuídos MVP — dispatch remoto HTTP entre instâncias bauer serve | P6 | P2 | M | — | DONE |
| [007](007-auth-key-file-permissions.md) | Restringir `.auth_key` para 0o600 + corrigir docstring | P5 | P1 | S | — | TODO |
| [008](008-webhook-ssrf-guard.md) | Aplicar guard SSRF (`url_safety`) na entrega de webhooks | P7 | P1 | S | — | TODO |
| [009](009-server-request-body-limits.md) | Limitar tamanho do body em /chat e /v1/chat/completions | P7 | P2 | S | — | TODO |
| [010](010-orchestrator-resume-robustness.md) | Robustez do orquestrador: validar StepResult no `--resume` + avisar DAG circular | P4 | P2 | S | — | TODO |
| [011](011-context-budget-regression-tests.md) | Testes de regressão para budget/tail/`shrink_budget` do ContextManager | P1 | P2 | S | — | TODO |
| [012](012-agents-md-claude-md.md) | Escrever `AGENTS.md` + `CLAUDE.md` para execução por agentes | DX | P2 | S | — | TODO |
| [013](013-agent-extract-slash-commands.md) | Extrair handlers de slash-command do `agent.py` (−800 linhas do god object) | P3 | P3 | M | — | TODO |
| [014](014-spike-autonomous-daemon-24-7.md) | SPIKE: Funcionário 24/7 — daemon → /loop → aprovação/relatório via gateway | P4 | P1 | M | — | DONE (spec: docs/architecture/autonomous-daemon-v2.yaml; achou GAP-1 ⚠) |
| [015](015-spike-learned-provider-profiles.md) | SPIKE: Runtime que aprende — perfis de provider por telemetria real | P1 | P2 | S | — | TODO |
| [016](016-spike-policy-router-cost-ledger.md) | SPIKE: Policy router por tarefa + ledger de custo real | P2 | P2 | M | 015 (recomendado) | TODO |
| [017](017-spike-memory-consolidation-feedback-loop.md) | SPIKE: Consolidação episódica→semântica + feedback que age | P3 | P2 | M | — | TODO |
| [018](018-spike-taint-tracking-accountability.md) | SPIKE: Taint tracking de conteúdo externo + digest de prestação de contas | P5 | P1 | M | — | REVISE (spec escrito e de boa qualidade em worktree agent-af2e8284; falta: quotar ~6 linhas de schema-notation p/ YAML parsear + commit; cortado pelo rate limit) |
| [019](019-spike-self-improving-skills-mcp-server.md) | SPIKE: Skills que se refinam por telemetria + Bauer como servidor MCP | P6 | P3 | M | — | TODO |
| [020](020-spike-proactive-agent-unified-identity.md) | SPIKE: Agente proativo (briefing/alertas) + identidade unificada | P7 | P2 | M | 014 (recomendado) | TODO |
| [021](021-bauer-run-autonomous-entrypoint.md) | Criar `bauer run` como entrada autônoma única para tarefas de ponta a ponta | DX | P1 | L | — (isolado do 013) | SUPERSEDED (022) |
| [022](022-bauer-run-e-simplificacao-cli.md) | `bauer run` governado pelo Kernel + simplificar superfície de comandos + desembaraçar limites | DX | P1 | L | — | TODO |

Status válidos: `TODO` | `IN PROGRESS` | `DONE` | `BLOCKED (motivo)` | `REJECTED (motivo)`

**Rodada 4 (2026-07-12, commit `ffd3a3d`) — entrada autônoma simplificada**:
o plano 021 cria `bauer run "tarefa"` como fachada síncrona do motor de `/loop`,
com workspace=CWD, config canônico, limites explícitos e paridade com a Web.
Execute isoladamente do plano 013 porque ambos alteram `bauer/agent.py`.

**Rodada 3 (2026-07-07, commit `2c9d86f`) — spikes de direção "20/10"**: os
planos 014–020 são planos de DESIGN/SPIKE, um por pilar: o entregável é um spec
em `docs/architecture/` (gap analysis, API, fatias de build, open questions),
NÃO código de produção. Ordem sugerida: **014 primeiro** (é o guarda-chuva — o
funcionário 24/7; descoberta-chave: `bauer daemon` e o automation-scheduler já
existem, o gap é aprovação remota via Slack + relatório + confiança
progressiva), depois 018 (segurança que a autonomia exige), 015→016 (telemetria
antes do router), e 017/019/020 em qualquer ordem. Os builds resultantes de 014
NÃO devem ir a produção antes de 007/008 (fixes de segurança) estarem DONE.

Pilar: P1 Runtime adaptativo · P2 Multi-provider · P3 Agent/tools/memória ·
P4 Autonomia/governança · P5 Segurança · P6 Extensibilidade · P7 Conectividade ·
DX cross-cutting. **P2 (multi-provider) e P6 (extensibilidade) não geraram
plano nesta rodada** — seus achados são de segunda camada ou direção (ver
seções abaixo); os pilares foram auditados, mas sem achado HIGH-leverage.

## Ordem recomendada (rodada 2)

Segurança e quick-wins primeiro; o refactor grande por último:

1. **007** (chmod .auth_key) — quick win de segurança, isolado.
2. **008** (SSRF webhook) — segurança, reusa módulo existente.
3. **009** (limite de body) — segurança/DoS, declarativo.
4. **010** (orquestrador --resume) — correctness, isolado.
5. **011** (testes de budget) — só testes, zero risco.
6. **012** (AGENTS.md/CLAUDE.md) — docs, zero risco de código.
7. **013** (extrair slash-commands) — refactor M/MED; rode com a suíte verde
   (ela é a rede de segurança). Independente dos demais, mas é o de maior risco.

## Notas de dependência

- 001–006 (rodada 1): todos DONE. 005 (testes de commands) já serve de rede de
  segurança; o pré-requisito que destravava o refactor de `agent.py` foi
  cumprido — por isso o **013** é fair game nesta rodada.
- 007–012 são independentes entre si (podem rodar em paralelo/qualquer ordem).
- 013 é independente mas deve rodar com a suíte de testes verde (é um refactor
  guardado pelos characterization tests existentes).

## Achados considerados e rejeitados (rodada 2)

Vetados abrindo o código citado; NÃO re-auditar:

- **Endpoints info sem auth** (`server.py`): **já corrigido** pelo plano 003 —
  `/status`, `/metrics`, `/tools`, `/models` têm `Depends(_verify_key)`. Só
  `/health` é aberto (correto por design). Falso positivo do auditor.
- **`secrets_scanner` "sem testes"**: falso — há `TestSecretsScanner` em
  `test_new_features.py` (10+ patterns testados de 22 definidos). Vira só
  lacuna parcial de cobertura (adicionar testes p/ os ~12 patterns restantes) —
  esforço S, baixa prioridade; não priorizado nesta rodada.
- **Race no vector store do session store** (`sqlite_session_store.py:578`):
  `store_if_absent` com `source_id` determinístico já é idempotente — a
  alegação de "vetores duplicados" cai. Best-effort por design.
- **Validação de schema em `execute()`** (tool_router): by-design — o Bauer mira
  modelos pequenos/locais e faz coerção defensiva por tool; jsonschema estrito
  rejeitaria inputs válidos coeridos.
- **Duplicação de path-validation nos file tools**: `_sandbox` já centraliza;
  os números de linha do auditor excediam o arquivo (evidência mal-atribuída).
- **Shell `python -c` / `find -exec` bypass** (`shell_runner`): inerente a
  allowlist de interpretador num agente local com approval flow — by-design.
- **Token floor-division** (`context_manager.py:404`): ~1% num heurístico já
  ±30% (char/4); negligível.
- **Tail-budget em contexto pequeno**: **já corrigido** no `__post_init__`
  (cap `min(TAIL, budget//3)`, bug de 2026-06-10) — sobrou só lacuna de teste,
  coberta pelo plano 011.
- **Nudge de memória sem lock / auxiliary swallow / bot-token plaintext em
  memória / X-Forwarded-For spoofing**: best-effort ou convenção padrão; LOW,
  não valem plano isolado.

## Achados de segunda camada (candidatos a próxima rodada)

Reais, mas leverage menor — não viraram plano agora:

- **P2 — cache de `models_dev` sem lock** (`models_dev.py:219`): race só em modo
  servidor com requisições concorrentes. Esforço M, conf MED.
- **P2 — duplicação no `error_classifier`** (detecção de erro em 2 lugares fora
  do `openai_client`). Tech-debt, esforço M.
- **P3 — cobertura de error-paths das tools** (file-not-found, timeout,
  permissão). Testes, esforço M.
- **P4 — concurrency do kanban/task_dispatcher** (CAS sem epoch/ABA, checagem de
  capacidade de lane fora do lock): reais mas exigem timing multi-processo;
  candidatos a "investigar" antes de plano.

## Achados de direção (opções do mantenedor, não bugs)

- **P6 — matching semântico de especialistas** (`agent_registry.match()`): hoje
  é overlap coefficient por palavra-chave, escolha *deliberada* (Jaccard
  penalizava docs ricos). Embeddings melhorariam a auto-seleção de agente, mas é
  enhancement, não bug. Esforço M, ganho de UX incremental.

## Achados da rodada 1 (histórico) considerados e rejeitados

- **Scripts `_fix_chat*.py` em `workspace/`**: dead code, delete manual.
- **`_quantfx_staging/`**: gitignoreado, sem impacto.
- **Inconsistência de extras no `pyproject.toml`**: editorial.
- **Pydantic v2 sem ADR**: baixa urgência, sem risco prático.
