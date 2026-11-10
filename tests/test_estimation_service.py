"""Unit tests for EstimationService (use case over the `llm-provider` interface)."""

from dataclasses import replace

import pytest

from app.prompts.loader import render_estimation_prompt
from app.services.llm_service import (
    EstimationResult,
    EstimationService,
    GenerationOptions,
    LLMProviderError,
)
from tests.conftest import make_input


def test_estimate_returns_result_with_cost(fake_provider_cls):
    """Requirement: Coste estimado / Scenario: Modelo con precio conocido (vía servicio)."""
    provider = fake_provider_cls(
        text="out", model="gpt-4o-mini", input_tokens=1000, output_tokens=500
    )
    result = EstimationService(provider).estimate(make_input())
    # Validation is on by default, so the result also carries the structural check.
    assert result.validation is not None
    assert replace(result, latency_ms=0, validation=None) == EstimationResult(
        text="out",
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.00045,
        prompt_version="v1",
    )


def test_estimate_unknown_model_cost_is_none(fake_provider_cls):
    """Requirement: Coste estimado / Scenario: Modelo desconocido (estimación normal)."""
    provider = fake_provider_cls(model="mystery-model")
    result = EstimationService(provider).estimate(make_input())
    assert result.cost_usd is None
    assert result.text == provider.result.text


def test_estimate_sends_system_and_a_single_user_message(fake_provider):
    """Requirement: Patrón de mensajes system / user / Scenario: Separación de roles."""
    request = make_input(description="Descripción de prueba única para el servicio")
    EstimationService(fake_provider).estimate(request)
    system, user = render_estimation_prompt(request, "v1")
    assert fake_provider.calls == [(system, [{"role": "user", "content": user}])]
    assert "Descripción de prueba única" not in system
    assert fake_provider.call_count == 1


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_estimate_reports_the_rendered_prompt_version(fake_provider, version):
    """Requirement: Endpoint de estimación / Scenario: Versión del prompt (servicio)."""
    result = EstimationService(fake_provider).estimate(make_input(), GenerationOptions(version))
    assert result.prompt_version == version
    assert fake_provider.calls[0][0] == render_estimation_prompt(make_input(), version)[0]


def test_estimate_propagates_provider_error(fake_provider_cls):
    """Requirement: Errores del proveedor / Scenario: Proveedor no disponible."""
    provider = fake_provider_cls(error=LLMProviderError(provider="openai"))
    with pytest.raises(LLMProviderError):
        EstimationService(provider).estimate(make_input())
