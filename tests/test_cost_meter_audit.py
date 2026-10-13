"""K05: usage nativo chega ao custo/sink com contrato canônico consistente."""

import pytest

from bauer.account_usage import normalize_usage
from bauer.cost_meter import cost_sink, report_llm_cost
from bauer.usage_pricing import estimate_cost_usd


@pytest.mark.parametrize("cache_read, cache_create", [(0, 0), (800, 0), (0, 200), (800, 200)])
def test_anthropic_native_usage_converges_with_canonical(cache_read, cache_create):
    raw = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_create,
    }
    original = dict(raw)
    canonical = {
        "prompt_tokens": 1000 + cache_read + cache_create,
        "completion_tokens": 500,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_create,
    }
    expected = (3000 + 7500 + cache_read * 0.3 + cache_create * 3.75) / 1_000_000
    received = []
    token = cost_sink.set(lambda p, m, u, c: received.append((p, m, u, c)))
    try:
        cost = report_llm_cost("anthropic", "claude-3-5-sonnet-latest", raw)
    finally:
        cost_sink.reset(token)

    assert cost == pytest.approx(expected)
    assert cost == pytest.approx(report_llm_cost("anthropic", "claude-3-5-sonnet-latest", canonical))
    assert received[0][2]["prompt_tokens"] == canonical["prompt_tokens"]
    assert received[0][2]["completion_tokens"] == 500
    assert received[0][2]["total_tokens"] == canonical["prompt_tokens"] + 500
    assert received[0][3] == cost
    assert raw == original


def test_normalization_of_native_cache_is_idempotent():
    raw = {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 80}
    canonical = normalize_usage(raw)
    assert canonical["prompt_tokens"] == 180
    assert canonical["total_tokens"] == 200
    assert normalize_usage(canonical) == canonical


def test_null_canonical_field_does_not_hide_native_cache():
    raw = {"prompt_tokens": None, "input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 80}
    assert normalize_usage(raw)["prompt_tokens"] == 180
    assert normalize_usage(raw)["total_tokens"] == 200


def test_camelcase_native_cache_is_normalized():
    raw = {"inputTokens": 100, "outputTokens": 20, "cacheReadInputTokens": 80, "cacheCreationInputTokens": 10}
    canonical = normalize_usage(raw)
    assert canonical["prompt_tokens"] == 190
    assert canonical["total_tokens"] == 210
    assert canonical["cache_read_input_tokens"] == 80


def test_native_client_usage_reaches_real_autonomous_budget():
    from bauer.agent_native_execution import report_native_cost
    from bauer.anthropic_client import AnthropicClient
    from bauer.autonomous_budget import AutonomousBudget

    client = AnthropicClient.__new__(AnthropicClient)
    client.last_usage = {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 800}
    budget = AutonomousBudget(max_cost_usd=1.0)

    def consume(provider, model, usage, cost):
        assert provider == "anthropic"
        budget.consume_llm_call(cost_usd=cost, output_tokens=usage["completion_tokens"])

    token = cost_sink.set(consume)
    try:
        report_native_cost(client, "claude-3-5-sonnet-latest")
    finally:
        cost_sink.reset(token)
    snapshot = budget.snapshot()
    assert snapshot.cost_usd == pytest.approx(0.01074)
    assert snapshot.output_tokens == 500
    assert snapshot.llm_calls == 1


def test_reported_cost_survives_usage_normalization():
    received = []
    raw = {"input_tokens": 1000, "output_tokens": 500, "cost": 0.123, "extra": "preserved"}
    token = cost_sink.set(lambda p, m, u, c: received.append((u, c)))
    try:
        assert report_llm_cost("anthropic", "claude-3-5-sonnet-latest", raw) == 0.123
    finally:
        cost_sink.reset(token)
    assert received[0][0]["cost"] == 0.123
    assert received[0][0]["extra"] == "preserved"
    assert received[0][0]["prompt_tokens"] == 1000


@pytest.mark.parametrize("provider", ["ollama", "lmstudio"])
def test_local_provider_remains_free_even_with_reported_cost(provider):
    usage = {"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.123}
    assert estimate_cost_usd(provider, "local-model", usage) == 0.0
    assert report_llm_cost(provider, "local-model", usage) == 0.0
