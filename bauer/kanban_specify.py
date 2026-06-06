"""Promote a triage task to a structured todo via the auxiliary LLM.

A *triage* task is one the user drops into the board as a rough idea — title
plus maybe a free-form body. `specify_task()` calls the `triage_specifier`
auxiliary slot to rewrite it as a structured, actionable specification:

    **Goal**
    What the task should achieve.

    **Approach**
    How to get there.

    **Acceptance criteria**
    What "done" looks like.

    **Out of scope**
    What is NOT being done now.

The task is also transitioned `triage → todo`, so subsequent `recompute_ready`
calls can flow it through the dispatcher.

Public surface::

    from bauer.kanban_specify import specify_task

    outcome = specify_task("042")
    if outcome.ok:
        print(f"Promoted {outcome.task_id}; new title: {outcome.title}")
    else:
        print(f"Couldn't promote: {outcome.reason}")

The function is best-effort:
    - If the auxiliary slot is unconfigured / unreachable, the call returns
      ok=False without touching the task — caller can prompt the user to
      pick the model.
    - If the LLM returns malformed JSON, we still try to extract a body
      paragraph; on total failure we return ok=False without rewriting.
    - Idempotent on already-specified tasks (no triage → no-op, returns ok).

Inspired by Hermes Agent's hermes_cli/kanban_specify.py.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from . import kanban_db as kb
# Imported at module level so tests can `patch("bauer.kanban_specify.get_text_auxiliary_client")`.
# The auxiliary client is cheap to import — no provider connection yet.
from .auxiliary_client import get_text_auxiliary_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class SpecifyOutcome:
    """Result of `specify_task()`. `ok` is the single signal of success.

    - ok=True, title=..., body=...: the task was rewritten
    - ok=True, reason="not_triage": task wasn't in triage; no change
    - ok=False, reason=...: something prevented the specify; task unchanged
    """
    task_id: str
    ok: bool
    title: str = ""
    body: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


# System prompt for the auxiliary LLM. Plain English on purpose — small
# models (gpt-4o-mini, llama-3.x-8b) follow these patterns reliably without
# needing few-shot examples to bloat the request.
_SYSTEM_PROMPT = """You are an engineering task specifier. Rewrite a rough \
triage idea into a tight, actionable specification a developer can pick up.

INPUT: a task dict with `id`, `title`, and `body` (any of these may be \
incomplete or vague).

OUTPUT: a single JSON object with exactly two keys:
  {
    "title": "imperative-voice phrase, <= 80 chars, no trailing punctuation",
    "body":  "markdown text with four sections, see template below"
  }

BODY TEMPLATE (use these exact section headers, in this order):

**Goal**
<one short paragraph: what success looks like for the user>

**Approach**
<bulleted list of 2-5 concrete steps; reuse existing modules when possible>

**Acceptance criteria**
<bulleted list: concrete tests / observable behaviours that prove the goal \
is met>

**Out of scope**
<bulleted list: explicit non-goals to prevent feature creep; "none" is OK>

RULES:
- Output ONLY valid JSON. No prose before or after.
- Preserve the substance of the input; do not invent requirements.
- If the input is already well-specified, normalise its formatting but \
keep the content.
- Keep the title imperative ("Add auth to /api" not "Adding auth to /api").
"""

# Hard cap so we don't blow the context window on a runaway triage paragraph.
_MAX_INPUT_BODY_CHARS = 4000


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull a JSON object from a (possibly noisy) LLM response.

    Tolerates: ``` fences, leading prose, trailing commentary. Returns the
    first valid JSON object found, or None when nothing parses.
    """
    if not text:
        return None
    stripped = _CODE_FENCE_RE.sub("", text.strip()).strip()
    # Easy path — entire response is JSON.
    try:
        out = json.loads(stripped)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        pass
    # Find the largest brace-balanced substring.
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return None
    candidate = stripped[first:last + 1]
    try:
        out = json.loads(candidate)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _coerce_title(value: Any, fallback: str) -> str:
    """Trim, single-line, max 80 chars; fall back if empty."""
    if not isinstance(value, str):
        return fallback
    title = re.sub(r"\s+", " ", value).strip()
    if not title:
        return fallback
    return title[:80]


def _coerce_body(value: Any, fallback: str) -> str:
    """Trust the LLM body but fall back to the original on empty / wrong type."""
    if not isinstance(value, str):
        return fallback
    body = value.strip()
    return body or fallback


# ---------------------------------------------------------------------------
# specify_task — main entry point
# ---------------------------------------------------------------------------


