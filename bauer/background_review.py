"""G10 — Background Review.

After each agent turn, a daemon thread evaluates the assistant response for
quality issues (incomplete answer, missed tool call, factual inconsistency).
Results are logged to ~/.bauer/review_log.jsonl and never shown to the user.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

try:
    from .auxiliary_client import call_aux_text
except Exception:
    call_aux_text = None  # type: ignore[assignment]

_REVIEW_PROMPT = """\
You are a silent quality reviewer for an AI assistant. Analyse the exchange below and respond with a single JSON object — no markdown, no preamble.

User asked: {user_input}

Tools used: {tool_summary}

Assistant responded: {assistant_response}

JSON schema:
{{
  "quality": "good" | "incomplete" | "off_topic" | "error",
  "issues": ["<short description>"],
  "suggestions": ["<actionable recommendation for the assistant>"]
}}

Rules:
- "good": answer is complete and correct given the context.
- "incomplete": the user's question was partially answered or a tool call was obviously missing.
- "off_topic": the response addresses something other than what was asked.
- "error": the response contains a factual error visible from the tool results.
- issues and suggestions must be non-empty only when quality != "good".
- Keep each string under 80 chars. Respond ONLY with the JSON object."""


# ── Public API ────────────────────────────────────────────────────────────────

@dataclass
class ReviewResult:
    quality: Literal["good", "incomplete", "off_topic", "error"]
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    raw: str = ""

    @classmethod
    def good(cls) -> "ReviewResult":
        return cls(quality="good")

    @classmethod
    def from_json(cls, data: dict, raw: str = "") -> "ReviewResult":
        q = data.get("quality", "good")
        if q not in ("good", "incomplete", "off_topic", "error"):
            q = "good"
        return cls(
            quality=q,  # type: ignore[arg-type]
            issues=list(data.get("issues") or []),
            suggestions=list(data.get("suggestions") or []),
            raw=raw,
        )


def review_turn(
    user_input: str,
    assistant_response: str,
    tool_log: list[dict],
    *,
    workspace: str = "",
    session_id: str = "",
    cfg=None,
) -> None:
    """Schedule a background quality review of the completed turn.

    Returns immediately — all work runs in a daemon thread. Never raises.
    """
    if not user_input or not assistant_response:
        return
    if len(assistant_response.strip()) < 20:
        return
    if user_input.strip().startswith("/"):
        return

    log_path = Path.home() / ".bauer" / "review_log.jsonl"

    t = threading.Thread(
        target=_do_review,
        args=(user_input, assistant_response, tool_log, log_path, cfg, session_id),
        daemon=True,
    )
    t.start()


# ── Internal ──────────────────────────────────────────────────────────────────

def _do_review(
    user_input: str,
    assistant_response: str,
    tool_log: list[dict],
    log_path: Path,
    cfg,
    session_id: str,
) -> None:
    try:
        result = _run_review(user_input, assistant_response, tool_log, cfg)
        if result is not None:
            _append_log(log_path, user_input, assistant_response, result, session_id)
    except Exception as exc:
        logger.debug("background_review._do_review: %s", exc)


def _run_review(
    user_input: str,
    assistant_response: str,
    tool_log: list[dict],
    cfg,
) -> ReviewResult | None:
    if call_aux_text is None:
        return None

    tool_summary = _summarise_tool_log(tool_log)
    prompt = _REVIEW_PROMPT.format(
        user_input=user_input[:400],
        tool_summary=tool_summary,
        assistant_response=assistant_response[:600],
    )
    messages = [{"role": "user", "content": prompt}]

    raw = call_aux_text("background_reviewer", messages, cfg=cfg, fallback="")
    if not raw:
        return None

    return ReviewResult.from_json(_parse_review_response(raw), raw=raw)


def _parse_review_response(raw: str) -> dict:
    """Extract a JSON object from the LLM response; fall back to 'good'."""
    raw = raw.strip()
    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Try to extract first {...} block
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {"quality": "good", "issues": [], "suggestions": []}


def _summarise_tool_log(tool_log: list[dict]) -> str:
    if not tool_log:
        return "none"
    names = [entry.get("tool", "?") for entry in tool_log[:5]]
    suffix = f" (+{len(tool_log) - 5} more)" if len(tool_log) > 5 else ""
    return ", ".join(names) + suffix


def _append_log(
    log_path: Path,
    user_input: str,
    assistant_response: str,
    result: ReviewResult,
    session_id: str,
) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "session_id": session_id,
            "quality": result.quality,
            "issues": result.issues,
            "suggestions": result.suggestions,
            "user_snippet": user_input[:80],
            "response_snippet": assistant_response[:80],
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("background_review._append_log: %s", exc)
