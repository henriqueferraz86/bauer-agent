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
