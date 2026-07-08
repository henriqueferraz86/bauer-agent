"""Tests for `bauer/prompt_caching.py` — Anthropic cache_control injection."""

from __future__ import annotations

from unittest.mock import MagicMock

from bauer.prompt_caching import (
    apply_anthropic_cache_control,
    should_apply_cache_control,
    strip_cache_control,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_cache_marker(msg: dict) -> bool:
    """True if the message has a cache_control marker anywhere in its content."""
    content = msg.get("content")
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("cache_control") is not None for b in content
        )
    return msg.get("cache_control") is not None


# ---------------------------------------------------------------------------
# apply_anthropic_cache_control — happy path
# ---------------------------------------------------------------------------


def test_marks_system_plus_last_3_non_system():
    msgs = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
    ]
    out = apply_anthropic_cache_control(msgs)

    # Total breakpoints = 4 (system + last 3 non-system)
    marked = [m for m in out if _has_cache_marker(m)]
    assert len(marked) == 4

    # Must include the system message
    assert _has_cache_marker(out[0])
    # And the last 3 non-system messages (a1, u2, a2, u3 → last 3 = u2, a2, u3)
    assert _has_cache_marker(out[3])  # u2
    assert _has_cache_marker(out[4])  # a2
    assert _has_cache_marker(out[5])  # u3
    # The earlier non-system messages should NOT be marked
    assert not _has_cache_marker(out[1])  # u1
    assert not _has_cache_marker(out[2])  # a1


def test_does_not_mutate_original():
    """Critical: caching markers must NOT leak into the persisted history."""
    msgs = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello"},
    ]
    apply_anthropic_cache_control(msgs)

    # Original untouched: still strings, no cache_control anywhere
    assert msgs[0]["content"] == "system prompt"
    assert msgs[1]["content"] == "hello"
    assert "cache_control" not in msgs[0]
    assert "cache_control" not in msgs[1]


def test_upgrades_string_content_to_blocks():
    msgs = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ]
    out = apply_anthropic_cache_control(msgs)

    # String content was upgraded to structured blocks
    assert isinstance(out[0]["content"], list)
    assert out[0]["content"][0]["type"] == "text"
    assert out[0]["content"][0]["text"] == "system"
    assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}


def test_preserves_existing_structured_content():
    """Already-structured content keeps existing blocks, marker on the last block."""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "part 1"},
                {"type": "text", "text": "part 2"},
            ],
        },
    ]
    out = apply_anthropic_cache_control(msgs)

    assert len(out[0]["content"]) == 2
    assert "cache_control" not in out[0]["content"][0]
    assert out[0]["content"][1].get("cache_control") == {
        "type": "ephemeral",
        "ttl": "5m",
    }


def test_default_ttl_is_5m():
    msgs = [{"role": "system", "content": "s"}]
    out = apply_anthropic_cache_control(msgs)
    assert out[0]["content"][0]["cache_control"]["ttl"] == "5m"


def test_ttl_1h_accepted():
    msgs = [{"role": "system", "content": "s"}]
    out = apply_anthropic_cache_control(msgs, cache_ttl="1h")
    assert out[0]["content"][0]["cache_control"]["ttl"] == "1h"


def test_invalid_ttl_falls_back_to_5m():
    msgs = [{"role": "system", "content": "s"}]
    out = apply_anthropic_cache_control(msgs, cache_ttl="invalid")
    assert out[0]["content"][0]["cache_control"]["ttl"] == "5m"


def test_tail_size_zero_only_marks_system():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "user", "content": "u2"},
    ]
    out = apply_anthropic_cache_control(msgs, tail_size=0)
    marked = [m for m in out if _has_cache_marker(m)]
    assert len(marked) == 1
    assert _has_cache_marker(out[0])


def test_few_messages_marks_only_what_exists():
    """If fewer messages than tail_size, mark only what's there."""
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
    ]
    out = apply_anthropic_cache_control(msgs)
    marked = [m for m in out if _has_cache_marker(m)]
    assert len(marked) == 2  # system + 1 user (only 1 exists)


def test_empty_list_returns_empty():
    assert apply_anthropic_cache_control([]) == []


def test_only_first_system_marked_when_multiple():
    """Defensive: only the first system message gets a marker."""
    msgs = [
        {"role": "system", "content": "sys1"},
        {"role": "system", "content": "sys2"},
        {"role": "user", "content": "u"},
    ]
    out = apply_anthropic_cache_control(msgs)
    assert _has_cache_marker(out[0])
    assert not _has_cache_marker(out[1])  # only first system


def test_no_marker_when_content_empty():
    """Empty / missing content shouldn't get a marker."""
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": ""},
        {"role": "assistant"},
    ]
    out = apply_anthropic_cache_control(msgs)
    assert _has_cache_marker(out[0])  # system OK
    assert not _has_cache_marker(out[1])  # empty content
    assert not _has_cache_marker(out[2])  # missing content


# ---------------------------------------------------------------------------
# should_apply_cache_control
# ---------------------------------------------------------------------------


def test_should_apply_true_for_anthropic_client():
    from bauer.anthropic_client import AnthropicClient

    client = AnthropicClient(api_key="sk-ant-test", model="claude-3-5-sonnet")
    assert should_apply_cache_control(client) is True


def test_should_apply_false_for_openai_client():
    from bauer.openai_client import OpenAIClient

    client = OpenAIClient(api_key="sk-test", model="gpt-4o-mini")
    assert should_apply_cache_control(client) is False


def test_should_apply_respects_explicit_opt_in():
    """Adapters can opt-in via supports_prompt_caching attribute (real bool only)."""

    class _Adapter:
        supports_prompt_caching = True

    assert should_apply_cache_control(_Adapter()) is True


def test_should_apply_rejects_magicmock_attribute():
    """MagicMock returns truthy mocks for any getattr — must NOT enable caching.

    This is the bug that broke test_agent_tool_result_fed_back in the first
    pass. Callers that pass MagicMock instances as clients (common in tests)
    would otherwise see deep-copied / restructured payloads.
    """
    fake = MagicMock()
    # MagicMock auto-attributes a child mock here, NOT a real bool.
    assert should_apply_cache_control(fake) is False


def test_should_apply_handles_none_attribute():
    fake = MagicMock(spec=[])  # no attributes
    assert should_apply_cache_control(fake) is False


# ---------------------------------------------------------------------------
# strip_cache_control
# ---------------------------------------------------------------------------


def test_strip_removes_markers_from_top_level():
    msgs = [
        {"role": "user", "content": "hi", "cache_control": {"type": "ephemeral"}},
    ]
    out = strip_cache_control(msgs)
    assert "cache_control" not in out[0]


def test_strip_removes_markers_from_content_blocks():
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}},
            ],
        },
    ]
    out = strip_cache_control(msgs)
    assert "cache_control" not in out[0]["content"][0]


def test_strip_does_not_mutate_input():
    msgs = [
        {"role": "user", "content": "hi", "cache_control": {"type": "ephemeral"}},
    ]
    strip_cache_control(msgs)
    assert "cache_control" in msgs[0]


def test_strip_handles_empty_list():
    assert strip_cache_control([]) == []
