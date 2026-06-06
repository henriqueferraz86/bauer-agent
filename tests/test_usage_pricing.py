"""Tests for `bauer/usage_pricing.py`."""

from __future__ import annotations

import pytest

from bauer.usage_pricing import estimate_cost_usd, format_cost, get_price


# ---------------------------------------------------------------------------
# get_price — table lookup
# ---------------------------------------------------------------------------


def test_get_price_known_model():
    inp, out = get_price("openai", "gpt-4o-mini")
    assert inp == 0.15 and out == 0.60


def test_get_price_anthropic():
    inp, out = get_price("anthropic", "claude-3-5-sonnet-latest")
    assert inp == 3.00 and out == 15.00


def test_get_price_case_insensitive_provider():
    inp_lower, _ = get_price("openai", "gpt-4o-mini")
    inp_upper, _ = get_price("OPENAI", "gpt-4o-mini")
    assert inp_lower == inp_upper


def test_get_price_unknown_falls_back():
    inp, out = get_price("nonexistent", "made-up-model")
    assert inp > 0 and out > 0  # not zero — uses fallback


# ---------------------------------------------------------------------------
# estimate_cost_usd
# ---------------------------------------------------------------------------


def test_cost_openai_simple():
    """gpt-4o-mini: $0.15 in / $0.60 out per 1M."""
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    cost = estimate_cost_usd("openai", "gpt-4o-mini", usage)
    assert cost == pytest.approx(0.15 + 0.60)


def test_cost_anthropic_no_cache():
    """claude-3-5-sonnet: $3 in / $15 out per 1M."""
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    cost = estimate_cost_usd("anthropic", "claude-3-5-sonnet-latest", usage)
    assert cost == pytest.approx(3.0 + 15.0)


def test_cost_anthropic_with_cache_read():
    """Cache reads are 10% of input price.

    100k prompt, 80k from cache_read, 20k regular, 0 output:
        cost = 80k × 3.00 × 0.10 / 1M + 20k × 3.00 / 1M
             = 0.024 + 0.060 = 0.084
    """
    usage = {
        "prompt_tokens": 100_000,
        "completion_tokens": 0,
        "cache_read_input_tokens": 80_000,
    }
    cost = estimate_cost_usd("anthropic", "claude-3-5-sonnet-latest", usage)
    assert cost == pytest.approx(0.024 + 0.060)


def test_cost_anthropic_with_cache_creation():
    """Cache creation is 1.25× input price.

    100k prompt, 50k cache_create, 50k regular, 0 output:
        cost = 50k × 3.00 × 1.25 / 1M + 50k × 3.00 / 1M
             = 0.1875 + 0.150 = 0.3375
    """
    usage = {
        "prompt_tokens": 100_000,
        "completion_tokens": 0,
        "cache_creation_input_tokens": 50_000,
    }
    cost = estimate_cost_usd("anthropic", "claude-3-5-sonnet-latest", usage)
    assert cost == pytest.approx(0.1875 + 0.150)


def test_cost_anthropic_cache_keys_ignored_for_openai():
    """Non-Anthropic providers ignore cache_* fields."""
    usage = {
        "prompt_tokens": 1_000_000,
        "completion_tokens": 0,
        "cache_read_input_tokens": 800_000,
    }
    cost_openai = estimate_cost_usd("openai", "gpt-4o-mini", usage)
    # Should bill full 1M at $0.15 → 0.15
    assert cost_openai == pytest.approx(0.15)


def test_cost_zero_usage():
    cost = estimate_cost_usd("openai", "gpt-4o-mini", {})
    assert cost == 0.0


def test_cost_negative_usage_clamps_to_zero():
    """Bogus negatives shouldn't produce negative cost."""
    usage = {"prompt_tokens": -100, "completion_tokens": 0}
    cost = estimate_cost_usd("openai", "gpt-4o-mini", usage)
    assert cost == 0.0


def test_cost_realistic_chat_turn():
    """Sanity-check: a small turn (1500 in, 300 out) on gpt-4o-mini ≈ $0.00041."""
    usage = {"prompt_tokens": 1500, "completion_tokens": 300}
    cost = estimate_cost_usd("openai", "gpt-4o-mini", usage)
    assert 0.0002 < cost < 0.001


def test_cost_unknown_model_uses_fallback():
    """Unknown models still produce a non-zero estimate."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    cost = estimate_cost_usd("openai", "totally-made-up-gpt-99", usage)
    assert cost > 0


# ---------------------------------------------------------------------------
# format_cost
# ---------------------------------------------------------------------------


def test_format_cost_large():
    # Use a value that's unambiguous under banker's rounding.
    assert format_cost(12.50) == "$12.50"
    assert format_cost(99.999) == "$100.00"


def test_format_cost_one_dollar():
    assert format_cost(1.5) == "$1.50"


def test_format_cost_medium():
    """≥ $0.01 → 4 decimals."""
    assert format_cost(0.0123) == "$0.0123"


def test_format_cost_small():
    """≥ $0.0001 → 6 decimals."""
    assert format_cost(0.000234) == "$0.000234"


def test_format_cost_micro():
    """< $0.0001 → lower-bound notation."""
    assert format_cost(0.00000005) == "<$0.000001"


def test_format_cost_zero():
    assert format_cost(0) == "$0"


def test_format_cost_negative():
    """Defensive: negatives normalised to $0."""
    assert format_cost(-1.0) == "$0"
