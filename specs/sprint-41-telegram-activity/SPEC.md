# Sprint 41 — Telegram: atividade dos agentes

## Goal

Let the authorized operator inspect the real activity of the current Telegram
project conversation and refresh that view in place, using persisted runtime
runs/events rather than a second task tracker.

## Scope

- Add private-chat `/atividade` handling before normal message queuing.
- Read the selected project's `session_key` and query
  `build_chat_activity(RunManager, EventBus, session_id)`.
- Render task, run state, agent/delegation task and activity, current tool,
  known cost or an explicit unknown-cost label, and a redacted terminal error
  summary where useful.
- Send one card per returned run with a manual `Atualizar` button for active
  runs. Each card stays pinned to that exact run even after newer runs start.
  While its run is active, coalesce persisted lifecycle events into rate-limited
  edits and send the terminal summary. The callback is bound to authorized user,
  private chat, message, project, session and run; it is read-only and may be
  used repeatedly until its short expiry.
- Isolate Telegram send/edit/API errors from governed task execution.

## Out of scope

- Background polling or streaming Telegram edits.
- Approval controls, task cancellation, or starting `/loop` from this card.
- Group and topic support for privileged controls.
- A new activity database or any provider/model calls.

## Acceptance

- `/atividade` displays only the current private conversation's project/session
  runs; another operator, chat, project or session cannot be queried through
  the callback.
- The card shows real RunManager/EventBus state, including repeated
  delegations as separate tasks, and never displays raw JSON, prompts, tool
  arguments/results, internal stack traces or secrets.
- Cost appears only when the runtime marks usage known; otherwise the card
  says it is unknown.
- Expired, malformed or mismatched callbacks cannot edit another card or
  reveal activity. A valid refresh updates the same Telegram message.
- Telegram API failure leaves the task and its Kernel state unchanged.
- Coalesced edits respect Telegram's edit interval; a failed intermediate edit
  does not suppress the final summary.
- Every card remains below Telegram's 4096-character message limit with a
  bounded total rendering budget and an explicit omitted-agent count.

## Validation

Use fake Telegram API responses, `tmp_path`, real temporary RunManager/EventBus
stores and fake providers. Cover private authorization, project/session
isolation, callback binding/expiry, redaction and API failure. Run the full
repository gates before closing this sprint.
