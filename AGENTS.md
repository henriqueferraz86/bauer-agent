# AGENTS.md — guia para agentes que trabalham neste repositório

Bauer Agent é um **runtime adaptativo para LLMs locais e cloud** (Python 3.11+):
sobe com o que tem, ajusta o que precisa, avisa claramente. CLI em Typer,
servidor HTTP em FastAPI, e um Kernel de execução governada opt-in.

## Como verificar (rode ANTES de abrir PR)

```bash
# Ambiente (uv é o gerenciador; o lock é versionado)
uv sync --all-extras --frozen        # instala do uv.lock (reproduzível)
# ou, para dev:  pip install -e ".[dev]"

# Testes (a rede de segurança — não há type-checker no CI)
pytest tests/ -q --tb=short

# Lint BLOQUEANTE (o único que barra merge)
ruff check bauer/ --select E9,F63,F7,F82

# Lint informativo (não bloqueia)
ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
```

- A suíte é hermética por design: `tests/conftest.py` aponta `BAUER_CONFIG`/
  `BAUER_HOME`/`BAUER_AGENTS_FILE` para caminhos inexistentes, então nenhum
  teste toca provider real. **Não reintroduza** carga de config real em teste
  (já causou CI de 5min→3.5h uma vez).
- CI roda em **ubuntu-latest** (Python 3.11 e 3.12). Cuidado: código
  Windows-específico não é validado no CI — teste localmente no Windows quando
  mexer em paths/subprocess/keyring.
- Escreva testes junto com o código; siga o teste vizinho como padrão. Use
  `tmp_path`/`monkeypatch`, nunca escreva na raiz do repo.

## Layout do pacote (`bauer/`)

| Área | Módulos-chave |
|------|---------------|
| CLI | `cli.py` (root Typer) + `commands/*.py` (um grupo por arquivo; `run_cmd.py` = `bauer run`) |
| Loop do agente | `agent.py` (loop interativo + slash-commands), `orchestrator.py` (DAG multi-passo) |
| Tools | `tool_router.py` + `tools/*.py` (mixins herdados pelo ToolRouter: fs, web, execution, kanban, media, memory, browser…) |
| Modo autônomo | `serve_loop.py` (motor de rodadas, compartilhado CLI+web), `autonomous_budget.py` (guardrails tempo/tools/custo) |
| Kernel (governança) | `core/kernel/` (kernel, states, evaluator), `core/runtime/` (scheduler, run_manager, autonomy, resilience, adapters), `core/policy/` — **opt-in via `kernel.enabled` (default False)** |
| Memória | `decision_memory.py`, `sqlite_session_store.py`, `memory_context.py` (prefetch/sync por turno), `embeddings.py` |
| Config | `config_loader.py` (Pydantic v2, seções estritas), `env_loader.py`, `paths.py` (`$BAUER_HOME`, default `~/.bauer/`) |
| Servidor | `server.py` (FastAPI: `/chat`, `/stream`, `/v1/chat/completions`, `/loop`, `/transcribe`), `web/` (dispatcher do chat web) |
| Canais | `channel_base.py`, `telegram_bridge.py`, `discord_bridge.py`, `gateway*.py` |
| Providers | `openai_client.py`, `anthropic_client.py`, `ollama_client.py`, `model_router.py` |

## Modelo mental do Kernel

O Kernel **consolida, não reimplementa** — é o caminho de execução governada
(admissão, política, aprovação, runs auditáveis) e fica **desligado por padrão**
(`kernel.enabled: false`). Quando ligado, `kernel.admit(KernelRequest)` governa
o run; `bauer run` e o `/loop` da web já o integram. O runtime (`core/runtime/`)
tem um `scheduler` persistente + `run_manager` (estado em JSONL sob
`$BAUER_HOME/memory/runtime/`) + `autonomy` (budget/kill-switch). **Convive** com
`orchestrator.py` (a geração anterior, ainda o caminho real quando o Kernel está
off) — uma migração em andamento.

## Fluxos principais

- `bauer run "tarefa"` — autônomo de ponta a ponta na pasta atual. Workspace =
  CWD; config = canônico (`~/.bauer/config.yaml`), NUNCA o `config.yaml` do
  projeto. Guardrails: `--max-minutes` / `--max-tool-calls` / `--max-cost`.
- `bauer agent` — chat interativo com tools, memória e slash-commands.
- `bauer serve` — API HTTP + web UI; `/loop` no chat = modo autônomo no browser.

## Convenções

- **Erros de tool**: levante `ToolError` (de `bauer/tools/base.py`). Config:
  `ConfigError`. Não use `RuntimeError` cru em caminho de tool.
- **Logging**: `logging.getLogger("bauer.<modulo>")` para serviços; `console`
  (Rich) para UI de CLI. Evite `print` em código de biblioteca.
- **Best-effort em caminhos acessórios** (memória, custo, telemetria): capture,
  logue em DEBUG e siga — nunca deixe uma falha auxiliar quebrar o turno.
- **httpx no Windows**: use `verify=shared_ssl_context()` (`http_shared.py`) em
  todo call site novo — criar SSL context custa ~260ms por chamada senão.
- **Paths**: valide que é `str/bytes/Path` antes de escrever (um `MagicMock` ou
  config malformado vira lixo em disco); veja `memory_context._safe_workspace`.
- **Commits**: fixes pequenos vão direto no master; features novas via branch+PR.

## Planos de trabalho

`plans/` guarda planos de implementação (gerados por auditoria) com índice em
`plans/README.md` — cada um é autocontido, com critérios de verificação. Ao
executar um plano, siga-o inteiro, respeite as condições de STOP e atualize a
linha de status.
