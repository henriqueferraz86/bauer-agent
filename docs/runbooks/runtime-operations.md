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
