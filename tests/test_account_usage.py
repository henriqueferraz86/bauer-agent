"""Tests for `bauer/account_usage.py` — provider-agnostic usage parsing."""

from __future__ import annotations

import pytest

from bauer.account_usage import (
    cache_hit_ratio,
    merge_usage,
    normalize_usage,
    usage_is_empty,
)


# ---------------------------------------------------------------------------
# normalize_usage — provider variants
# ---------------------------------------------------------------------------


def test_normalize_openai_shape():
    """OpenAI / Groq / Mistral / Together / xAI all use this shape."""
    raw = {
        "prompt_tokens": 150,
        "completion_tokens": 30,
        "total_tokens": 180,
    }
    out = normalize_usage(raw)
    assert out["prompt_tokens"] == 150
    assert out["completion_tokens"] == 30
    assert out["total_tokens"] == 180
    assert out["cache_read_input_tokens"] == 0
    assert out["cache_creation_input_tokens"] == 0


def test_normalize_anthropic_shape():
    """Anthropic uses input/output_tokens and cache_* fields."""
    raw = {
        "input_tokens": 200,
        "output_tokens": 50,
        "cache_read_input_tokens": 180,
        "cache_creation_input_tokens": 20,
    }
    out = normalize_usage(raw)
    assert out["prompt_tokens"] == 200
    assert out["completion_tokens"] == 50
    assert out["total_tokens"] == 250
    assert out["cache_read_input_tokens"] == 180
    assert out["cache_creation_input_tokens"] == 20


def test_normalize_openai_with_cached_tokens_details():
    """OpenAI nests cached_tokens under prompt_tokens_details."""
    raw = {
        "prompt_tokens": 500,
        "completion_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 400},
    }
    out = normalize_usage(raw)
    assert out["prompt_tokens"] == 500
    assert out["cache_read_input_tokens"] == 400


def test_normalize_camelcase_keys():
    """Some SDKs return camelCase. Parser tolerates both."""
    raw = {
        "promptTokens": 100,
        "completionTokens": 50,
        "totalTokens": 150,
    }
    out = normalize_usage(raw)
    assert out["prompt_tokens"] == 100
    assert out["completion_tokens"] == 50


def test_normalize_none_returns_zeros():
    out = normalize_usage(None)
    assert all(v == 0 for v in out.values())


def test_normalize_empty_dict_returns_zeros():
    out = normalize_usage({})
    assert all(v == 0 for v in out.values())


def test_normalize_non_mapping_returns_zeros():
    out = normalize_usage(42)
    assert all(v == 0 for v in out.values())
    out = normalize_usage("not a dict")
    assert all(v == 0 for v in out.values())


def test_normalize_recomputes_total_when_inconsistent():
    """Provider's total_tokens < derived → trust the derived sum."""
    raw = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 0}
    out = normalize_usage(raw)
    assert out["total_tokens"] == 150


def test_normalize_handles_negative_values():
    """Bogus negatives clamp to zero (defensive)."""
    raw = {"prompt_tokens": -5, "completion_tokens": 20}
    out = normalize_usage(raw)
    assert out["prompt_tokens"] == 0
    assert out["completion_tokens"] == 20


def test_normalize_handles_floats():
    """Some providers return floats — coerce to int."""
    raw = {"prompt_tokens": 100.7, "completion_tokens": 50.2}
    out = normalize_usage(raw)
    assert out["prompt_tokens"] == 100
    assert out["completion_tokens"] == 50


def test_normalize_handles_string_numbers():
    raw = {"prompt_tokens": "100", "completion_tokens": "50"}
    out = normalize_usage(raw)
    assert out["prompt_tokens"] == 100
    assert out["completion_tokens"] == 50


def test_normalize_handles_string_garbage():
    """Non-numeric strings default to 0."""
    raw = {"prompt_tokens": "lots", "completion_tokens": "many"}
    out = normalize_usage(raw)
    assert out["prompt_tokens"] == 0
    assert out["completion_tokens"] == 0


# ---------------------------------------------------------------------------
# merge_usage — session accumulation
# ---------------------------------------------------------------------------


def test_merge_sums_simple():
    a = normalize_usage({"prompt_tokens": 100, "completion_tokens": 20})
    b = normalize_usage({"prompt_tokens": 50, "completion_tokens": 30})
    total = merge_usage(a, b)
    assert total["prompt_tokens"] == 150
    assert total["completion_tokens"] == 50
    assert total["total_tokens"] == 200


def test_merge_with_anthropic_cache():
    a = normalize_usage(
        {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 80}
    )
    b = normalize_usage(
        {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 90}
    )
    total = merge_usage(a, b)
    assert total["cache_read_input_tokens"] == 170


def test_merge_zero_args_returns_zeros():
    total = merge_usage()
    assert all(v == 0 for v in total.values())


def test_merge_one_arg_returns_normalised_copy():
    a = normalize_usage({"prompt_tokens": 100})
    total = merge_usage(a)
    assert total == a


def test_merge_ignores_non_mappings():
    a = normalize_usage({"prompt_tokens": 100})
    total = merge_usage(a, None, 42, a)
    assert total["prompt_tokens"] == 200  # only the two valid `a`s


# ---------------------------------------------------------------------------
# usage_is_empty
# ---------------------------------------------------------------------------


def test_usage_is_empty_true_for_zero_dict():
    assert usage_is_empty(normalize_usage(None)) is True
    assert usage_is_empty({"prompt_tokens": 0, "completion_tokens": 0}) is True


def test_usage_is_empty_false_for_real_usage():
    assert usage_is_empty(normalize_usage({"prompt_tokens": 1})) is False


# ---------------------------------------------------------------------------
# cache_hit_ratio
# ---------------------------------------------------------------------------


def test_cache_hit_ratio_full_hit():
    u = normalize_usage({"input_tokens": 100, "cache_read_input_tokens": 100})
    assert cache_hit_ratio(u) == 1.0


def test_cache_hit_ratio_partial():
    u = normalize_usage({"input_tokens": 100, "cache_read_input_tokens": 80})
    assert cache_hit_ratio(u) == pytest.approx(0.8)


def test_cache_hit_ratio_no_prompt_tokens():
    assert cache_hit_ratio(normalize_usage({})) == 0.0


def test_cache_hit_ratio_no_cache():
    u = normalize_usage({"prompt_tokens": 100})
    assert cache_hit_ratio(u) == 0.0


def test_cache_hit_ratio_capped_at_one():
    """Defensive: if cache > prompt (shouldn't happen, but...), cap at 1.0."""
    u = {"prompt_tokens": 100, "cache_read_input_tokens": 150}
    assert cache_hit_ratio(u) == 1.0
