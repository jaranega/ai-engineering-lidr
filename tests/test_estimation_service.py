"""Unit tests for EstimationService (use case over the `llm-provider` interface)."""

import pytest

from app.services.llm_service import (
    ChatMessage,
    EstimationResult,
    EstimationService,
    LLMProviderError,
    _chat_system_prompt,
    _system_prompt,
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
    assert provider.calls[0][1] == [{"role": "user", "content": "my transcript"}]
    assert provider.call_count == 1


def test_estimate_propagates_provider_error(fake_provider_cls):
    """Requirement: Errores del proveedor / Scenario: Proveedor no disponible."""
    provider = fake_provider_cls(error=LLMProviderError(provider="openai"))
    with pytest.raises(LLMProviderError):
        EstimationService(provider).estimate("transcript")


# --- stream_chat (spec: llm-provider "Generación en streaming", cag-prompt "Prompt del chat") ---

CHAT: list[ChatMessage] = [
    {"role": "user", "content": "T"},
    {"role": "assistant", "content": "E"},
    {"role": "user", "content": "A"},
]


def test_stream_chat_multi_turn_uses_conversation_prompt_and_unmodified_messages(
    fake_provider_cls,
):
    """Requirement: System prompt del chat / multi-turno: prompt con sección de conversación."""
    provider = fake_provider_cls(fragments=["a", "b"])
    stream = EstimationService(provider).stream_chat(CHAT)
    assert list(stream) == ["a", "b"]
    system, messages = provider.calls[0]
    assert system == _chat_system_prompt()
    assert messages == CHAT
    assert provider.call_count == 1


def test_stream_chat_first_turn_uses_endpoint_prompt(fake_provider_cls):
    """Requirement: System prompt del chat / Scenario: Primer turno con el prompt del endpoint."""
    provider = fake_provider_cls(fragments=["a"])
    list(EstimationService(provider).stream_chat(CHAT[:1]))
    system, messages = provider.calls[0]
    assert system == _system_prompt()
    assert messages == CHAT[:1]


def test_stream_chat_result_includes_cost(fake_provider_cls):
    """Requirement: Coste estimado / Scenario: Modelo con precio conocido (vía stream_chat)."""
    provider = fake_provider_cls(
        fragments=["out", "put"], model="gpt-4o-mini", input_tokens=1000, output_tokens=500
    )
    stream = EstimationService(provider).stream_chat(CHAT)
    list(stream)
    assert stream.result == EstimationResult(
        text="output",
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.00045,
    )


def test_stream_chat_unknown_model_cost_is_none(fake_provider_cls):
    """Requirement: Coste estimado / Scenario: Modelo desconocido (vía stream_chat)."""
    provider = fake_provider_cls(model="mystery-model")
    stream = EstimationService(provider).stream_chat(CHAT)
    list(stream)
    assert stream.result.cost_usd is None


def test_stream_chat_result_before_consumption_raises(fake_provider):
    """Requirement: Generación en streaming / Scenario: Resultado antes de terminar (servicio)."""
    stream = EstimationService(fake_provider).stream_chat(CHAT)
    with pytest.raises(RuntimeError):
        stream.result  # noqa: B018


def test_stream_chat_propagates_provider_error_on_iteration(fake_provider_cls):
    """Requirement: Errores del proveedor / Scenario: Error durante el streaming (servicio)."""
    provider = fake_provider_cls(
        fragments=["a", "b"], error=LLMProviderError(provider="openai"), error_after=1
    )
    stream = EstimationService(provider).stream_chat(CHAT)
    received = []
    with pytest.raises(LLMProviderError):
        for fragment in stream:
            received.append(fragment)
    assert received == ["a"]


def test_estimate_sends_a_single_user_message(fake_provider):
    """Requirement: Patrón de mensajes / Scenario: Separación de roles (estimate)."""
    EstimationService(fake_provider).estimate("T")
    system, messages = fake_provider.calls[0]
    assert messages == [{"role": "user", "content": "T"}]
    assert system == build_estimation_prompt("T")[0]
    assert system != _chat_system_prompt()
