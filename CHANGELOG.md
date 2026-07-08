# Changelog

Todas as mudanças notáveis são documentadas aqui.
Segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/) e [SemVer](https://semver.org/lang/pt-BR/).

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
- Autonomia: IterationBudget, CheckpointManager, MetricsRegistry, AuditTrail
- Kanban SQLite com DAG, swarm e decomposição por LLM
- Memory providers: vetorial com isolamento por workspace
- 5110+ testes

---

*Para a política de versionamento e checklist de release, ver [docs/release.md](docs/release.md).*
