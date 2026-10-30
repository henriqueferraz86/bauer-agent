# Backlog — Sprint 41

- [x] Record scope, acceptance, storage/API contracts and failure behavior.
- [x] Add backend activity lookup bound to a freshly resolved project session.
- [x] Add `build_chat_activity` fields for cost known/unknown and safe failure
  summary if the existing projection does not provide them.
- [x] Add private `/atividade` command and one bounded card per returned run;
  active cards coalesce lifecycle edits and show a terminal summary.
- [x] Add opaque refresh callbacks bound to operator/chat/message/project/
  session/run with expiry and revalidation.
- [x] Add focused tests for projection fields, privacy, isolation, callback
  replay/mismatch/expiry and Telegram API failure.
- [x] Ensure refresh/live updates resolve a fresh project binding and workspace;
  never render raw `run.error`.
- [x] Enforce an explicit total text budget under Telegram's 4096-character
  limit and preserve each callback's original run binding.
- [x] Serialize live refreshes with an inflight/dirty coalescer, enforce the
  edit interval when timers fire, and stop terminal watches after fallback.
- [x] Share one EventBus across project Kernels per channel backend so router
  events reach the bridge subscriber without retaining a bus per workspace.
- [x] Separate the 10-minute callback TTL from the 24-hour live-watch TTL;
  terminal events continue to update cards after refresh callbacks expire.
- [x] Stop cancels activity timers, clears callback records, and prevents
  subscribers or in-flight refreshes from issuing activity HTTP afterward.
- [x] Bridge reset recreates closed HTTP/executor resources and resumes the
  existing EventBus subscriber without duplicate registration.
- [x] Serialize initial activity-card sends and command fallbacks with stop;
  in-flight sends finish before shutdown and later sends are suppressed.
- [x] Bind refreshes to a lifecycle generation and revalidate the current
  callback record immediately before edit/fallback, preventing stale HTTP after
  stop/reset.
- [x] Run focused tests and Ruff/mypy for modified modules.
- [x] Run all repository gates: frozen sync, full pytest suite, critical/style
  Ruff, changed-module mypy, `uv lock --check` and `git diff --check`.
- [x] Cross-review implementation and update Plan 061/index; review findings
  were fixed and revalidated with focused regressions.
