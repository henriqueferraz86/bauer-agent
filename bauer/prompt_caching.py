"""Anthropic prompt-caching support — applies cache_control breakpoints.

Anthropic's prompt caching allows up to 4 cache breakpoints per request. Hermes
uses the "system_and_3" pattern: cache the system prompt + the last 3 non-system
messages. This keeps the cache hot across multi-turn conversations while staying
within the 4-breakpoint limit.

The cache_control marker is `{"type": "ephemeral", "ttl": "5m" | "1h"}` — only
the literal string `"5m"` and `"1h"` are recognised by Anthropic at time of
writing.

This module is provider-aware: `apply_anthropic_cache_control()` is a no-op for
non-Anthropic clients (`should_apply_cache_control()` returns False).

Reference: agent/prompt_caching.py in Hermes Agent.

Usage::

    from bauer.prompt_caching import apply_anthropic_cache_control, should_apply_cache_control

    if should_apply_cache_control(client):
        api_messages = apply_anthropic_cache_control(api_messages, cache_ttl="5m")
    response = client.chat_stream(model, api_messages)
"""

from __future__ import annotations

import copy
from typing import Any, Mapping


# Anthropic accepts only these literal TTL values at present.
_VALID_TTLS = frozenset({"5m", "1h"})

# How many *non-system* messages to mark as cache breakpoints (besides system).
# system + last_N = N+1 breakpoints. Anthropic max is 4 → N=3 keeps under limit.
_DEFAULT_TAIL_SIZE = 3


def should_apply_cache_control(client: Any) -> bool:
    """True if the client targets the Anthropic native API (not OpenAI-compat).

    Anthropic prompt caching is only supported on:
        - api.anthropic.com (AnthropicClient native)
        - Anthropic Bedrock proxy (we don't ship this yet)
        - Anthropic Vertex proxy (we don't ship this yet)

    OpenAI-compat clients pointing at Anthropic (e.g. via OpenRouter) currently
    *do* honour cache_control on Anthropic upstreams, but the response shape
    differs. To stay safe, we only apply caching to the native client.

    Detection prefers explicit class membership (AnthropicClient) over hostname
    sniffing to avoid false positives.
    """
    try:
        from .anthropic_client import AnthropicClient as _Anthropic
    except ImportError:
        return False
    if isinstance(client, _Anthropic):
        return True

    # Fallback: explicit opt-in attribute. Must be a real bool — MagicMock
    # returns a truthy mock for any getattr, so we'd otherwise enable caching
    # for every test that uses MagicMock as a client stub.
    flag = getattr(client, "supports_prompt_caching", False)
    return isinstance(flag, bool) and flag is True


def apply_anthropic_cache_control(
    api_messages: list[dict],
    *,
    cache_ttl: str = "5m",
    tail_size: int = _DEFAULT_TAIL_SIZE,
) -> list[dict]:
    """Apply cache_control breakpoints to system + last N non-system messages.

    Returns a *deep copy* of `api_messages` — the input is never mutated. This
    is critical so the persisted conversation history remains free of ephemeral
    cache markers (which would invalidate cache hits on subsequent turns).

    Args:
        api_messages: Standard chat messages list (each dict has `role` and `content`).
        cache_ttl: Either "5m" or "1h". Invalid values fall back to "5m".
        tail_size: How many trailing non-system messages to mark as cache breakpoints.
            Default 3 → system + 3 = 4 breakpoints (Anthropic max).

    Returns:
        New list with cache_control markers applied. Original input is unchanged.

    Behaviour:
        - System message (role=="system") gets a single cache_control breakpoint.
        - The last `tail_size` non-system messages each get a cache_control breakpoint.
        - Messages with string content are upgraded to the structured `content`
          block format: `[{"type": "text", "text": "...", "cache_control": {...}}]`.
        - Messages already using structured content have cache_control added to
          their last content block.
    """
    if not api_messages:
        return []

    ttl = cache_ttl if cache_ttl in _VALID_TTLS else "5m"
    cache_marker = {"type": "ephemeral", "ttl": ttl}

    out = copy.deepcopy(api_messages)

    # Mark the system message (if any). There can be more than one in some flows;
    # mark only the first to preserve the breakpoint budget for tail messages.
    system_marked = False
    for msg in out:
        if msg.get("role") == "system":
            _apply_cache_marker_to_message(msg, cache_marker)
            system_marked = True
            break

    # Mark the last `tail_size` non-system messages.
    non_system_indices = [
        i for i, msg in enumerate(out) if msg.get("role") != "system"
    ]
    tail_indices = non_system_indices[-tail_size:] if tail_size > 0 else []
    for i in tail_indices:
        _apply_cache_marker_to_message(out[i], cache_marker)

    return out


def strip_cache_control(api_messages: list[dict]) -> list[dict]:
    """Remove all cache_control markers — useful for non-Anthropic providers.

    Returns a deep copy with all `cache_control` keys recursively removed.
    Some OpenAI-compat providers (xAI strict mode, Groq) reject unknown keys.
    """
    out = copy.deepcopy(api_messages)
    for msg in out:
        msg.pop("cache_control", None)
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, Mapping):
                    block.pop("cache_control", None)  # type: ignore[attr-defined]
    return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _apply_cache_marker_to_message(msg: dict, marker: dict) -> None:
    """Insert a cache_control marker into a single message (in-place).

    Handles three content shapes:
        1. content: "string"        → upgrade to [{"type":"text","text":"...","cache_control":marker}]
        2. content: [blocks]        → add cache_control to the last block
        3. content: None / missing  → no-op (nothing to cache)
    """
    content = msg.get("content")
    if not content:
        return

    if isinstance(content, str):
        msg["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": dict(marker),
            }
        ]
        return

    if isinstance(content, list) and content:
        # Add cache_control to the last text/content block.
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = dict(marker)
