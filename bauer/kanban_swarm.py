"""Multi-agent swarm topology over kanban_db tasks.

A swarm runs a fixed-shape DAG of LLM agents over the kanban_db kernel:

    Root (done, coordinator)
        ├── Worker 1 (ready)
        ├── Worker 2 (ready)
        └── Worker N (ready)
              ↓ (verifier waits on all workers)
        Verifier (todo)
              ↓ (synthesizer waits on verifier passing)
        Synthesizer (todo)

The pattern is borrowed from Hermes Agent's `hermes_cli/kanban_swarm.py`.

Why a swarm not just a decompose?
    - Swarm encodes a *role-based* DAG: workers do, verifier reviews, synthesizer
      ships. Each role has a known prompt and assignee.
    - Workers run in parallel without any shared state at task level. Coordination
      flows through structured comments on the root ("blackboard").
    - The verifier gates progression: it can fail back to `blocked` instead of
      ushering a half-done synthesis.

This module deals strictly with topology + persistence. It doesn't run the
agents — that's the dispatcher's job (`bauer dispatch once`). The swarm is
just kanban tasks with the right linking.

Public surface::

    from bauer.kanban_swarm import create_swarm, post_blackboard_update, latest_blackboard

    swarm = create_swarm(
        goal="Implement OAuth login",
        workers=["Auth API", "Login UI", "Tests"],
        verifier="Verify integration end-to-end",
        synthesizer="Ship changelog + docs",
    )
    print(f"root={swarm.root_id} workers={swarm.worker_ids} ...")

    post_blackboard_update(swarm.root_id, key="api_endpoint", value="https://...")
    snapshot = latest_blackboard(swarm.root_id)
    # → {"api_endpoint": "https://..."}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from . import kanban_db as kb

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Bounds on the worker count. < MIN doesn't justify swarm overhead; > MAX
# stresses the dispatcher's parallelism budget without helping.
MIN_WORKERS = 1
MAX_WORKERS = 8

# Prefix marking a comment as a structured blackboard update. We keep this
# narrow so user comments stay readable in the UI without us mis-parsing them.
_BLACKBOARD_PREFIX = "[swarm:blackboard] "
_BLACKBOARD_AUTHOR_DEFAULT = "swarm"

# Skill hints surfaced in the body of each agent role. Workers get no special
# skills; verifier and synthesizer get Hermes-style hints to nudge the LLM.
_VERIFIER_SKILL_HINT = "requesting-code-review"
_SYNTHESIZER_SKILL_HINT = "avoid-ai-writing"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class SwarmCreated:
    """All the IDs you need to drive the swarm after `create_swarm` returns."""
    root_id: str
    worker_ids: list[str] = field(default_factory=list)
    verifier_id: str = ""
    synthesizer_id: str = ""
    goal: str = ""


# ---------------------------------------------------------------------------
# Body templates
# ---------------------------------------------------------------------------


# Each role gets a short markdown body so any human or agent reviewing the
# kanban can see what they're supposed to do. The bodies are also what gets
# fed to the LLM by the dispatcher.

def _root_body(goal: str, n_workers: int) -> str:
    return (
        f"# Swarm root\n\n"
        f"**Goal:** {goal}\n\n"
        f"This task is the coordinator for a swarm of {n_workers} worker(s), "
        f"a verifier, and a synthesizer.\n\n"
        f"## Swarm protocol\n\n"
        f"- Workers run in parallel and do not depend on each other.\n"
        f"- Coordination happens via **blackboard comments on this root** "
        f"with the prefix `{_BLACKBOARD_PREFIX.strip()}` followed by JSON "
        f"`{{\"key\":\"...\",\"value\":...}}`.\n"
        f"- Verifier reviews ALL worker outputs before the synthesizer runs.\n"
        f"- Synthesizer merges into the final deliverable.\n"
    )


def _worker_body(title: str, goal: str, root_id: str, role_index: int,
                  total_workers: int) -> str:
    return (
        f"# Worker {role_index + 1}/{total_workers}\n\n"
        f"**Swarm goal:** {goal}\n\n"
        f"**Your slice:** {title}\n\n"
        f"## Protocol\n\n"
        f"- Read the latest blackboard on task `{root_id}` before starting "
        f"(comments prefixed with `{_BLACKBOARD_PREFIX.strip()}`).\n"
        f"- Publish any decision your siblings need with a blackboard comment "
        f"on `{root_id}` of the form "
        f"`{_BLACKBOARD_PREFIX.strip()} {{\"key\":\"...\",\"value\":...}}`.\n"
        f"- Complete this task with a metadata field summarising what you did.\n"
        f"- Do NOT depend on other workers — design your slice to be independent.\n"
    )


def _verifier_body(goal: str, worker_ids: list[str], root_id: str) -> str:
    worker_list = ", ".join(worker_ids)
    return (
        f"# Verifier\n\n"
        f"**Swarm goal:** {goal}\n\n"
        f"All workers ({worker_list}) have completed before this task is "
        f"`ready`. Review each worker's output (visible via "
        f"`bauer kanban show <id>`) and the blackboard on `{root_id}`.\n\n"
        f"## Gate\n\n"
        f"- Complete with metadata `{{\"gate\":\"pass\"}}` if everything is "
        f"consistent and shippable.\n"
        f"- Otherwise, BLOCK this task with a comment explaining what needs "
        f"redoing. The synthesizer will not run while you're blocked.\n\n"
        f"_Skill hint:_ `{_VERIFIER_SKILL_HINT}`\n"
    )


def _synthesizer_body(goal: str, verifier_id: str, root_id: str) -> str:
    return (
        f"# Synthesizer\n\n"
        f"**Swarm goal:** {goal}\n\n"
        f"The verifier (`{verifier_id}`) has passed. Read every worker's "
        f"summary and the blackboard on `{root_id}` and produce the final "
        f"deliverable.\n\n"
        f"## Output\n\n"
        f"- Compose the artefact (PR description, doc, changelog, etc.).\n"
        f"- Post it as your completion summary.\n\n"
        f"_Skill hint:_ `{_SYNTHESIZER_SKILL_HINT}`\n"
    )


# ---------------------------------------------------------------------------
# create_swarm — main entry point
# ---------------------------------------------------------------------------


def create_swarm(
    goal: str,
    workers: list[str],
    *,
    verifier: str | None = None,
    synthesizer: str | None = None,
    board: str | None = None,
    priority: str = "high",
    worker_assignee: str = "",
    verifier_assignee: str = "",
    synthesizer_assignee: str = "",
) -> SwarmCreated:
    """Create the swarm DAG on a kanban_db board. Returns the new IDs.

    Args:
        goal: One-sentence description of what the swarm should achieve.
            Becomes the root task title.
        workers: List of worker slice descriptions (each becomes a worker
            task title). Must have between MIN_WORKERS and MAX_WORKERS items.
        verifier: Optional verifier task title. Defaults to "Verify <goal>".
        synthesizer: Optional synthesizer task title. Defaults to
            "Synthesise <goal>".
        board: kanban_db board name. None = active board.
        priority: Priority applied to root + every spawned task.
        worker_assignee / verifier_assignee / synthesizer_assignee: optional
            assignee strings written to the respective tasks.

    Returns:
        `SwarmCreated` with all the IDs you need to drive subsequent calls.

    Raises:
        ValueError: when `goal` is empty or `workers` count is out of bounds.
    """
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("create_swarm: goal is required")

    titles = [t.strip() for t in workers if (t or "").strip()]
    if not (MIN_WORKERS <= len(titles) <= MAX_WORKERS):
        raise ValueError(
            f"create_swarm: need {MIN_WORKERS}-{MAX_WORKERS} workers, "
            f"got {len(titles)}"
        )

    verifier_title = (verifier or f"Verify swarm: {goal}").strip()
    synthesizer_title = (synthesizer or f"Synthesise swarm: {goal}").strip()

    with kb.connect(board) as conn:
        kb.init_db(conn)

        # Root: 'done' immediately so the dispatcher doesn't try to spawn it.
        # It's effectively a coordination anchor — its body documents the
        # swarm protocol and is where the blackboard lives.
        root_id = kb.create_task(
            conn,
            f"Swarm: {goal}",
            body=_root_body(goal, len(titles)),
            status=kb.STATUS_DONE,
            priority=priority,
            assignee=worker_assignee or "",
        )

        # Workers: 'ready' so the dispatcher can pick them up immediately.
        # No parents — they run in parallel.
        worker_ids: list[str] = []
        for idx, title in enumerate(titles):
            wid = kb.create_task(
                conn,
                title,
                body=_worker_body(title, goal, root_id, idx, len(titles)),
                status=kb.STATUS_READY,
                priority=priority,
                assignee=worker_assignee,
            )
            worker_ids.append(wid)

        # Verifier: 'todo' with all workers as parents. recompute_ready will
        # promote it to 'ready' once every worker is DONE.
        verifier_id = kb.create_task(
            conn,
            verifier_title,
            body=_verifier_body(goal, worker_ids, root_id),
            status=kb.STATUS_TODO,
            priority=priority,
            assignee=verifier_assignee,
        )
        for wid in worker_ids:
            kb.link_tasks(conn, wid, verifier_id)

        # Synthesizer: 'todo' with verifier as parent.
        synthesizer_id = kb.create_task(
            conn,
            synthesizer_title,
            body=_synthesizer_body(goal, verifier_id, root_id),
            status=kb.STATUS_TODO,
            priority=priority,
            assignee=synthesizer_assignee,
        )
        kb.link_tasks(conn, verifier_id, synthesizer_id)

        # Root waits on synthesizer (the only leaf in our DAG). When the
        # synthesizer completes, root's `parents_done` becomes true; since
        # the root is already in DONE, this is a no-op for the dispatcher
        # but documents the intent in task_links.
        kb.link_tasks(conn, synthesizer_id, root_id)

        # Audit trail on the root.
        kb.add_event(
            conn, root_id, kind="swarm.created",
            payload={
                "goal": goal,
                "workers": worker_ids,
                "verifier": verifier_id,
                "synthesizer": synthesizer_id,
            },
        )

    return SwarmCreated(
        root_id=root_id,
        worker_ids=worker_ids,
        verifier_id=verifier_id,
        synthesizer_id=synthesizer_id,
        goal=goal,
    )


# ---------------------------------------------------------------------------
# Blackboard — structured comment IPC on the root task
# ---------------------------------------------------------------------------


def post_blackboard_update(
    root_id: str,
    *,
    key: str,
    value: Any,
    author: str = _BLACKBOARD_AUTHOR_DEFAULT,
    board: str | None = None,
) -> int:
    """Write a key=value pair to the swarm's blackboard.

    Implemented as a structured comment on the root task: any party with
    access to kanban_db can read the blackboard later via `latest_blackboard`.
    There's no locking — last writer wins (workers should pick keys that
    don't collide).

    Args:
        root_id: Swarm root task ID (from `SwarmCreated.root_id`).
        key: Identifier for this entry. Must be non-empty.
        value: JSON-serialisable value. Anything that `json.dumps` accepts.
        author: Comment author label (defaults to 'swarm'). Workers
            typically pass their assignee name.
        board: kanban_db board. None = active board.

    Returns:
        The new comment row's rowid (positive integer).
    """
    if not key:
        raise ValueError("post_blackboard_update: key is required")
    try:
        payload = json.dumps({"key": str(key), "value": value},
                              ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not JSON-serialisable: {exc}") from exc
    body = _BLACKBOARD_PREFIX + payload
    with kb.connect(board) as conn:
        return kb.add_comment(conn, root_id, body, author=author)


def latest_blackboard(
    root_id: str,
    *,
    board: str | None = None,
) -> dict[str, Any]:
    """Read every blackboard entry, last-wins by key.

    Returns a flat dict mapping keys to their most-recently-posted values.
    Malformed comments are skipped silently so a single bad write doesn't
    corrupt the snapshot.
    """
    snapshot: dict[str, Any] = {}
    with kb.connect(board) as conn:
        for comment in kb.list_comments(conn, root_id):
            body = comment.get("body", "")
            if not isinstance(body, str) or not body.startswith(_BLACKBOARD_PREFIX):
                continue
            payload_str = body[len(_BLACKBOARD_PREFIX):]
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                logger.info("kanban_swarm: ignoring malformed blackboard "
                            "comment: %r", body[:80])
                continue
            if not isinstance(payload, dict):
                continue
            key = payload.get("key")
            if not key:
                continue
            snapshot[str(key)] = payload.get("value")
    return snapshot


def blackboard_history(
    root_id: str,
    *,
    board: str | None = None,
) -> list[dict[str, Any]]:
    """Return every blackboard entry in posting order (oldest first).

    Useful for debugging: you can replay how a swarm reached its final state.
    Each item is `{"key": ..., "value": ..., "author": ..., "created_at": ...}`.
    """
    out: list[dict[str, Any]] = []
    with kb.connect(board) as conn:
        for comment in kb.list_comments(conn, root_id):
            body = comment.get("body", "")
            if not isinstance(body, str) or not body.startswith(_BLACKBOARD_PREFIX):
                continue
            payload_str = body[len(_BLACKBOARD_PREFIX):]
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            out.append({
                "key": payload.get("key"),
                "value": payload.get("value"),
                "author": comment.get("author", ""),
                "created_at": comment.get("created_at"),
            })
    return out


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def is_swarm_root(task_id: str, *, board: str | None = None) -> bool:
    """True when the given task was created as a swarm root."""
    with kb.connect(board) as conn:
        kb.init_db(conn)
        for event in kb.list_events(conn, task_id):
            if event.get("kind") == "swarm.created":
                return True
    return False


def swarm_summary(root_id: str, *, board: str | None = None) -> dict[str, Any]:
    """Snapshot of a swarm's current state — useful for CLI status output.

    Returns a dict::

        {
            "root_id": ...,
            "goal": ...,
            "workers": [{"id": ..., "title": ..., "status": ...}, ...],
            "verifier": {...},
            "synthesizer": {...},
            "blackboard": {key: value, ...},
        }
    """
    with kb.connect(board) as conn:
        kb.init_db(conn)
        events = kb.list_events(conn, root_id)
        created = next((e for e in events
                        if e.get("kind") == "swarm.created"), None)
        if not created:
            return {"root_id": root_id, "error": "not a swarm root"}
        payload = created.get("payload") or {}
        if not isinstance(payload, dict):
            return {"root_id": root_id, "error": "swarm event malformed"}

        def _snapshot(tid: str) -> dict[str, Any]:
            t = kb.get_task_or_none(conn, tid)
            return {"id": tid} if t is None else {
                "id": tid, "title": t.title, "status": t.status,
            }

        return {
            "root_id": root_id,
            "goal": payload.get("goal", ""),
            "workers": [_snapshot(w) for w in (payload.get("workers") or [])],
            "verifier": _snapshot(payload.get("verifier", "")),
            "synthesizer": _snapshot(payload.get("synthesizer", "")),
            "blackboard": latest_blackboard(root_id, board=board),
        }
