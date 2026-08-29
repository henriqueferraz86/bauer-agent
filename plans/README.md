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
| [007](007-auth-key-file-permissions.md) | Restringir `.auth_key` para 0o600 + corrigir docstring | P5 | P1 | S | — | DONE (PR #86) |
| [008](008-webhook-ssrf-guard.md) | Aplicar guard SSRF (`url_safety`) na entrega de webhooks | P7 | P1 | S | — | DONE (merged `5c5f219`) |
| [009](009-server-request-body-limits.md) | Limitar tamanho do body em /chat e /v1/chat/completions | P7 | P2 | S | — | DONE |
| [010](010-orchestrator-resume-robustness.md) | Robustez do orquestrador: validar StepResult no `--resume` + avisar DAG circular | P4 | P2 | S | — | DONE |
| [011](011-context-budget-regression-tests.md) | Testes de regressão para budget/tail/`shrink_budget` do ContextManager | P1 | P2 | S | — | DONE (cobertura já existente, verificada em 2026-08-25) |
| [012](012-agents-md-claude-md.md) | Escrever `AGENTS.md` + `CLAUDE.md` para execução por agentes | DX | P2 | S | — | DONE |
| [013](013-agent-extract-slash-commands.md) | Extrair handlers de slash-command do `agent.py` (−800 linhas do god object) | P3 | P3 | M | — | DONE (drift remapeado; `agent_slash_commands.py`, compatibilidade por re-export) |
| [014](014-spike-autonomous-daemon-24-7.md) | SPIKE: Funcionário 24/7 — daemon → /loop → aprovação/relatório via gateway | P4 | P1 | M | — | DONE (spec: docs/architecture/autonomous-daemon-v2.yaml; achou GAP-1 ⚠) |
| [015](015-spike-learned-provider-profiles.md) | SPIKE: Runtime que aprende — perfis de provider por telemetria real | P1 | P2 | S | — | DONE (draft: `docs/architecture/learned-provider-profiles.yaml`) |
| [016](016-spike-policy-router-cost-ledger.md) | SPIKE: Policy router por tarefa + ledger de custo real | P2 | P2 | M | 015 (recomendado) | DONE (draft: `docs/architecture/policy-router-cost-ledger.yaml`) |
| [017](017-spike-memory-consolidation-feedback-loop.md) | SPIKE: Consolidação episódica→semântica + feedback que age | P3 | P2 | M | — | BLOCKED (escopo multiempresa; opções no draft) |
| [018](018-spike-taint-tracking-accountability.md) | SPIKE: Taint tracking de conteúdo externo + digest de prestação de contas | P5 | P1 | M | — | DONE (draft: `docs/architecture/taint-tracking-accountability.yaml`; preserva/evolui o ContextBuilder existente) |
| [019](019-spike-self-improving-skills-mcp-server.md) | SPIKE: Skills que se refinam por telemetria + Bauer como servidor MCP | P6 | P3 | M | — | DONE (draft: `docs/architecture/skills-refinement-mcp-server.yaml`; refinamento observável/rebaixamento opt-in, geração e servidor remoto adiados) |
| [020](020-spike-proactive-agent-unified-identity.md) | SPIKE: Agente proativo (briefing/alertas) + identidade unificada | P7 | P2 | M | 014 (recomendado) | DONE (draft: `docs/architecture/proactive-unified-identity.yaml`) |
| [021](021-bauer-run-autonomous-entrypoint.md) | Criar `bauer run` como entrada autônoma única para tarefas de ponta a ponta | DX | P1 | L | — (isolado do 013) | SUPERSEDED (022) |
| [022](022-bauer-run-e-simplificacao-cli.md) | `bauer run` governado pelo Kernel + simplificar superfície de comandos + desembaraçar limites | DX | P1 | L | — | DONE (branch bauer-run-cli) |

| [023](023-system-prompt-mode-aware-tools.md) | System prompt não ensina tool-call-como-JSON em modo `native` (raiz da inconsistência de tools) | P3 | P1 | M | — | DONE (PR #57) |
| [024](024-app-factory-integration-serve.md) | App Factory funciona pelo serve/Desktop (tools expostas + contexto no prompt) | P4 | P1 | M | 023 | DONE (implemented `0318b59`) |
| [025](025-system-prompt-os-aware.md) | System prompt reflete o SO real (não "Windows" fixo) em servidores Linux | P3 | P2 | S | — | DONE (`platform.system()`, paths/comandos Unix; Windows preservado) |
| [026](026-tool-capability-detection.md) | Aviso claro quando modelo cai no bridge por não estar no registry (+ detecção via Ollama) | P1 | P2 | M | — | DONE (live-client capability + warning in `8cd58cd`) |
| [027](027-doctor-agentic-stack-checks.md) | `bauer doctor` valida a stack agêntica (factory tools, tool mode, gate) | DX | P2 | S-M | — | DONE (notas best-effort para allowlist efetiva, bridge e gate App Factory) |
| [029](029-vector-store-dim-cache-invalidation.md) | Cache de dimensão do vector_store fica obsoleto após `delete()`/`delete_prefix()` (perda silenciosa de escrita) | P1 | P1 | S | — | DONE (commit `4c24cb1`) |
| [030](030-ollama-embed-json-guard.md) | `_ollama_embed` pode lançar exceção no caminho de sucesso, quebrando o contrato "nunca lança" do `EmbeddingEngine` | P1 | P1 | S | — | DONE (commit `8d80644`) |
| [031](031-tool-dedup-mutating-tools-fix.md) | `MUTATING_TOOLS` do dedup cita 5 tools inexistentes e falta ~17 tools mutantes reais (replay indevido) | P3 | P1 | S | — | DONE (commit `b2492b1`) |
| [032](032-delete-trigger-manager.md) | Deletar `trigger_manager.py` — 621 linhas, zero chamador fora do próprio teste | P4 | P2 | S | — | DONE (commit `980776f`) |
| [033](033-tier4-checkpoint-audit-observability-decision.md) | Decidir destino do "Tier 4" (checkpoint/audit_trail/observability) — testado mas nunca ligado ao daemon | P4 | P2 | M | — | DONE (commit `c3f927e`, Path B nos 3) |
| [034](034-delete-otel-traces.md) | Deletar `otel.py` + `bauer traces` — tracer OTLP morto, comando sempre vazio em produção | P4/DX | P2 | S | — | DONE (commit `94a6f15`) |
| [035](035-delete-feedback-store.md) | Deletar `feedback_store.py` — wrapper sem chamador real fora do próprio teste | P3 | P3 | S | — | DONE (commit `c348dca`) |
| [036](036-unify-memory-managers.md) | Reconciliar `MemoryManager` (Markdown) e `RuntimeMemoryManager` (JSONL) — dois stores irmãos sem sincronia sob `bauer memory` | P3 | P2 | M | — | DONE (commit `d8fb91d`, Path B: `memory md`/`memory runtime`) |
| [037](037-consolidate-scheduler-engines.md) | Consolidar `bauer schedule`/`worker` (zero teste de CLI) no stack supervisionado `cron`/`dispatch`/`runtime` | P4 | P2 | L | 032 (fazer 032 primeiro, aquecimento sem decisão) | DONE (commit `9f80b8b`) |
| [038](038-nest-thin-command-groups.md) | Aninhar `telegram`/`discord` sob `gateway` e `skills-hub`/`skills-bundle` sob `skills` | DX | P2 | S | — | DONE (commit `71cba8c`) |
| [039](039-dedupe-report-commands-helper.md) | Unificar `_parse_last` triplicado em `audit`/`perf`/`skills_cmd` (corrige bug de timezone no `skills_cmd`) | P1/DX | P2 | M | — | DONE (commit `c8fc8ae`) |
| [040](040-merge-events-into-runs.md) | Fundir `bauer events tail` em `bauer runs events` (mesma API `EventBus`, features complementares) | DX | P3 | S | — | DONE (commit `c5158f9`) |
| [041](041-unify-auth-credential-stores.md) | Documentar precedência `bauer auth` vs `bauer credential` + `auth status --all-sources` | P5/DX | P3 | M | — | DONE (commit `f699a5c`) |
| [042](042-collapse-lsp-tools.md) | Unificar as 7 tools `lsp_*` de leitura numa tool `lsp(action=...)` — `lsp_format`/`lsp_rename` ficam separadas | P3 | P3 | S | — | DONE (commit `028c6b2`) |
| [043](043-multiplex-kanban-tools.md) | Multiplexar as 9 tools `kanban_*` em `kanban_read`/`kanban_write` (padrão já usado por `cronjob`/`process`) | P3 | P3 | M | 044 (fazer 044 primeiro, código morto no mesmo arquivo) | DONE (commit `1b8f3c6`) |
| [044](044-delete-legacy-kanban-methods.md) | Deletar os 8 métodos `_legacy_kanban_*` — 124 linhas mortas em `tools/kanban.py` | P3 | P2 | S | — | DONE (commit `f5dd4a6`) |
| [045](045-collapse-search-text-regex-search.md) | `search_text` passa a delegar para `regex_search` (implementação duplicada em `tools/fs.py`) | P3 | P3 | S | — | DONE (commit `42aabe6`) |
| [046](046-fix-stale-tools-yaml.md) | Deletar `tools.yaml` — cobre 7 de 84 tools, efeito zero em runtime hoje | DX | P3 | S | — | DONE (commit `3089c49`) |
| [047](047-merge-channel-social-mixins.md) | Fundir `SocialToolsMixin` em `ChannelToolsMixin` — dois mixins pequenos pro mesmo domínio (mensageria) | P6 | P4 | S | — | DONE (commit `3b5398b`) |
| [048](048-cli-visual-2.md) | Bauer CLI Visual 2.0 — linguagem textual, resultados e fluxos principais | DX | P2 | M | 028 | DONE |
| [050](050-p1-governance-hard-limits.md) | Tornar efetivos os limites de governança e aprovações | Segurança/correção | P1 | L | 049 | DONE (merge `af40d4b`; commit `74af4c4`) |
| [051](051-p2-resource-boundaries.md) | Impor limites a timeout de tools e tarefas de memória | Confiabilidade | P2 | M | 050 | DONE (merge `0bb8ae4`; commit `98932ac`) |
| [052](052-p3-docs-command-parity.md) | Alinhar roteiro beta e setup de desenvolvimento | Documentação/DX | P3 | S | 051 | DONE (merge `c1f75d2`; commit `ad2b59b`) |
| [053](053-p4-sqlite-task-backend-rollout.md) | Rollout seguro e opt-in do backend SQLite de tarefas | Migração de dados/DX | P4 | L | 052 | DONE (merge `9c94778`; commit `ba236a5`) |

Status válidos: `TODO` | `IN PROGRESS` | `DONE` | `BLOCKED (motivo)` | `REJECTED (motivo)`

**Rodada 7 (2026-08-25) — P0→P4, endurecimento pós-auditoria**: planos 049–053,
executados em série (cada um depende do anterior). Todos DONE.

Duas pendências de índice ficam registradas aqui em vez de silenciosamente
resolvidas:

- O plano **049** (`049-p0-security-boundaries.md`) foi escrito na branch
  `codex/cli-visual-2` e **nunca chegou ao master** — só o código dele veio
  (`13093dd`, merge `c2d3e4e`). A tabela cita "049" como dependência do 050,
  mas não há linha nem arquivo para ele. Quem for reconciliar a numeração:
  trazer o documento junto.
- O **053** entrou pelo merge `9c94778` sem passar por esta tabela; a linha
  acima é a correção.

O que o 053 mudou, para quem for mexer no caminho de migração depois:

- `kanban_migration.migrate_tasks_md` deixou de tratar uma task já presente
  como "nada a fazer". O skip cego não duplicava nada, mas os comentários do
  Markdown de uma task existente nunca eram revisitados. Agora
  `_ensure_legacy_comments` conta as ocorrências já gravadas com
  `author='legacy-md'` e insere só o delta, e `MigrationReport.comments_added`
  reporta o total. A contagem é por **multiset**, não por conjunto: `task_comments`
  não tem constraint de unicidade de propósito (discussão normal repete texto),
  então dois bullets idênticos na origem rendem duas linhas — um dedup por `set`
  perderia a segunda para sempre. Só comentários com o marcador de autor entram
  na conta: o que o usuário escreveu pela API do kanban nunca é tocado.
- `bauer kanban-migrate --activate` é a adoção governada. **Recusa `--board`**:
  ativar validando um board diferente do que a factory vai ler depois é o bug
  `#10-F` de volta, com a flag já virada. Faz backup de `TASKS.md` e do config
  via `open("xb")` — criação exclusiva, sem a corrida de um `if exists()` seguido
  de cópia — em `.before-sqlite.bak`, `.bak.1`, … , nunca sobrescrevendo o de uma
  ativação anterior. Migra, **valida que todo ID da origem chegou ao destino**
  (contar linhas migradas não bastaria: um erro parcial mantém o relatório verde
  para as tasks que passaram) e só então grava `agent.task_backend: sqlite`, por
  escrita atômica em tempfile. Origem ausente, erro de migração, ID faltando,
  backup impossível ou config inexistente deixam a flag intacta.
  `--dry-run --activate` descreve backups, validação e ativação sem efeito nenhum.
  O default de instalações existentes continua `markdown`.

Limite conhecido, dito na saída do comando em vez de escondido: o rollback é da
FLAG, não dos dados. Voltar para `markdown` devolve a leitura ao `TASKS.md`
preservado, mas tarefas criadas depois da ativação vivem só no SQLite — não há
espelhamento de volta, e o comando não finge que há.

**Rodada 6 (2026-08-18, commit `d903de8`) — redução de superfície: bugs, código morto, consolidação**:
planos 029–047, motivados por pedido explícito do usuário para reduzir a
quantidade de funcionalidades acumuladas (múltiplas rodadas de sprints
adicionaram 172 módulos em `bauer/`, 51 grupos de comando, 84 tools em 18
mixins). 4 agentes de auditoria em paralelo (superfície de CLI, tools/
mixins, código morto/sistemas duplicados, bugs em código recém-alterado),
achados vetados abrindo o código citado antes de virar plano — inclusive
uma descoberta feita durante a vetagem: `_CHAT_CONTEXT_DENYLIST`
(`tool_router.py:319`) está declarado mas **nunca é consultado em lugar
nenhum** (ver plano 043), o que reduziu o risco estimado da multiplexação
do kanban.

Três frentes:
- **Bugs reais** (029–031): cache de dimensão obsoleto no vector_store,
  guard faltando em `_ollama_embed`, lista de tools mutantes desatualizada
  no dedup — todos S/LOW/alta confiança, reproduzidos ou confirmados por
  leitura direta do código antes de virar plano.
- **Código morto confirmado** (032, 034, 035, 044): `trigger_manager.py`,
  `otel.py`+`traces_cmd.py`, `feedback_store.py`, métodos
  `_legacy_kanban_*` — todos com grep de zero-chamador confirmado
  pessoalmente, não só relatado pelo subagente.
- **Consolidação/unificação** (033, 036–043, 045–047): decisões
  arquiteturais (3 engines de agendamento, 2 memory managers, 2 cofres de
  credencial, Tier 4 wire-in-ou-delete) e reduções mecânicas de superfície
  (CLI aninhada, tools multiplexadas, helpers deduplicados).

Ordem sugerida: **044 antes de 043** (remove código morto do mesmo arquivo
antes de multiplexar); **032 antes de 037** (aquecimento sem decisão antes
do scheduler que exige decisão); os bugs (029–031) e as deleções puras
(032, 034, 035, 044, 046) são independentes entre si e de baixo risco —
bom ponto de partida. Os planos de decisão (033, 036, 037, 041) têm um
passo de investigação embutido — não pulem para a implementação sem
concluí-lo.

**Todos os 19 planos da rodada 6 concluídos em 2026-08-18** — 029–031,
032/034/035/044/046, 038/039/040/042/043/045/047 (PRs #115, #116, #117) e
033/036/037/041 (branch `feat/033-036-037-041-arch-decisions`, os 4 de
decisão arquitetural). Resumo dos 4 últimos: **033** — Tier 4
(checkpoint/audit_trail/observability) removido, Path B nos 3: o daemon já
tem seu próprio modelo de resiliência (kanban board + `DaemonStateDB`), o
`EventBus` já é o log estruturado vivo, e `server.py` já tinha sua própria
classe `_Metrics` (nunca usou `observability.py`). **036** — `bauer memory`
virou `memory md`/`memory runtime` explícitos: investigação mostrou que
`MemoryManager` (Markdown) já era automática (plugada em `MemoryProvider`,
lida por `agent.py`) enquanto `RuntimeMemoryManager` (JSONL) tinha zero
consumidor automático — nenhuma das duas premissas do plano batia
exatamente, optou-se por explicitar em vez de forçar uma unificação sem
evidência. **037** — `bauer schedule`/`worker` removidos (zero cobertura de
teste, nunca supervisionados por `bauer runtime start`); `Scheduler` em si
sobrevive só para o desktop. **041** — `bauer auth status --all-sources`
novo, documentando a precedência real (mais fina que "auth sempre vence":
token JWT do Codex cai para `bauer credential`).

Achado relevante durante
o plano 043 (kanban multiplex): a granularidade por ação (contexto `chat`
não pode `heartbeat`/`complete`/`block`; contexto `worker` só pode
`heartbeat`/`comment`/`complete`/`block`, não `create`/`unblock`/`link`) **não
estava** em `_CHAT_CONTEXT_DENYLIST`/`_WORKER_CONTEXT_ALLOWLIST` do
`tool_router.py` como o plano assumia — essas duas eram código morto (zero
consumidor, deletadas). A política real vive em `bauer/tool_policy.py`'s
`default_tool_contexts()`, carregada em `ToolRouter.__init__` e consultada em
`_is_tool_allowed_in_context()`. Movida para dentro de
`KanbanToolsMixin._kanban_write` (que também replica `_record_tool_denied`
para não quebrar a trilha de auditoria `tool.denied`/`last_denied_tool`).
Segundo achado: `bauer/commands/_runtime.py`'s `_LOCAL_DEFAULT_ALLOWLIST`
(toolset enxuto pra modelo local) também citava `kanban_create`/`list`/
`complete` por nome — corrigido. **Lição**: antes de renomear/multiplexar uma
tool, `grep -rn "<nome_da_tool>"` em `bauer/` inteiro, não só em
`tool_router.py` — os consumidores reais de uma política de contexto podem
estar em outro módulo do que o esperado.

**Nota de reconciliação do índice**: durante o recon desta rodada, os
arquivos `023-auditoria-completa-2026-07.md`, `024-spike-task-store-migration.md`
e `028-design-system-bauer.md` foram encontrados em `plans/` mas nunca
foram adicionados a este índice (colisão de numeração com `023-system-
prompt-mode-aware-tools.md` e `024-app-factory-integration-serve.md` — duas
rodadas diferentes reusaram os mesmos números). `023-auditoria-completa-
2026-07.md` tem sua própria tabela de status interna com ~10 itens ainda
`TODO`. Não foram re-auditados nem reindexados nesta rodada (fora do
escopo do pedido do usuário) — fica registrado aqui para quem for arrumar
a numeração depois.

**Rodada 5 (2026-07-18, commit `ced7dc2`) — tarefas agênticas + App Factory pelo `bauer serve`/Desktop com modelos locais**:
planos 023–027, motivados por um deploy real (Ubuntu + Ollama, usuário no Desktop).
Achado-raiz: o `_build_system_prompt` (compartilhado CLI/serve) **ensina o modelo a
emitir tool calls como JSON de texto** mesmo em modo `native` — modelos fracos obedecem
e não executam nada ("0 tools"); é a raiz de toda a inconsistência de tools (plano 023).
Objetivo do usuário: usar o App Factory como hábito **pelo Desktop**, mas o serve não
expõe as tools do factory nem injeta o estado dele no prompt (plano 024). Planos menores:
prompt cravado em "Windows" num servidor Linux (025), degradação silenciosa pro bridge
quando o modelo não está no `models.yaml` (026), e checagens da stack agêntica no doctor
(027). Ordem: **023 → 024** (o factory depende das tools executarem); 025/026/027 independentes.
Contexto operacional descoberto na sessão: tools escrevem no workspace do **projeto ativo**
(`/api/projects` active), não em `~/.bauer/workspace`; `qwen3-coder:30b` faz tool calling
nativo confiável, `qwen2.5:7b` não; modelo importado sem `RENDERER` no Modelfile degenera.

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
