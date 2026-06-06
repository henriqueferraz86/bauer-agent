"""Per-token pricing for LLM providers — converts usage dicts to USD cost.

Prices are USD per 1M tokens (input/output) and include cache discounts when
the provider supports them (Anthropic prompt caching: cache_read = 10% of
input price; cache_creation = 125% of input price).

Updated 2026-06. Prices change frequently — keep this table current via
`bauer/usage_pricing.py` updates. For models not in the table, falls back
to a conservative midrange estimate.

Usage::

    from bauer.usage_pricing import estimate_cost_usd

    usage = {"prompt_tokens": 1500, "completion_tokens": 300,
             "cache_read_input_tokens": 1000}
    cost = estimate_cost_usd("anthropic", "claude-3-5-sonnet-20241022", usage)
    # → ~0.00505 USD
"""

from __future__ import annotations

from typing import Mapping


# Prices in USD per 1,000,000 tokens.
# Format: (input_per_1M, output_per_1M). Cache pricing derived as needed.
# Sources: provider pricing pages as of 2026-06.
_PRICING: dict[tuple[str, str], tuple[float, float]] = {
    # OpenAI
    ("openai", "gpt-4o-mini"):        (0.15,   0.60),
    ("openai", "gpt-4o"):             (2.50,  10.00),
    ("openai", "gpt-4.1-mini"):       (0.40,   1.60),
    ("openai", "gpt-4.1"):            (2.00,   8.00),
    ("openai", "gpt-4.5"):            (75.00, 150.00),
    ("openai", "gpt-4-turbo"):        (10.00, 30.00),
    ("openai", "gpt-5"):              (5.00,  20.00),
    ("openai", "gpt-5-mini"):         (0.50,   2.00),
    ("openai", "o3"):                 (20.00, 80.00),
    ("openai", "o3-mini"):            (1.10,   4.40),
    ("openai", "o4-mini"):            (1.10,   4.40),
    ("openai", "o1"):                 (15.00, 60.00),

    # Anthropic
    ("anthropic", "claude-3-5-sonnet-20241022"): (3.00, 15.00),
    ("anthropic", "claude-3-5-sonnet-latest"):   (3.00, 15.00),
    ("anthropic", "claude-3-5-haiku-20241022"):  (0.80,  4.00),
    ("anthropic", "claude-3-7-sonnet-20250219"): (3.00, 15.00),
    ("anthropic", "claude-opus-4-20250514"):     (15.00, 75.00),
    ("anthropic", "claude-sonnet-4-5"):          (3.00, 15.00),

    # Groq (free tier has rate limits but tokens are charged at these rates on paid)
    ("groq", "llama-3.3-70b-versatile"): (0.59, 0.79),
    ("groq", "llama-3.1-8b-instant"):    (0.05, 0.08),
    ("groq", "mixtral-8x7b-32768"):      (0.24, 0.24),

    # Google Gemini
    ("gemini", "gemini-2.0-flash-exp"): (0.075, 0.30),
    ("gemini", "gemini-1.5-pro"):       (1.25,  5.00),
    ("gemini", "gemini-1.5-flash"):     (0.075, 0.30),

    # DeepSeek
    ("deepseek", "deepseek-chat"):    (0.27, 1.10),
    ("deepseek", "deepseek-reasoner"): (0.55, 2.19),

    # xAI Grok
    ("xai", "grok-2-latest"): (2.00, 10.00),
    ("xai", "grok-beta"):     (5.00, 15.00),

    # Mistral
    ("mistral", "mistral-large-latest"): (2.00, 6.00),
    ("mistral", "mistral-small-latest"): (0.20, 0.60),
    ("mistral", "codestral-latest"):     (0.20, 0.60),

    # Together
    ("together", "meta-llama/Llama-3.3-70B-Instruct-Turbo"): (0.88, 0.88),
    ("together", "Qwen/Qwen2.5-Coder-32B-Instruct"):         (0.80, 0.80),
}

# Conservative midrange fallback for unknown models. Keeps cost estimates non-zero
# so users still see *some* indication, even if imprecise.
_FALLBACK_INPUT_PER_1M = 1.00
_FALLBACK_OUTPUT_PER_1M = 4.00

# Anthropic cache pricing: read = 0.10× input, creation = 1.25× input.
_ANTHROPIC_CACHE_READ_RATIO = 0.10
_ANTHROPIC_CACHE_CREATE_RATIO = 1.25


def get_price(provider: str, model: str) -> tuple[float, float]:
    """Return (input_per_1M, output_per_1M) USD for a provider/model pair.

    Falls back to conservative defaults if the pair is not in the table.
    Model name matching is case-sensitive and exact (not prefix).
    """
    return _PRICING.get(
        (provider.lower(), model),
        (_FALLBACK_INPUT_PER_1M, _FALLBACK_OUTPUT_PER_1M),
    )


def estimate_cost_usd(
    provider: str,
    model: str,
    usage: Mapping[str, int],
) -> float:
    """Estimate USD cost from a usage dict.

    Args:
        provider: Provider name (matched against `_PRICING` keys, case-insensitive).
        model: Model identifier (exact match required).
        usage: Dict with standard keys — `prompt_tokens`, `completion_tokens`,
            optionally `cache_read_input_tokens` and `cache_creation_input_tokens`
            (Anthropic prompt caching).

    Returns:
        Total cost in USD (float, never negative). Missing usage keys default to 0.

    Cache pricing logic (Anthropic only):
        - cache_read_input_tokens × input_price × 0.10  (cached reads are cheap)
        - cache_creation_input_tokens × input_price × 1.25  (creating cache costs more)
        - Remaining prompt_tokens (= prompt_tokens - cache_read - cache_creation)
          billed at full input rate.

    For non-Anthropic providers, cache keys are silently ignored.
    """
    input_per_1m, output_per_1m = get_price(provider, model)

    prompt = max(0, int(usage.get("prompt_tokens", 0) or 0))
    completion = max(0, int(usage.get("completion_tokens", 0) or 0))
    cache_read = max(0, int(usage.get("cache_read_input_tokens", 0) or 0))
    cache_create = max(0, int(usage.get("cache_creation_input_tokens", 0) or 0))

    is_anthropic = provider.lower() == "anthropic"

    if is_anthropic and (cache_read or cache_create):
        # Apply cache pricing — subtract cache portions from full-price prompt
        regular_prompt = max(0, prompt - cache_read - cache_create)
        cost = (
            regular_prompt * input_per_1m
            + cache_read * input_per_1m * _ANTHROPIC_CACHE_READ_RATIO
            + cache_create * input_per_1m * _ANTHROPIC_CACHE_CREATE_RATIO
            + completion * output_per_1m
        ) / 1_000_000.0
    else:
        cost = (prompt * input_per_1m + completion * output_per_1m) / 1_000_000.0

    return max(0.0, cost)


def format_cost(cost_usd: float) -> str:
    """Render a USD cost as a short human string.

    Strategy:
        - >= $1.00     → "$1.23"        (2 decimals)
        - >= $0.01     → "$0.0123"      (4 decimals)
        - >= $0.0001   → "$0.000234"    (6 decimals)
        - smaller      → "<$0.000001"   (lower bound)
    """
    if cost_usd < 0:
        return "$0"
    if cost_usd >= 1.0:
        return f"${cost_usd:.2f}"
    if cost_usd >= 0.01:
        return f"${cost_usd:.4f}"
    if cost_usd >= 0.0001:
        return f"${cost_usd:.6f}"
    if cost_usd > 0:
        return "<$0.000001"
    return "$0"
