"""Model pricing table and cost estimation.

Prices are USD per million tokens (input, output), standard tier, without prompt caching
or batch discounts. Lookup is exact on the requested model id: aliases and known
snapshots are listed explicitly, so a price is never borrowed from a different model.
"""

# Verified 2026-10-21:
# - OpenAI: https://developers.openai.com/api/docs/pricing (gpt-4o-mini, Standard tier) and
#   https://developers.openai.com/api/docs/models/gpt-4o-mini (default snapshot
#   gpt-4o-mini-2024-07-18, same $0.15 / $0.60).
# - Anthropic: https://platform.claude.com/docs/en/about-claude/pricing and
#   https://platform.claude.com/docs/en/models/haiku-4-5/overview (alias claude-haiku-4-5
#   resolves to claude-haiku-4-5-20251001; $1 / $5).
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o-mini-2024-07-18": (0.15, 0.60),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated USD cost of a call, rounded to 6 decimals; `None` if the model is unknown."""
    prices = MODEL_PRICES.get(model)
    if prices is None:
        return None
    price_in, price_out = prices
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)
