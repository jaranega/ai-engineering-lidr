"""Unit tests for the `llm-provider` requirement "Coste estimado" (app/services/pricing.py)."""

import pytest

from app.services.pricing import estimate_cost


def test_known_model_price():
    """Requirement: Coste estimado / Scenario: Modelo con precio conocido."""
    assert estimate_cost("gpt-4o-mini", 1000, 500) == 0.00045


@pytest.mark.parametrize(
    ("model", "expected"),
    [("claude-haiku-4-5", 0.0035), ("gpt-4o-mini-2024-07-18", 0.00045)],
)
def test_known_model_prices_for_aliases(model, expected):
    """Requirement: Coste estimado / Scenario: Modelo con precio conocido."""
    assert estimate_cost(model, 1000, 500) == expected


def test_cost_rounded_to_six_decimals():
    """Requirement: Coste estimado / Scenario: Modelo con precio conocido (redondeo)."""
    # 1 * 0.15 / 1e6 = 1.5e-7 -> rounds to 0.0 at 6 decimals; 7 tokens -> 1.05e-6 -> 1e-6
    assert estimate_cost("gpt-4o-mini", 7, 0) == 0.000001


def test_pinned_snapshot_uses_same_price_as_alias():
    """Requirement: Coste estimado / Scenario: Snapshot fijado por configuración."""
    snapshot = estimate_cost("claude-haiku-4-5-20251001", 1000, 500)
    assert snapshot == 0.0035
    assert snapshot == estimate_cost("claude-haiku-4-5", 1000, 500)


def test_unknown_model_returns_none():
    """Requirement: Coste estimado / Scenario: Modelo desconocido."""
    assert estimate_cost("some-future-model", 1000, 500) is None
