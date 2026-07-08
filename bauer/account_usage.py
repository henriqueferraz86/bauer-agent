"""Provider-agnostic LLM usage parsing — normalises `response.usage` payloads.

Different providers return slightly different shapes for token usage:

    OpenAI / Groq / Together / Mistral / DeepSeek / xAI:
        usage: {prompt_tokens, completion_tokens, total_tokens}
        Optional: prompt_tokens_details: {cached_tokens}

    Anthropic:
        usage: {input_tokens, output_tokens,
                cache_creation_input_tokens, cache_read_input_tokens}

    Gemini (OpenAI-compat endpoint):
        same as OpenAI

`normalize_usage()` converts any of these into a canonical dict:

    {
        "prompt_tokens": int,
        "completion_tokens": int,
        "total_tokens": int,
        "cache_read_input_tokens": int,
        "cache_creation_input_tokens": int,
    }

All keys are always present. Zero is the default. Use `merge_usage()` to
accumulate over multiple turns within a session.
"""

from __future__ import annotations

from typing import Any, Mapping


_CANONICAL_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def normalize_usage(raw: Any) -> dict[str, int]:
    """Convert any provider's usage payload into the canonical dict.

    Accepts:
        - `None` or non-mapping → returns all-zero dict.
        - Dict-like with OpenAI shape (prompt_tokens, completion_tokens).
        - Dict-like with Anthropic shape (input_tokens, output_tokens, cache_*).
        - Dict-like with mixed/partial fields.

    Returns:
        Dict with the 5 canonical keys, all ints, all >= 0.
        `total_tokens` is recomputed as `prompt_tokens + completion_tokens` when
        the provider didn't supply it (or supplied an incorrect value).
    """
    if not isinstance(raw, Mapping):
        return {k: 0 for k in _CANONICAL_KEYS}

    # Coalesce known synonyms for prompt/completion tokens.
    prompt = _coerce_int(
        raw.get("prompt_tokens")
        or raw.get("input_tokens")
        or raw.get("promptTokens")
        or raw.get("inputTokens")
        or 0
    )
    completion = _coerce_int(
        raw.get("completion_tokens")
        or raw.get("output_tokens")
        or raw.get("completionTokens")
        or raw.get("outputTokens")
        or 0
    )

    # OpenAI nests cached_tokens under prompt_tokens_details.
    cache_read = _coerce_int(raw.get("cache_read_input_tokens") or 0)
    if not cache_read:
        details = raw.get("prompt_tokens_details") or raw.get("promptTokensDetails") or {}
        if isinstance(details, Mapping):
            cache_read = _coerce_int(details.get("cached_tokens") or details.get("cachedTokens") or 0)

    cache_create = _coerce_int(
        raw.get("cache_creation_input_tokens")
        or raw.get("cacheCreationInputTokens")
        or 0
    )

    # Total: trust provider if supplied AND consistent, else recompute.
    provider_total = _coerce_int(raw.get("total_tokens") or raw.get("totalTokens") or 0)
    derived_total = prompt + completion
    total = provider_total if provider_total >= derived_total else derived_total

    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_create,
    }


def merge_usage(*usages: Mapping[str, int]) -> dict[str, int]:
    """Sum N normalized usage dicts into a single accumulator.

    Each input should already be normalized via `normalize_usage()`. Missing
    keys default to 0. Use this to accumulate session totals across turns.

        session_total = merge_usage(turn1_usage, turn2_usage, turn3_usage)
    """
    out = {k: 0 for k in _CANONICAL_KEYS}
    for usage in usages:
        if not isinstance(usage, Mapping):
            continue
        for k in _CANONICAL_KEYS:
            out[k] += _coerce_int(usage.get(k, 0))
    return out


def usage_is_empty(usage: Mapping[str, int]) -> bool:
    """True if no tokens were consumed (e.g. provider didn't return usage)."""
    if not isinstance(usage, Mapping):
        return True
    return all(_coerce_int(usage.get(k, 0)) == 0 for k in _CANONICAL_KEYS)


def cache_hit_ratio(usage: Mapping[str, int]) -> float:
    """Fraction of prompt tokens served from cache. 0.0 if no caching."""
    if not isinstance(usage, Mapping):
        return 0.0
    prompt = _coerce_int(usage.get("prompt_tokens", 0))
    if prompt <= 0:
        return 0.0
    cached = _coerce_int(usage.get("cache_read_input_tokens", 0))
    return min(1.0, cached / prompt)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> int:
    """Best-effort int coercion. Returns 0 for None/invalid types."""
    if value is None:
        return 0
    if isinstance(value, bool):
        # bool is a subclass of int — guard separately to avoid surprises.
        return int(value)
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    try:
        return max(0, int(str(value).strip()))
    except (ValueError, TypeError):
        return 0
