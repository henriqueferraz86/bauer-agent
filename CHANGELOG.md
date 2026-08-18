# Changelog

Todas as mudanças notáveis são documentadas aqui.
Segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/) e [SemVer](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Harness — execução autônoma governada (S7–S14)

Campanha de 2026-07-29 a 07-31. Objetivo: o agente não pode declarar sucesso sem
validação. Scorecard medido (`python -m evals.harness.medir`): **98%**, 21 dos 22
indicadores. Detalhes em [docs/harness/](docs/harness/README.md).

#### Mudança de comportamento
- **`kernel.enabled` agora é `true` por default.** Toda execução passa pelo
  `BauerKernel`. Motivo: `load_config` não mescla o config do diretório com o de
  `$BAUER_HOME` — com default `false`, qualquer projeto com `config.yaml` próprio
  e sem seção `kernel:` desligava a governança sem ninguém notar. Desligar agora
  exige escrever `enabled: false`.
- **Provider local (Ollama, LM Studio) custa US$ 0.** Antes caía no preço genérico
  de nuvem ($1/$4 por 1M) e um laço 100% local abortava no `--max-cost` em ~94
  rodadas sem ter gastado nada.

#### Adicionado
- **Gates de validação**: `Tests`, `Baseline` (ratchet — só falha nova reprova),
  `Scope`, `Secrets` (só linhas adicionadas do diff), `Diff` (contrato sem
  mudança = falso sucesso), `Acceptance` (roda `validation.commands` de verdade).
  Reprovação vira `replan_feedback` e o laço tenta corrigir.
- **`.bauer/task.yaml`** (`core/task/contract.py`): escopo, critérios de aceite,
  comandos de validação, nível de isolamento, `risk_level` e `requires_approval`.
  O `AcceptanceGate` usa o snapshot lido antes do run — agente que edita o próprio
  contrato não escreve o critério que vai julgá-lo.
- **Aprovação humana por contrato**: `risk_level` `high`/`critical` põe o run em
  `waiting_approval` e o Kernel para.
- **Isolamento por worktree** (`core/workspace/isolation.py`) — git worktree por
  run, publicado no fim ou preservado em falha.
- **`--local`** em `bauer agent` e `bauer serve`: rotea por `model.profiles_local`
  / `model.fallback_models_local` e **recusa subir** se algo apontar para a nuvem,
  nomeando o campo errado.
- **Invariante de capacidade do runtime** (`runtime_capability.py`): modo de tool
  calling (nativo × bridge) por provider, executado como invariante testada em vez
  de inferido em vários pontos.
- **Sinais de progresso** (`progress_signals.py`): estagnação detectada pelo hash
  do patch, não pela lista de arquivos.
- **Observabilidade**: 9 tipos de evento novos, `EVENT_TYPES` derivado do
  `EventType` (acabou a lista duplicada escrita à mão) e `emitir()` tolerante a
  bus ausente.
- **Suíte de avaliações** (`evals/harness/`): `medir.py` (scorecard) + runner com
  cenários de governança e resiliência.
- **Catraca de custódia** (`tests/test_arquitetura_custodia_kernel.py`): mapa de
  quem pode fechar run fora do Kernel, com contagem e motivo. Caminho novo falha;
  quando a contagem cai, o teste também falha pedindo para travar o ganho.

#### Corrigido
- **Perda de trabalho no worktree**: `not commit.committed` confundia "nada em
  stage" com "commit falhou", e o segundo caso APAGAVA o worktree com trabalho
  dentro. Agora considera `changed_files`.
- **Corrida no `/stream`**: o `fail_run` de timeout sobrescrevia um run que a
  thread órfã já tinha concluído — o desfecho dependia de quem ganhasse a corrida.
  `fail_run_se_nao_terminal()` respeita estado terminal.
- **`bauer agent run-one`**: o tratador de erro quebrava ao reportar o erro
  (`console.print(err=True)`); passou a usar um `Console` de stderr.
- Barra de status e banner passaram a mostrar o modelo que de fato respondeu.

### Bauer OS (Sprint 24 — alpha)
- Home unificada: rota `/` do desktop agrega agentes ativos, aprovações pendentes, tarefas agendadas com falha, budget do dia e últimas execuções (`GET /api/os/home`).
- Command Palette com roteador de intenções por LLM: comandos livres ("abre o navegador e pesquisa docs do Agno") viram skill + inputs via slot `auxiliary.intent_router`, executados pelo SkillExecutor (policy → approval → eventos). Fallback determinístico preservado quando o LLM está indisponível.
- Atalhos de navegação do palette só disparam em comandos curtos; frases compostas vão pro roteador.
- `windows.browser`: aliases de navegador padrão ("default", "padrão", "system") não são mais tratados como executável.
- "Agno" removido das sugestões do palette ("status agno" → "status do runtime"); segue visível apenas em telas técnicas (RFC-005).

## [0.9.0b1] - 2026-07-08

### Adicionado
- Closed beta do Bauer Agent Runtime documentado em `docs/BETA_CLOSED.md`.
- Roadmap oficial do runtime em `docs/ROADMAP.md`.
- README atualizado com comandos principais do Runtime beta.
- Exemplo de `config.yaml` documenta `runtime.default_adapter` e `runtime.adapters`.
- RFC-005 Bauer OS aceito como shell/experience do closed beta.

### Runtime
- Adapter nativo, adapter Agno, runs, sessions, Event Bus, scheduler, dashboard, Windows Skill Pack e observability consolidados como escopo do beta.
- Demo de 5 minutos documentada para validar Agno, policy, approvals, eventos, audit log, scheduler, worker e kill switch.

### Compatibilidade
- Configs antigas sem `runtime.adapters` continuam validas porque o loader aplica defaults.

---

## [0.2.0] — 2026-06-25

### Segurança
- `serve.host` default alterado de `0.0.0.0` para `127.0.0.1` — bind local por padrão
- `bauer doctor` emite `[AVISO DE SEGURANÇA]` quando host externo + `api_key` vazio
- vite atualizado `5.4.11 → 6.4.3` — fecha 4 CVEs (GHSA-67mh-4wv8-2f99, GHSA-4w7w-66w2-5vf9, GHSA-v6wh-96g9-6wx3, GHSA-fx2h-pf6j-xcff), 2 deles Windows-specific

### Adicionado
- CI: job `lint-critical` bloqueia merge em erros E9/F63/F7/F82 (sintaxe + imports indefinidos)
- CI: job `install-check` valida `uv sync --all-extras` + imports críticos + coleta de testes
- CI: `npm audit --audit-level=high` bloqueante no job `desktop-build`
- `verify_app` é agora **gate obrigatório** para `Gate.DELIVERY` — entrega sem smoke run verde não avança
- `verify_log.jsonl` — trilha de auditoria de todas as tentativas de verificação (máx 3 por sessão)
- `verify_result.json` ganhou campos `smoke_passed: bool` e `attempts: int`
- `log_suppressed(context, exc)` em `bauer/logging_config.py` — supressões intencionais com rastro em DEBUG
- `.editorconfig` — UTF-8, LF, indent por tipo de arquivo (4 py / 2 yaml/toml/json)
- `[tool.ruff.lint]` versionado em `pyproject.toml` — `select`/`ignore` saem do CI YAML e entram no repo

### Corrigido
- `cli.py:_start_embedded_server` usava `config` (variável do escopo externo) — renomeado para `config_path`
- 13 erros F821 em `agent.py`, `cli.py`, `tool_router.py`, `dag_renderer.py`
- `tool_router.py:2649` — `raw: Any = None` com `Any` não importado
- `agent.py:2641` — `Path` não importado no escopo local da função

### Documentação
- README: seção "Setup em 3 comandos" com `uv` + aviso de conflito `bauer.exe` no Windows

---

## [0.1.0] — 2026-04-15

Lançamento inicial. Principais capacidades:

### Adicionado
- CLI completa (`bauer chat`, `bauer agent`, `bauer serve`, `bauer gateway`, `bauer doctor`)
- Multi-provider: Anthropic, OpenAI, Google, Groq, OpenRouter e 10+ providers
- Fallback automático em 3 camadas (retry backoff → 429 fallback → provider index)
- App Factory com gate pipeline: DISCOVERY → PLANNING → IMPLEMENTATION → DELIVERY
- `verify_app` (stack detection + smoke run + 14 regras de diagnóstico + Delivery Score 11 checks)
- Loop fingerprint (args_sig MD5[:8]) + task ledger (TASKS.md no system prompt)
- Gateway Telegram/Discord com suporte a mídia, streaming e botões
- Desktop Tauri v2: SPA 8 telas + auto-update via GitHub Releases
- Autonomia: IterationBudget
- Kanban SQLite com DAG, swarm e decomposição por LLM
- Memory providers: vetorial com isolamento por workspace
- 5110+ testes

---

*Para a política de versionamento e checklist de release, ver [docs/release.md](docs/release.md).*
