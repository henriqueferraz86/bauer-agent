# Architecture — Sprint 41

The bridge handles `/atividade` on the update thread after the existing
allowlist check and before it submits ordinary user turns to the worker pool.
It requires a private Telegram chat and a valid selected project context. The
project context supplies the same session key used by chat history and
governed runs.

The handler asks the injected channel backend for the read-only activity
projection backed by the runtime `RunManager` and persistent `EventBus`. The
projection stays the source of task/member state and performs redaction and
bounds. The handler sends one card per run returned by that session-filtered
projection. Each refresh token remains pinned to its recorded `run_id`; it
never silently switches to a newer run. A small Telegram formatter turns each
run into readable Portuguese text within a total character budget below 4096,
showing how many members were omitted;
it never serializes the projection as JSON. Cost is shown only when
`usage_known` is true.

Refresh callbacks use opaque random tokens stored in a bounded in-memory map
with a ten-minute TTL. Each record pins operator, chat, Telegram message,
project, session and run. Every callback rechecks authorization, private chat,
message and current project binding before rebuilding the projection and
editing the same message. The callback is intentionally repeatable and has no
runtime side effects. Refreshes and any live event edit build a fresh
`ChannelMessage` without a saved project snapshot, then resolve and validate
the current binding and pinned workspace before reading activity.

Register one local EventBus subscriber per project bus for the lifetime of the
bridge, rather than one subscriber per callback. Filter notifications by the
pinned session and run, coalesce changes, and schedule edits no faster than
`STREAM_EDIT_INTERVAL_S`. Each edit reads the persisted projection. A terminal
event schedules a final summary edit; Telegram failures stay presentation-only.
Do not render raw `run.error`: map known terminal reasons to fixed safe text
and use a generic message for all other errors.

Telegram send/edit exceptions are caught at this presentation boundary. The
bridge may answer with a brief retry instruction, but it does not alter run,
approval, budget or Kernel state.