def specify_task(
    task_id: str,
    *,
    board: str | None = None,
    cfg=None,
    author: str = "auxiliary",
) -> SpecifyOutcome:
    """Promote a triage task into a structured todo via the auxiliary LLM.

    Args:
        task_id: Task to specify. Must exist on the board.
        board: kanban_db board name. None = active board.
        cfg: BauerConfig. None = autoload from `config.yaml`.
        author: Comment / event author label for audit trail.

    Returns:
        `SpecifyOutcome`. Inspect `.ok` first; `.reason` is set on failure.
        Reasons returned:
            - "task_not_found": no row with that id
            - "not_triage": the task isn't in 'triage' (no-op success-ish)
            - "auxiliary_unavailable": LLM slot not configured or unreachable
            - "llm_invalid_json": response wasn't parseable JSON
            - "internal_error": exception during DB write

    Side effects on success:
        - tasks.title and tasks.body updated
        - tasks.status flipped triage → todo
        - audit comment added ("Spec'd by <author>")
        - audit event recorded (kind="task.specified")
    """
    try:
        with kb.connect(board) as conn:
            kb.init_db(conn)
            task = kb.get_task_or_none(conn, task_id)
            if task is None:
                return SpecifyOutcome(task_id, ok=False, reason="task_not_found")

            # Idempotency: only triage tasks get rewritten. Re-specifying a
            # todo would risk overwriting human edits.
            if task.status != kb.STATUS_TRIAGE:
                return SpecifyOutcome(
                    task_id, ok=True, title=task.title, body=task.body,
                    reason="not_triage",
                )

            client, model = get_text_auxiliary_client("triage_specifier", cfg)
            if client is None or not model:
                return SpecifyOutcome(
                    task_id, ok=False, reason="auxiliary_unavailable",
                )

            raw_response = _call_specifier(client, model, task.id, task.title,
                                            task.body)
            parsed = _extract_json(raw_response or "")
            if not parsed:
                logger.info("kanban_specify: invalid JSON from auxiliary; "
                            "raw=%r", (raw_response or "")[:300])
                return SpecifyOutcome(task_id, ok=False, reason="llm_invalid_json")

            new_title = _coerce_title(parsed.get("title"), task.title)
            new_body = _coerce_body(parsed.get("body"), task.body)

            # All writes happen in the same txn so we never end up with a
            # partial commit (status flipped but title not updated, etc.).
            kb.update_task_metadata(
                conn, task.id,
                title=new_title,
                body=new_body,
            )
            # CAS transition: only succeeds if status is still 'triage'.
            kb.update_status(
                conn, task.id, kb.STATUS_TODO,
                expected_status=kb.STATUS_TRIAGE,
            )
            kb.add_event(
                conn, task.id, kind="task.specified",
                payload={"author": author, "model": model},
            )
            kb.add_comment(
                conn, task.id,
                f"Specified via {model} (triage → todo)",
                author=author,
            )

            return SpecifyOutcome(
                task_id=task.id, ok=True,
                title=new_title, body=new_body,
            )
    except Exception as exc:
        # Production environments should never see a kanban_specify exception
        # bubble up — the caller is usually a UI flow. Log and return failure.
        logger.warning("kanban_specify(%r): %s", task_id, exc)
        return SpecifyOutcome(task_id, ok=False, reason=f"internal_error: {exc}")


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _call_specifier(client, model: str, task_id: str, title: str,
                     body: str) -> str:
    """Build the chat payload and join the streamed response."""
    user_payload = {
        "id": task_id,
        "title": title or "",
        "body": (body or "")[:_MAX_INPUT_BODY_CHARS],
    }
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    chunks: list[str] = []
    try:
        for chunk in client.chat_stream(model, messages):
            chunks.append(chunk)
            # Defensive cap: a well-behaved spec is <2000 chars. Cut the
            # stream after 6000 to bound worst-case context bloat.
            if sum(len(c) for c in chunks) > 6000:
                break
    except Exception as exc:
        logger.info("kanban_specify: chat_stream raised: %s", exc)
        return ""
    return "".join(chunks)


# ---------------------------------------------------------------------------
# Convenience: list candidates
# ---------------------------------------------------------------------------


def list_triage_ids(board: str | None = None) -> list[str]:
    """Return the IDs of every task currently in 'triage' on the board.

    Useful for CLI flows that offer a multi-select before calling
    `specify_task` per id.
    """
    with kb.connect(board) as conn:
        kb.init_db(conn)
        tasks = kb.list_tasks(conn, status=kb.STATUS_TRIAGE)
        return [t.id for t in tasks]
