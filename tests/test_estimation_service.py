"""Unit tests for EstimationService (use case over the `llm-provider` interface)."""

import pytest

from app.services.llm_service import (
    EstimationResult,
    EstimationService,
    LLMProviderError,
    build_estimation_prompt,
)


def test_estimate_returns_result_with_cost(fake_provider_cls):
    """Requirement: Coste estimado / Scenario: Modelo con precio conocido (vía servicio)."""
    provider = fake_provider_cls(
        text="out", model="gpt-4o-mini", input_tokens=1000, output_tokens=500
    )
    result = EstimationService(provider).estimate("transcript")
    assert result == EstimationResult(
        text="out",
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.00045,
    )


def test_estimate_unknown_model_cost_is_none(fake_provider_cls):
    """Requirement: Coste estimado / Scenario: Modelo desconocido (estimación normal)."""
    provider = fake_provider_cls(model="mystery-model")
    result = EstimationService(provider).estimate("transcript")
    assert result.cost_usd is None
    assert result.text == provider.result.text


def test_estimate_passes_cag_prompt_to_provider(fake_provider):
    """Requirement: Interfaz mínima de proveedor (system + user)."""
    provider = fake_provider
    EstimationService(provider).estimate("my transcript")
    assert provider.calls == [build_estimation_prompt("my transcript")]
    assert provider.call_count == 1


def test_estimate_propagates_provider_error(fake_provider_cls):
    """Requirement: Errores del proveedor / Scenario: Proveedor no disponible."""
    provider = fake_provider_cls(error=LLMProviderError(provider="openai"))
    with pytest.raises(LLMProviderError):
        EstimationService(provider).estimate("transcript")
