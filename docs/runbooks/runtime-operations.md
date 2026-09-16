# Bauer Runtime Operations Runbook

## Daily Runtime

Start the always-on supervisor. It manages dispatcher, cron, gateway outbox delivery,
and the Kanban dashboard as child services:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli runtime start --workspace workspace
```

Inspect and stop the supervised runtime:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli runtime status --workspace workspace
.\.venv\Scripts\python.exe -m bauer.cli runtime logs --workspace workspace --service dispatcher --lines 80
.\.venv\Scripts\python.exe -m bauer.cli runtime stop --workspace workspace
```

## Governed Autopilot

The persistent autopilot is opt-in. It works only from a declared mission or
persisted goals, creates `READY` Kanban tasks, and leaves execution to the
existing dispatcher/Kernel path. Free model proposals remain disabled by
default.

```yaml
autopilot:
  enabled: true
  mission: "Deliver the declared MVP"
  poll_interval_s: 30
  max_active_goals: 1
  max_replans_per_goal: 1
  approval_mode: threshold
  max_minutes: 30
  max_tool_calls: 500
  max_cost_usd: 2.0
```

Start it explicitly or let `runtime start` read `autopilot.enabled`:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli runtime start --workspace workspace --autopilot
.\.venv\Scripts\python.exe -m bauer.cli runtime status --workspace workspace --json
.\.venv\Scripts\python.exe -m bauer.cli runtime autopilot-control pause --workspace workspace
.\.venv\Scripts\python.exe -m bauer.cli runtime autopilot-control resume --workspace workspace
.\.venv\Scripts\python.exe -m bauer.cli runtime autopilot-control replan --workspace workspace
```

Use `bauer runtime kill-switch on` to prevent new autonomous work. A paused,
blocked, failed or budget-exhausted controller leaves its state under
`workspace/.bauer_runtime/autopilot.json` for inspection and safe recovery.

## Fleet multi-projeto

Para supervisionar continuamente bugs, melhorias, manutenção e qualidade dos
projetos Bauer dentro de `~/.bauer/workspace`, use o bootstrap idempotente
`fleet up`. Ele prepara `~/.bauer/config.yaml`, aplica somente defaults
ausentes (missão global, `autopilot.enabled`, `approval_mode: threshold`,
`fleet.enabled` e a raiz), descobre os projetos e inicia o supervisor em uma
única chamada. Não grava segredos e não altera tarefas TODO.

O comando funciona a partir de qualquer diretório; os defaults de config e
models são sempre canônicos (`~/.bauer/config.yaml` e `~/.bauer/models.yaml`).
Use `--root` para uma raiz diferente ou `--mission` para substituir
explicitamente a missão:

```powershell
uv run bauer runtime fleet up
uv run bauer runtime fleet up --root D:\repos --mission "Revisar bugs e cobertura de testes"
```

Resumo e bloqueios aparecem ao final. Se faltar credencial, configure a
variável indicada no ambiente/.env; para Ollama, confirme o serviço e rode
`ollama pull <modelo>`. Se não houver projetos, o Fleet continua ativo e
passará a descobri-los nos próximos ciclos.

Para inspecionar sem iniciar processos, use `discover`; para consultar estado,
use `status`:

```powershell
uv run bauer runtime fleet discover --root "$env:USERPROFILE\.bauer\workspace"
uv run bauer runtime fleet status --root "$env:USERPROFILE\.bauer\workspace"
```

`discover` é somente inspeção. `status` é somente consulta. O fluxo antigo de
três comandos (`discover` → configurar YAML manualmente → `start`) foi
substituído por `up`; `start` permanece disponível para operação avançada.

Cada projeto recebe um runtime isolado:

```powershell
uv run bauer runtime fleet start --root "$env:USERPROFILE\.bauer\workspace"
uv run bauer runtime fleet status --root "$env:USERPROFILE\.bauer\workspace"
```

Cada projeto mantém o próprio `.bauer_runtime`, dispatcher, estado e logs. O
fleet mantém somente coordenação e estado global em `.bauer_fleet` na raiz.
Falha em um projeto não interrompe os demais. O Kanban HTTP fica desligado no
fleet por padrão para evitar conflito de portas; habilite `fleet.start_kanban`
somente se precisar dele.

Controles globais:

```powershell
uv run bauer runtime fleet pause --root "$env:USERPROFILE\.bauer\workspace"
uv run bauer runtime fleet resume --root "$env:USERPROFILE\.bauer\workspace"
uv run bauer runtime fleet kill-switch on --root "$env:USERPROFILE\.bauer\workspace"
uv run bauer runtime fleet stop --root "$env:USERPROFILE\.bauer\workspace"
```

Use `fleet.include` e `fleet.exclude` no config para transformar a seleção em
uma allowlist explícita quando a raiz contiver pastas que não devem ser
supervisionadas.

O dispatcher preserva a semântica de segurança do Kanban: TODO continua fora
da fila. O Autopilot só materializa os passos da missão como READY e nenhuma
rotina Fleet promove TODO existente arbitrariamente; faça essa transição por
uma decisão explícita do operador (`bauer task ready <id>` ou a interface
equivalente).

Manual fallback: start the durable automation scheduler and dispatcher in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli cron daemon --workspace workspace --interval 60
.\.venv\Scripts\python.exe -m bauer.cli dispatch daemon --workspace workspace --interval 5 --max-spawn 1 --max-in-progress 2
```

Inspect health:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli ops status --workspace workspace
.\.venv\Scripts\python.exe -m bauer.cli gateway-outbox --workspace workspace
```

## Recovery

Return crashed/stale workers to READY:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli dispatch reclaim --workspace workspace
```

Retry failed work:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli dispatch retry 001 --workspace workspace
```

Resume a durable orchestration:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli orchestrate resume <run_id> --workspace workspace
```

## Gateway Delivery

Configure named outbound channels. Secrets stay in environment variables, not in
workspace files:

```powershell
$env:TELEGRAM_BOT_TOKEN="..."
.\.venv\Scripts\python.exe -m bauer.cli gateway-channel-add alerts telegram 123456 --workspace workspace --metadata-json '{"token_env":"TELEGRAM_BOT_TOKEN"}'
.\.venv\Scripts\python.exe -m bauer.cli gateway-channels --workspace workspace
```

Send a manual message through a registered channel or direct platform target:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli gateway-send alerts "Runtime online" --workspace workspace --deliver-now
.\.venv\Scripts\python.exe -m bauer.cli gateway-send webhook "Ping" --target "https://example.test/hook" --workspace workspace
```

Cron jobs can enqueue delivery intents through a registered channel:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli cron create daily-report "Generate daily report" --schedule "daily 09:00" --deliver "channel:alerts"
```

Deliver pending messages. The supervisor already runs this as the `outbox` service:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli gateway-deliver --workspace workspace
.\.venv\Scripts\python.exe -m bauer.cli gateway-deliver --workspace workspace --watch --interval 30
```

## Schema Ledger

Record and inspect sidecar schema baselines:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli ops migrations --workspace workspace
```

## Memory Recall

Build and query the FTS memory index:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli memory index --dir memory
.\.venv\Scripts\python.exe -m bauer.cli memory search "dispatcher crash" --fts --dir memory
```

## Research Trajectories

Append manually curated trajectories:

```powershell
.\.venv\Scripts\python.exe -m bauer.cli research trajectory-add "Investigate bug" --kind debug --input-json "{}" --output-json "{}"
.\.venv\Scripts\python.exe -m bauer.cli research trajectory-list
```
